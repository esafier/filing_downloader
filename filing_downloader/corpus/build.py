"""
Corpus driver: decides which filings go in, fetches them, dispatches to the
splitters, writes the index.

Two entry points:

  build_corpus    wipe and rebuild (add / rebuild). Only .md files inside the
                  corpus folder are ever deleted.
  refresh_corpus  incremental (the weekly job). Compares EDGAR against the
                  accessions recorded in _ticker.json, processes only new
                  filings, removes the files of filings that fell outside the
                  caps, and regenerates the financials and the index.

Both record what they wrote in profile["accessions"] (see profile.py) so the
next refresh knows which files belong to which filing.
"""

import time
from datetime import date

from ..config import CORPUS_FOLDER_NAME, CORPUS_LIMITS
from .. import edgar
from ..profile import accession_record
from .html import html_to_markdown
from .split_10k import process_10k
from .split_10q import process_10q
from .split_proxy import process_proxy
from .release import process_earnings_release
from .financials import build_financials_timeseries
from .index import write_index

# The filing kinds a corpus can hold. "8-K" means earnings releases only.
SECTION_FORMS = ("10-K", "10-Q", "DEF 14A")
RELEASE_KIND = "8-K"


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


# ------------------------------------------------------------------
# Planning: which filings SHOULD be in the corpus right now
# ------------------------------------------------------------------

def plan_filings(cik, sections, start_year, end_year):
    """
    Asks EDGAR what the corpus should contain: {kind: [filings, newest
    first]} for every kind in the profile's sections, already deduped and
    capped. Light-tier profiles (XBRL only) get an empty plan.
    """
    plan = {}
    for form in SECTION_FORMS:
        if form not in sections:
            continue
        print(f"\n  Finding {form} filings...")
        found = edgar.list_filings(cik, form, start_year, end_year)
        if not found and form == "10-K":
            # A corpus missing its annual report is not worth shipping just
            # because the years were off.
            print("    None in range; widening search to the last 5 years...")
            found = edgar.list_filings(cik, form, end_year - 5, end_year)
        selected = select_filings(found, CORPUS_LIMITS[form])
        print(f"    Found {len(found)}, keeping the {len(selected)} most recent." if selected
              else f"    No {form} found.")
        plan[form] = selected

    if RELEASE_KIND in sections:
        print("\n  Finding earnings releases (8-K Item 2.02)...")
        eight_ks = edgar.list_filings(cik, RELEASE_KIND, start_year, end_year)
        results = [f for f in eight_ks if "2.02" in f.get("items", "")]
        selected = select_filings(results, CORPUS_LIMITS["earnings releases"])
        print(f"    Found {len(results)}, keeping the {len(selected)} most recent." if selected
              else "    No earnings releases found.")
        plan[RELEASE_KIND] = selected
    return plan


def newest_10k_period(plan):
    """Period end of the newest 10-K in the plan, or "" if there is none."""
    tenks = plan.get("10-K") or []
    return tenks[0].get("report_date", "") if tenks else ""


def filing_flags(kind, filing, plan):
    """
    (skip_items, include_notes) for one filing, decided from the whole plan:
    Business (Item 1) and the full notes come from the newest 10-K only; a
    10-Q keeps its notes only if it is newer than that 10-K.
    """
    if kind == "10-K":
        position = next((i for i, f in enumerate(plan.get("10-K", []))
                         if f["accession"] == filing["accession"]), 0)
        return (("1",) if position > 0 else ()), position == 0
    if kind == "10-Q":
        return (), filing.get("report_date", "") > newest_10k_period(plan)
    return (), False


def _role_suffixes(kind, filing, plan):
    """
    (lose, need): filename endings a filing must NOT have and MUST have in
    its current role. Business and the full notes belong to the newest 10-K
    only; a 10-Q keeps its notes only while it is newer than that 10-K.
    """
    skip_items, include_notes = filing_flags(kind, filing, plan)
    notes_ending = "_08N_Notes.md" if kind == "10-K" else "_10-Q_Notes.md"
    lose, need = [], []
    if kind == "10-K":
        (lose if skip_items else need).append("_01_Business.md")
    if kind in ("10-K", "10-Q"):
        (need if include_notes else lose).append(notes_ending)
    return tuple(lose), tuple(need)


def superseded_suffixes(kind, filing, plan):
    """Endings a KEPT filing must lose because a newer 10-K arrived."""
    return _role_suffixes(kind, filing, plan)[0]


def needs_reprocessing(kind, filing, plan, record):
    """
    True when a kept filing's role grew (it became the newest 10-K, or a
    10-Q now outranks the newest 10-K) and the files that role requires are
    not in its record. The whole filing is then re-downloaded, since the
    section text is not kept locally.
    """
    _, need = _role_suffixes(kind, filing, plan)
    return any(not any(name.endswith(ending) for name in record["files"]) for ending in need)


# ------------------------------------------------------------------
# Processing one filing
# ------------------------------------------------------------------

