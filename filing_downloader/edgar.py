"""
SEC EDGAR client.

Free, official, no API key. Everything the tool fetches from the SEC goes
through here so there is one place for headers, caching and politeness
delays.

Endpoints used:
  sec.gov/files/company_tickers.json          ticker -> CIK + company name
  data.sec.gov/submissions/CIK##########.json  the company's filing list
  <filing folder>/<accession>-index.htm        exhibit list of one filing
  data.sec.gov/api/xbrl/companyfacts/...       XBRL financial facts
"""

import time
import requests
from bs4 import BeautifulSoup

from .config import SEC_HEADERS

# Caches so the same big files are not re-downloaded within one run.
# company_tickers.json is ~1MB; the submissions JSON is a few hundred KB.
_TICKER_TABLE = None          # {TICKER: (cik, company name)}
_SUBMISSIONS_CACHE = {}       # {(cik, start_year, end_year): [batches]}


class EdgarError(Exception):
    """Raised when EDGAR cannot be reached or returns an error."""


def _get(url, timeout=30):
    """One GET with the SEC headers. Raises EdgarError on any failure."""
    try:
        response = requests.get(url, headers=SEC_HEADERS, timeout=timeout)
    except requests.exceptions.RequestException as error:
        raise EdgarError(f"request failed: {error}") from error
    if response.status_code != 200:
        raise EdgarError(f"status {response.status_code} from {url}")
    return response


def _ticker_table():
    """Downloads the SEC's ticker list once per run and indexes it by ticker."""
    global _TICKER_TABLE
    if _TICKER_TABLE is None:
        data = _get("https://www.sec.gov/files/company_tickers.json").json()
        _TICKER_TABLE = {
            entry.get("ticker", "").upper(): (int(entry["cik_str"]), entry.get("title", "Unknown Company"))
            for entry in data.values()
        }
    return _TICKER_TABLE


def lookup_company(ticker):
    """
    Returns (cik, company_name) for a ticker, or None if the SEC has no
    record of it. Share classes use a dash on EDGAR (BRK-B, not BRK.B).
    """
    return _ticker_table().get(ticker.upper().replace(".", "-"))


def get_submissions(cik, start_year, end_year):
    """
    Returns the raw EDGAR submission batches overlapping the year range.

    'recent' holds the last ~1000 filings. Older ones live in extra archive
    files listed under 'files'; only the ones overlapping our years are
    fetched.
    """
    cache_key = (cik, start_year, end_year)
    if cache_key in _SUBMISSIONS_CACHE:
        return _SUBMISSIONS_CACHE[cache_key]

    data = _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").json()
    batches = [data.get("filings", {}).get("recent", {})]
    for extra in data.get("filings", {}).get("files", []):
        oldest = int(extra["filingFrom"][:4])
        newest = int(extra["filingTo"][:4])
        if newest >= start_year and oldest <= end_year:
            try:
                batches.append(_get(f"https://data.sec.gov/submissions/{extra['name']}").json())
                time.sleep(0.2)  # be polite to SEC servers
            except EdgarError:
                pass  # skip an archive we can't fetch; keep what we have

    _SUBMISSIONS_CACHE[cache_key] = batches
    return batches


def list_filings(cik, form, start_year, end_year):
    """
    Lists one form type for a company, newest first.

    Each entry carries filing_date, report_date (the fiscal PERIOD END, which
    is what files are labelled with), accession, items (8-K item numbers like
    "2.02,9.01") and the primary document URL.
    """
    filings = []
    for batch in get_submissions(cik, start_year, end_year):
        forms = batch.get("form", [])
        rows = zip(
            forms,
            batch.get("filingDate", []),
            batch.get("reportDate", []),
            batch.get("accessionNumber", []),
            batch.get("primaryDocument", []),
            batch.get("primaryDocDescription", []),
            batch.get("items", []) or [""] * len(forms),
        )
        for row_form, filed, report_date, accession, document, description, items in rows:
            if row_form != form:
                continue
            if not (start_year <= int(filed[:4]) <= end_year):
                continue
            if not document:
                continue  # no primary document (rare, mostly old paper filings)
            accession_clean = accession.replace("-", "")
            filings.append({
                "form": row_form,
                "filing_date": filed,
                "report_date": report_date or filed,
                "accession": accession,
                "description": description,
                "items": items or "",
                "cik": cik,
                "filing_url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_clean}/{document}",
            })

    filings.sort(key=lambda f: f["filing_date"], reverse=True)
    return filings


def fetch_document(url, timeout=90):
    """Downloads one filing document and returns its HTML text."""
    return _get(url, timeout=timeout).text


def find_press_release_url(filing):
    """
    Finds the press release exhibit (EX-99.1) inside an 8-K.

    The 8-K's primary document is just a cover page saying "see Exhibit
    99.1". The actual release lives in a separate exhibit listed on the
    filing's index page. Returns None if there is no HTML EX-99 exhibit.
    """
    accession = filing["accession"]
    folder = f"https://www.sec.gov/Archives/edgar/data/{filing['cik']}/{accession.replace('-', '')}"
    response = _get(f"{folder}/{accession}-index.htm")

    # Index table columns: Seq | Description | Document | Type | Size
    exhibits = []
    for row in BeautifulSoup(response.text, "html.parser").find_all("tr"):
        cells = row.find_all("td")
        link = row.find("a")
        if len(cells) < 4 or link is None:
            continue
        exhibit_type = cells[3].get_text(strip=True).upper()
        # Inline-XBRL documents link through a viewer ("/ix?doc=/Archives/...");
        # strip that so we fetch the raw HTML file itself.
        href = link["href"].replace("/ix?doc=", "")
        if exhibit_type.startswith("EX-99") and href.lower().endswith((".htm", ".html")):
            exhibits.append((exhibit_type, f"https://www.sec.gov{href}"))

    if not exhibits:
        return None
    # EX-99.1 is the press release by convention; EX-99.2 is often slides.
    for exhibit_type, url in exhibits:
        if exhibit_type in ("EX-99.1", "EX-99.01"):
            return url
    return exhibits[0][1]


def fetch_company_facts(cik):
    """Downloads the SEC's XBRL Company Facts file for one company, or None."""
    try:
        return _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", timeout=60).json()
    except EdgarError as error:
        print(f"  WARNING: Company Facts unavailable ({error})")
        return None
