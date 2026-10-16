"""
Corpus driver: decides which filings go in, fetches them, dispatches to the
splitters, writes the index.

Writes to its own folder, wiped and rebuilt each run (Phase 2 makes this
incremental). Only .md files inside the corpus folder are ever deleted.
"""

import time
from datetime import date

from ..config import CORPUS_FOLDER_NAME, CORPUS_LIMITS
from .. import edgar
from .html import html_to_markdown
from .split_10k import process_10k
from .split_10q import process_10q
from .split_proxy import process_proxy
from .release import process_earnings_release
from .financials import build_financials_timeseries
from .index import write_index


def select_filings(filings, limit):
    """
    Dedupes by fiscal period (an amended 10-K shares its period with the
    original; the LAST filed wins) and caps the count.
    """
    by_period = {}
    for filing in filings:
        period = filing.get("report_date") or filing.get("filing_date")
        existing = by_period.get(period)
        if existing is None or filing.get("filing_date", "") > existing.get("filing_date", ""):
            by_period[period] = filing
    unique = sorted(by_period.values(),
                    key=lambda f: f.get("report_date") or f.get("filing_date"), reverse=True)
    return unique[:limit]


def process_filing(html, meta, out_dir, skip_items=(), include_notes=True):
    """Routes one filing's HTML to the right splitter by form."""
    text = html_to_markdown(html)
    form = meta["form"]
    if form == "DEF 14A":
        return process_proxy(text, meta, out_dir)
    if form == "10-Q":
        return process_10q(text, meta, out_dir, include_notes)
    return process_10k(text, meta, out_dir, skip_items, include_notes)


def _clear_corpus_folder(out_dir):
    if out_dir.exists():
        for existing in out_dir.iterdir():
            if existing.is_file() and existing.suffix.lower() == ".md":
                existing.unlink()
            else:
                print(f"  NOTE: leaving non-Markdown file in place: {existing.name}")
    out_dir.mkdir(parents=True, exist_ok=True)


def _add_financials(cik, ticker, company_name, out_dir, written):
    print("\n  Building financial statement time series from SEC XBRL...", end=" ")
    financials = build_financials_timeseries(cik, ticker, company_name)
    if not financials:
        print("Skipped (no XBRL data).")
        return
    path = out_dir / f"{ticker}_00_Financials_Timeseries.md"
    path.write_text(financials, encoding="utf-8")
    written.append({
        "filename": path.name, "chars": len(financials),
        "section": "Derived financial statements", "form": "XBRL",
        "period_end": "multi-period",
        "source": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
        "accession": "",
    })
    print("Done!")


def _add_filings(cik, ticker, company_name, out_dir, forms, start_year, end_year, written, limits_used):
    # Period end of the newest 10-K. Stays "" if there isn't one, so every
    # 10-Q then keeps its notes (any date string sorts after "").
    newest_10k_period = ""
    for form in forms:
        limit = CORPUS_LIMITS[form]
        print(f"\n  Finding {form} filings...")
        found = edgar.list_filings(cik, form, start_year, end_year)
        if not found and form == "10-K":
            # A corpus missing its annual report is not worth shipping just
            # because the years were off.
            print("    None in range; widening search to the last 5 years...")
            found = edgar.list_filings(cik, form, end_year - 5, end_year)

        selected = select_filings(found, limit)
        if not selected:
            print(f"    No {form} found.")
            limits_used[form] = 0
            continue
        print(f"    Found {len(found)}, using the {len(selected)} most recent.")
        limits_used[form] = len(selected)

        for position, filing in enumerate(selected):
            print(f"    Processing {form} (period end {filing.get('report_date', '?')})...", end=" ")

            # Item 1 (Business) is near-identical year to year: newest 10-K only.
            skip_items = ("1",) if (form == "10-K" and position > 0) else ()
            # Full notes only for the newest reports. A 10-Q filed BEFORE the
            # newest 10-K is superseded by it; later 10-Qs are new information.
            if form == "10-K":
                include_notes = position == 0
                if position == 0:
                    newest_10k_period = filing.get("report_date", "")
            elif form == "10-Q":
                include_notes = filing.get("report_date", "") > newest_10k_period
            else:
                include_notes = False

            try:
                html = edgar.fetch_document(filing["filing_url"])
                meta = {
                    "ticker": ticker, "company": company_name, "form": form,
                    "report_date": filing.get("report_date", ""),
                    "filed": filing.get("filing_date", ""),
                    "accession": filing.get("accession", ""),
                    "url": filing["filing_url"],
                }
                files = process_filing(html, meta, out_dir, skip_items, include_notes)
                written.extend(files)
                print(f"Done ({len(files)} section file(s))")
            except Exception as error:  # one bad filing must not sink the run
                print(f"Failed ({error})")
            time.sleep(0.5)  # be polite to SEC.gov