def process_one(kind, filing, ticker, company_name, out_dir, plan):
    """
    Downloads and splits one planned filing. Returns the written-file
    records (possibly empty for a release with no usable exhibit). Raises on
    network or parsing failure; the caller decides what that means.
    """
    if kind == RELEASE_KIND:
        url = edgar.find_press_release_url(filing)
        if url is None:
            print("Skipped (no EX-99 press release exhibit)")
            return []
        time.sleep(0.2)
        html = edgar.fetch_document(url, timeout=60)
        meta = {"ticker": ticker, "company": company_name, "filed": filing["filing_date"],
                "accession": filing["accession"], "url": url}
        return process_earnings_release(html, meta, out_dir)

    skip_items, include_notes = filing_flags(kind, filing, plan)
    html = edgar.fetch_document(filing["filing_url"])
    meta = {
        "ticker": ticker, "company": company_name, "form": kind,
        "report_date": filing.get("report_date", ""),
        "filed": filing.get("filing_date", ""),
        "accession": filing.get("accession", ""),
        "url": filing["filing_url"],
    }
    return process_filing(html, meta, out_dir, skip_items, include_notes)


def _describe(kind, filing):
    if kind == RELEASE_KIND:
        return f"release filed {filing.get('filing_date', '?')}"
    return f"{kind} (period end {filing.get('report_date', '?')})"


def _process_planned(plan, ticker, company_name, out_dir, todo, written, records, errors):
    """
    Runs process_one over (kind, filing) pairs, collecting written records
    and accession records. One bad filing never sinks the run; it is noted
    in errors and retried next time because it is not recorded.
    """
    added = []
    for kind, filing in todo:
        print(f"    Processing {_describe(kind, filing)}...", end=" ")
        try:
            files = process_one(kind, filing, ticker, company_name, out_dir, plan)
        except Exception as error:
            print(f"Failed ({error})")
            errors.append(f"{_describe(kind, filing)}: {error}")
            time.sleep(0.5)
            continue
        written.extend(files)
        names = [item["filename"] for item in files]
        added.extend(names)
        records.append(accession_record(kind, filing, names))
        if files:
            print(f"Done ({len(files)} file(s))")
        elif kind != RELEASE_KIND:
            print("Done (nothing written)")
        time.sleep(0.5)  # be polite to SEC.gov
    return added


# ------------------------------------------------------------------
# Financials, index, bookkeeping
# ------------------------------------------------------------------

def _financials_filename(ticker):
    return f"{ticker}_00_Financials_Timeseries.md"


def _add_financials(cik, ticker, company_name, out_dir, written):
    """Regenerates the XBRL time series. Always rewritten; never stale."""
    print("\n  Building financial statement time series from SEC XBRL...", end=" ")
    path = out_dir / _financials_filename(ticker)
    financials = build_financials_timeseries(cik, ticker, company_name)
    if not financials:
        print("Skipped (no XBRL data).")
        if path.exists():
            path.unlink()
        return
    path.write_text(financials, encoding="utf-8")
    written.append({
        "filename": path.name, "chars": len(financials),
        "section": "Derived financial statements", "form": "XBRL",
        "period_end": "multi-period",
        "source": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
        "accession": "",
    })
    print("Done!")


def _limits_used(plan, records):
    """
    Counts for the index's Coverage block, from the filings that actually
    produced files (a failed download is planned but not counted).
    """
    used = {}
    for kind in plan:
        key = "earnings releases" if kind == RELEASE_KIND else kind
        used[key] = sum(1 for rec in records if rec["form"] == kind and rec["files"])
    return used


def read_file_record(path):
    """
    Rebuilds an index record from a corpus file's YAML front matter, so kept
    files can be listed without re-downloading anything.
    """
    text = path.read_text(encoding="utf-8")
    fields, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    value = value.strip()
                    if len(value) >= 2 and value[0] == value[-1] == '"':
                        value = value[1:-1]
                    fields[key.strip()] = value
            body = text[end + 4:]
    return {
        "filename": path.name, "chars": len(body.strip()),
        "section": fields.get("section", ""), "form": fields.get("form", ""),
        "period_end": fields.get("period_end", ""), "filed": fields.get("filed", ""),
        "source": fields.get("source", ""), "accession": fields.get("accession", ""),
    }


def remove_files(out_dir, names):
    """Deletes the named corpus files that exist; returns what went."""
    removed = []
    for name in names:
        path = out_dir / name
        if path.exists():
            path.unlink()
            removed.append(name)
    return removed


def diff_filings(plan, records, out_dir):
    """
    Compares the plan with the accession records from the last run.

    Returns (new, kept, dropped):
      new      [(kind, filing)] planned filings with no record, or whose
               recorded files are missing on disk (re-processed)
      kept     [(kind, filing, record)] planned and fully present
      dropped  [record] recorded filings no longer planned (fell outside the
               cap or the year window, or superseded by an amendment)
    """
    existing = {rec["accession"]: rec for rec in records if isinstance(rec, dict)}
    planned = set()
    new, kept = [], []
    for kind, filings in plan.items():
        for filing in filings:
            planned.add(filing["accession"])
            record = existing.get(filing["accession"])
            if record is not None and all((out_dir / name).exists() for name in record["files"]):
                kept.append((kind, filing, record))
            else:
                new.append((kind, filing))
    dropped = [rec for acc, rec in existing.items() if acc not in planned]
    return new, kept, dropped


def _clear_corpus_folder(out_dir):
    if out_dir.exists():
        for existing in out_dir.iterdir():
            if existing.is_file() and existing.suffix.lower() == ".md":
                existing.unlink()
            else:
                print(f"  NOTE: leaving non-Markdown file in place: {existing.name}")
    out_dir.mkdir(parents=True, exist_ok=True)


def _year_range(profile):
    end_year = date.today().year
    return end_year - int(profile.get("years_back", 3)), end_year


def _banner(title, profile, out_dir, start_year, end_year):
    print("\n" + "-" * 60)
    print(f"  {title} ({profile['tier']} tier)")
    print("-" * 60)
    print(f"  Output: {out_dir}")
    print(f"  Sections: {', '.join(profile['sections'])}; searching {start_year}-{end_year}")


def _finish(plan, written, out_dir, profile, records, company_folder):
    write_index(out_dir, profile["ticker"], profile["company"], written,
                _limits_used(plan, records), tier=profile["tier"],
                company_folder=company_folder)
    profile["accessions"] = sorted(records, key=lambda r: r.get("filed", ""), reverse=True)


# ------------------------------------------------------------------
# Entry points
# ------------------------------------------------------------------

def build_corpus(company_folder, profile):
    """
    Wipes the corpus folder and builds it from scratch per the profile.
    Updates profile["accessions"]. Returns the number of files written
    including the index.
    """
    ticker, company_name, cik = profile["ticker"], profile["company"], int(profile["cik"])
    sections = set(profile["sections"])
    start_year, end_year = _year_range(profile)
    out_dir = company_folder / CORPUS_FOLDER_NAME
    _clear_corpus_folder(out_dir)
    _banner("BUILDING CORPUS", profile, out_dir, start_year, end_year)

    written, records, errors = [], [], []
    if "XBRL" in sections:
        _add_financials(cik, ticker, company_name, out_dir, written)

    plan = plan_filings(cik, sections, start_year, end_year)
    todo = [(kind, filing) for kind, filings in plan.items() for filing in filings]
    if todo:
        print()
    _process_planned(plan, ticker, company_name, out_dir, todo, written, records, errors)
    _finish(plan, written, out_dir, profile, records, company_folder)

    total_chars = sum(item["chars"] for item in written)
    print("\n" + "=" * 60)
    print(f"  CORPUS BUILT: {len(written) + 1} file(s)")
    print(f"  Size: {total_chars:,} chars (roughly {total_chars // 4:,} tokens)")
    print(f"  Folder: {out_dir}")
    print("=" * 60)
    return len(written) + 1


def refresh_corpus(company_folder, profile):
    """
    Incremental update: only new filings are downloaded, existing section
    files keep their names, filings that fell outside the caps lose their
    files, and the financials and index are regenerated.

    Updates profile["accessions"]. Returns a summary dict:
      {"added": [filenames], "removed": [filenames], "errors": [messages]}
    """
    ticker, company_name, cik = profile["ticker"], profile["company"], int(profile["cik"])
    sections = set(profile["sections"])
    start_year, end_year = _year_range(profile)
    out_dir = company_folder / CORPUS_FOLDER_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    _banner("REFRESHING CORPUS", profile, out_dir, start_year, end_year)

    written, records, errors, removed = [], [], [], []
    if "XBRL" in sections:
        try:
            _add_financials(cik, ticker, company_name, out_dir, written)
        except Exception as error:
            print(f"Failed ({error})")
            errors.append(f"financials: {error}")

    plan = plan_filings(cik, sections, start_year, end_year)
    new, kept, dropped = diff_filings(plan, profile.get("accessions", []), out_dir)

    for record in dropped:
        removed.extend(remove_files(out_dir, record["files"]))

    for kind, filing, record in kept:
        if needs_reprocessing(kind, filing, plan, record):
            # Role grew (e.g. this 10-K is now the newest): rebuild it whole.
            removed.extend(remove_files(out_dir, record["files"]))
            new.append((kind, filing))
            continue
        endings = superseded_suffixes(kind, filing, plan)
        stale = [name for name in record["files"] if endings and name.endswith(endings)]
        removed.extend(remove_files(out_dir, stale))
        record["files"] = [name for name in record["files"] if name not in stale]
        written.extend(read_file_record(out_dir / name) for name in record["files"])
        records.append(record)

    if new:
        print(f"\n  {len(new)} new filing(s) to process:")
    else:
        print("\n  No new filings.")
    added = _process_planned(plan, ticker, company_name, out_dir, new, written, records, errors)
    _finish(plan, written, out_dir, profile, records, company_folder)

    print(f"\n  REFRESHED {ticker}: {len(added)} file(s) added, {len(removed)} removed, "
          f"{len(errors)} error(s)")
    return {"added": added, "removed": removed, "errors": errors}