def _add_earnings_releases(cik, ticker, company_name, out_dir, start_year, end_year, written, limits_used):
    limit = CORPUS_LIMITS["earnings releases"]
    print("\n  Finding earnings releases (8-K Item 2.02)...")
    eight_ks = edgar.list_filings(cik, "8-K", start_year, end_year)
    results = [f for f in eight_ks if "2.02" in f.get("items", "")]
    selected = select_filings(results, limit)
    limits_used["earnings releases"] = 0
    if not selected:
        print("    No earnings releases found.")
        return
    print(f"    Found {len(results)}, using the {len(selected)} most recent.")
    for filing in selected:
        print(f"    Processing release filed {filing['filing_date']}...", end=" ")
        try:
            url = edgar.find_press_release_url(filing)
            if url is None:
                print("Skipped (no EX-99 press release exhibit)")
                continue
            time.sleep(0.2)
            html = edgar.fetch_document(url, timeout=60)
            meta = {"ticker": ticker, "company": company_name, "filed": filing["filing_date"],
                    "accession": filing["accession"], "url": url}
            files = process_earnings_release(html, meta, out_dir)
            written.extend(files)
            limits_used["earnings releases"] += len(files)
            print("Done" if files else "Skipped (release text too short to be real)")
        except Exception as error:
            print(f"Failed ({error})")
        time.sleep(0.5)


def build_corpus(company_folder, profile):
    """
    Builds the corpus for one ticker according to its profile.

    profile: dict from profile.py (ticker, company, cik, tier, sections,
    years_back). Returns the number of files written including the index.
    """
    ticker = profile["ticker"]
    company_name = profile["company"]
    cik = int(profile["cik"])
    sections = set(profile["sections"])
    end_year = date.today().year
    start_year = end_year - int(profile.get("years_back", 3))

    out_dir = company_folder / CORPUS_FOLDER_NAME
    _clear_corpus_folder(out_dir)

    print("\n" + "-" * 60)
    print(f"  BUILDING CORPUS ({profile['tier']} tier)")
    print("-" * 60)
    print(f"  Output: {out_dir}")
    print(f"  Sections: {', '.join(profile['sections'])}; searching {start_year}-{end_year}")

    written, limits_used = [], {}

    if "XBRL" in sections:
        _add_financials(cik, ticker, company_name, out_dir, written)

    forms = [form for form in ("10-K", "10-Q", "DEF 14A") if form in sections]
    if forms:
        _add_filings(cik, ticker, company_name, out_dir, forms, start_year, end_year, written, limits_used)

    if "8-K" in sections:
        _add_earnings_releases(cik, ticker, company_name, out_dir, start_year, end_year, written, limits_used)

    write_index(out_dir, ticker, company_name, written, limits_used, tier=profile["tier"])

    total_chars = sum(item["chars"] for item in written)
    print("\n" + "=" * 60)
    print(f"  CORPUS BUILT: {len(written) + 1} file(s)")
    print(f"  Size: {total_chars:,} chars (roughly {total_chars // 4:,} tokens)")
    print(f"  Folder: {out_dir}")
    print("=" * 60)
    return len(written) + 1
