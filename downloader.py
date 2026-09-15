"""
SEC Filing & Earnings Transcript Downloader
============================================
Downloads 10-K, 10-Q, DEF 14A (proxy statement) and 20-F filings plus earnings
call transcripts for any public company.

Filings come from SEC EDGAR (free, official, no API key). The API Ninjas key is
optional and only used for earnings transcripts.

TWO MODES
---------
Menu options 1-5 build the RAW ARCHIVE: one big plain-text file per document,
under Filings\\. Complete but enormous - a single 10-K is ~600K characters.

Menu option 6 builds the CLAUDE PROJECT CORPUS: the same source documents
turned into small, labelled Markdown files under "Claude Project Knowledge\\".
Upload that folder to a Claude Project. It is roughly half the size, keeps
financial tables readable, and splits each filing by section so Claude
retrieves the right part. See corpus.py for how and why.

OUTPUT STRUCTURE:
  C:\\Users\\ely\\OneDrive\\Tickers\\<TICKER - Company Name>\\
    ├── Claude Project Knowledge\\   <-- option 6; upload THIS to Claude
    ├── Filings\\                    <-- options 1-5; the raw archive
    │   ├── 10-K\\
    │   ├── 10-Q\\
    │   ├── DEF 14A\\
    │   ├── Transcripts\\
    │   ├── Other\\
    │   └── (20-F, etc. created on demand)
    ├── Models\\
    ├── Memos & Theses\\
    ├── Earnings\\
    │   ├── Prep\\
    │   └── Reaction\\
    └── _Links.md

The corpus folder is wiped and rebuilt on each run so stale files can never
end up in an upload. Filings\\ is never touched by option 6.

When a new ticker is downloaded for the first time, the full canonical
structure is auto-created. Existing ticker folders are preserved.

Usage: python downloader.py
"""

import os
import re
import sys
import time
import requests
from datetime import date
from pathlib import Path
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Our own module: turns raw filings into the compact Markdown corpus that
# actually fits inside a Claude Project. See corpus.py for the why.
import corpus

# ============================================================
# CONFIGURATION
# ============================================================

# Load the API key from .env file (keeps it private and out of the code)
load_dotenv()
API_KEY = os.getenv("API_NINJAS_KEY")

# Base URL for all API Ninja endpoints
BASE_URL = "https://api.api-ninjas.com/v1"

# Canonical output location — OneDrive\Tickers (set after May-2026 reorg).
# Override via TICKERS_ROOT env var if you need to test against a different folder.
TICKERS_DIR = Path(os.environ.get("TICKERS_ROOT", str(Path.home() / "OneDrive" / "Tickers")))

# Canonical subfolder structure auto-created for every new ticker folder.
CANONICAL_SUBFOLDERS = [
    "Filings/10-K",
    "Filings/10-Q",
    "Filings/8-K",
    "Filings/DEF 14A",
    "Filings/Transcripts",
    "Filings/Other",
    "Models",
    "Memos & Theses",
    "Earnings/Prep",
    "Earnings/Reaction",
]

LINKS_MD_TEMPLATE = """# Links — {ticker}

Cross-surface pointers for {company}. Fill in as you go.

- **Claude.ai Project:** _(paste URL when you create one)_
- **Cowork artifacts:** _(e.g., 'Insider Buys Explorer' filtered to this name)_
- **SEC EDGAR:** https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=&type=
- **Company IR page:** _(paste URL)_
- **Notes:**
"""

# Headers sent with every API request (authentication)
HEADERS = {"X-Api-Key": API_KEY}

# Headers for anything we fetch from SEC.gov / EDGAR.
# SEC REQUIRES a User-Agent identifying you (name + email) or they block you (403).
SEC_HEADERS = {
    "User-Agent": "Ely Safier elymsafier@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def call_api(endpoint, params):
    """
    Makes a GET request to an API Ninja endpoint.

    'endpoint' is the API path like '/sec' or '/ticker'
    'params' is a dictionary of query parameters like {'ticker': 'AAPL'}

    Returns the JSON response, or None if something went wrong.
    """
    url = f"{BASE_URL}/{endpoint}"

    try:
        response = requests.get(url, headers=HEADERS, params=params, timeout=30)

        # Check if API returned an error
        if response.status_code == 401:
            print("\n  ERROR: Invalid API key. Check your .env file.")
            return None
        elif response.status_code == 429:
            print("\n  ERROR: Rate limit hit. Wait a moment and try again.")
            return None
        elif response.status_code != 200:
            # Show the API's own error message too — a bare status code
            # tells you almost nothing when debugging
            print(f"\n  ERROR: API returned status {response.status_code}: {response.text[:200]}")
            return None

        return response.json()

    except requests.exceptions.Timeout:
        print("\n  ERROR: Request timed out. Check your internet connection.")
        return None
    except requests.exceptions.ConnectionError:
        print("\n  ERROR: Could not connect. Check your internet connection.")
        return None


def html_to_clean_text(html_content):
    """
    Converts raw SEC filing HTML into clean, readable text.

    SEC filings are full of HTML tags, inline CSS, XBRL data, and other
    markup that bloats the file. This strips all of that out, leaving
    just the words and numbers — which is what AI tools actually need.

    A 20MB HTML filing typically becomes ~500KB of clean text.
    """
    # Parse the HTML using BeautifulSoup
    soup = BeautifulSoup(html_content, "html.parser")

    # Remove elements that are never useful text content:
    # <style> = CSS styling, <script> = JavaScript, <xbrl>/<ix:> = financial markup tags
    for tag in soup.find_all(["style", "script", "xbrl", "ix:header"]):
        tag.decompose()

    # Pull out just the visible text
    raw_text = soup.get_text(separator="\n")

    # Clean up the messy whitespace that HTML leaves behind:
    # 1. Remove leading/trailing spaces on each line
    # 2. Collapse runs of 3+ blank lines down to 2 (keeps some structure)
    lines = [line.strip() for line in raw_text.splitlines()]
    clean_text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))

    return clean_text.strip()


def make_safe_folder_name(name):
    """
    Removes characters that aren't allowed in Windows folder names.
    For example, turns 'Apple Inc.' into 'Apple Inc'
    """
    # Characters Windows doesn't allow in folder/file names
    bad_chars = '<>:"/\\|?*'
    for char in bad_chars:
        name = name.replace(char, "")
    return name.strip(". ")


# ============================================================
# TICKER VALIDATION
# ============================================================

def validate_ticker(ticker):
    """
    Checks if a ticker symbol is valid by looking it up on API Ninja.
    Returns the company name if valid, or None if not found.

    This is useful because:
    - It catches typos before wasting API calls
    - We get the company name for folder naming
    """
    print(f"\n  Looking up {ticker.upper()}...")

    # No API key? Fall back to SEC's own company list, which carries the
    # company name and needs no key. Keeps the tool usable without a key.
    if not API_KEY or API_KEY == "your_api_key_here":
        name = get_company_name_from_sec(ticker)
        if name:
            print(f"  Found: {name} (via SEC EDGAR)")
        return name

    result = call_api("ticker", {"ticker": ticker.upper()})

    # API Ninjas failed for some reason - try EDGAR before giving up
    if result is None:
        return get_company_name_from_sec(ticker)

    # The API returns a list - empty means ticker not found
    if isinstance(result, list) and len(result) == 0:
        print(f"  Ticker '{ticker.upper()}' not found. Check the symbol and try again.")
        return None

    # Pull out the company info (API may return a dict or a list with one item)
    company = result[0] if isinstance(result, list) else result

    company_name = company.get("name", "Unknown Company")
    exchange = company.get("exchange", "Unknown Exchange")

    print(f"  Found: {company_name} ({exchange})")

    return company_name


# ============================================================
# SEC EDGAR FALLBACK (free, official, no API key needed)
# ============================================================

# Caches so we don't re-download the same big files repeatedly within one run.
# company_tickers.json is ~1MB and was previously fetched once PER FILING TYPE,
# so a "download all" run pulled it four times for no reason.
_CIK_CACHE = {}
_SUBMISSIONS_CACHE = {}


def get_company_name_from_sec(ticker):
    """
    Gets a company's name from SEC's public ticker list (no API key needed).

    Used when API Ninjas is unavailable or no key is configured, so that a
    missing key never blocks the tool.
    """
    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        response = requests.get(url, headers=SEC_HEADERS, timeout=30)
        if response.status_code != 200:
            return None
        for entry in response.json().values():
            if entry.get("ticker", "").upper() == ticker.upper():
                _CIK_CACHE[ticker.upper()] = int(entry["cik_str"])
                return entry.get("title", "Unknown Company")
    except requests.exceptions.RequestException:
        return None

    print(f"  Ticker '{ticker.upper()}' not found in SEC's company list.")
    return None


def get_cik_for_ticker(ticker):
    """
    Looks up a company's CIK number from SEC's public ticker list.

    The CIK (Central Index Key) is the SEC's own ID for each company —
    EDGAR organizes everything by CIK, not ticker. This mapping file is
    free and public, no API key needed.
    """
    ticker = ticker.upper()
    if ticker in _CIK_CACHE:
        return _CIK_CACHE[ticker]

    url = "https://www.sec.gov/files/company_tickers.json"

    try:
        response = requests.get(url, headers=SEC_HEADERS, timeout=30)
        if response.status_code != 200:
            print(f"  ERROR: SEC ticker lookup returned status {response.status_code}")
            return None
        # The file is a dict of {"0": {"cik_str": ..., "ticker": ..., "title": ...}, ...}
        for entry in response.json().values():
            if entry.get("ticker", "").upper() == ticker:
                _CIK_CACHE[ticker] = int(entry["cik_str"])
                return _CIK_CACHE[ticker]
    except requests.exceptions.RequestException as error:
        print(f"  ERROR: SEC ticker lookup failed ({error})")
        return None

    print(f"  Ticker '{ticker.upper()}' not found in SEC's company list.")
    return None


def get_filings_from_edgar(ticker, filing_type, start_year, end_year):
    """
    Gets a company's filing list straight from SEC EDGAR (data.sec.gov).

    EDGAR is the only lookup source — free, official, no API key, and it's
    where the documents live anyway. Returns a list of dicts carrying the
    filing date, the fiscal period end (report_date), the accession number,
    and the document URL.
    """
    cik = get_cik_for_ticker(ticker)
    if cik is None:
        return []

    # Reuse the submissions data if we already pulled it this run. Without
    # this, downloading 10-K + 10-Q + DEF 14A refetches the same JSON 3 times.
    cache_key = (cik, start_year, end_year)
    if cache_key in _SUBMISSIONS_CACHE:
        return _filter_batches(_SUBMISSIONS_CACHE[cache_key], cik, filing_type,
                               start_year, end_year)

    # EDGAR's submissions API: one JSON per company, CIK zero-padded to 10 digits
    url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"

    try:
        response = requests.get(url, headers=SEC_HEADERS, timeout=30)
        if response.status_code != 200:
            print(f"  ERROR: EDGAR returned status {response.status_code}")
            return []
        data = response.json()
    except requests.exceptions.RequestException as error:
        print(f"  ERROR: EDGAR request failed ({error})")
        return []

    # 'recent' holds the last ~1000 filings. Older ones live in extra archive
    # files listed under 'files' — only fetch the ones overlapping our years.
    batches = [data.get("filings", {}).get("recent", {})]
    for extra in data.get("filings", {}).get("files", []):
        oldest = int(extra["filingFrom"][:4])
        newest = int(extra["filingTo"][:4])
        if newest >= start_year and oldest <= end_year:
            try:
                extra_response = requests.get(
                    f"https://data.sec.gov/submissions/{extra['name']}",
                    headers=SEC_HEADERS, timeout=30,
                )
                if extra_response.status_code == 200:
                    batches.append(extra_response.json())
                time.sleep(0.2)  # be polite to SEC servers
            except requests.exceptions.RequestException:
                pass  # skip an archive we can't fetch; keep what we have

    _SUBMISSIONS_CACHE[cache_key] = batches
    return _filter_batches(batches, cik, filing_type, start_year, end_year)


def _filter_batches(batches, cik, filing_type, start_year, end_year):
    """
    Pulls one form type out of EDGAR's submission batches.

    Split out from get_filings_from_edgar so the (expensive) download and the
    (cheap) filtering can be cached separately — we fetch the company's
    submissions once and then filter it per form type.
    """
    # EDGAR stores filings as parallel columns (all forms in one list, all
    # dates in another, etc.) — zip() walks them together row by row
    filings = []
    for batch in batches:
        rows = zip(
            batch.get("form", []),
            batch.get("filingDate", []),
            batch.get("reportDate", []),
            batch.get("accessionNumber", []),
            batch.get("primaryDocument", []),
            batch.get("primaryDocDescription", []),
        )
        for form, date, report_date, accession, document, description in rows:
            if form != filing_type:
                continue
            if not (start_year <= int(date[:4]) <= end_year):
                continue
            if not document:
                continue  # no primary document listed (rare, mostly old paper filings)
            # Build the document URL: accession number loses its dashes in the path
            accession_clean = accession.replace("-", "")
            filings.append({
                "filing_date": date,
                "filing_url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_clean}/{document}",
                # Keep the rest of what EDGAR gave us. report_date is the fiscal
                # PERIOD END, which is what we label files with — filing_date is
                # just when it was submitted and is often months later.
                "report_date": report_date or date,
                "accession": accession,
                "description": description,
                "form": form,
                "cik": cik,
            })

    # Newest first, same as the API
    filings.sort(key=lambda f: f["filing_date"], reverse=True)
    return filings


# ============================================================
# SEC FILING DOWNLOADER
# ============================================================

def download_sec_filings(ticker, filing_type, start_year, end_year, company_folder):
    """
    Downloads SEC filings (10-K or 10-Q) for a given ticker and year range.

    How it works:
    1. Calls the SEC API to get a list of filing URLs
    2. Downloads each filing from SEC.gov
    3. Saves them as HTML files in the company folder
    """
    print(f"\n  Searching for {filing_type} filings ({start_year}-{end_year})...")

    # EDGAR is the only lookup source now. API Ninjas' /sec endpoint has
    # returned 400 on every request since mid-2026 (see tasks/lessons.md), and
    # it only ever supplied URLs — the documents always came from SEC.gov
    # anyway. Going EDGAR-only also guarantees every filing carries its full
    # metadata (period end, accession), which the corpus builder needs.
    filings = get_filings_from_edgar(ticker, filing_type, start_year, end_year)

    if len(filings) == 0:
        print(f"  No {filing_type} filings found for that date range.")
        return 0

    print(f"  Found {len(filings)} {filing_type} filing(s). Downloading...")

    # Create the subfolder for this filing type under Filings\
    # (e.g., AAPL - Apple Inc/Filings/10-K/)
    filing_folder = company_folder / "Filings" / filing_type
    filing_folder.mkdir(parents=True, exist_ok=True)

    downloaded_count = 0

    for filing in filings:
        filing_date = filing.get("filing_date", "unknown-date")
        filing_url = filing.get("filing_url", "")

        if not filing_url:
            print(f"    Skipping filing from {filing_date} - no URL provided")
            continue

        # Create a descriptive filename like "2024-02-02_10-K.txt"
        filename = f"{filing_date}_{filing_type}.txt"
        filepath = filing_folder / filename

        # Skip if we already downloaded this file (saves time on re-runs)
        if filepath.exists():
            print(f"    Already have: {filename} (skipping)")
            downloaded_count += 1
            continue

        # Download the actual filing from SEC.gov
        try:
            print(f"    Downloading: {filename}...", end=" ")

            file_response = requests.get(filing_url, headers=SEC_HEADERS, timeout=60)

            if file_response.status_code == 200:
                # Convert the HTML to clean text (strips all markup, CSS, XBRL tags)
                # This shrinks files from ~20MB to ~500KB — small enough for AI tools
                clean_text = html_to_clean_text(file_response.text)
                filepath.write_text(clean_text, encoding="utf-8")
                downloaded_count += 1
                print("Done!")
            else:
                print(f"Failed (status {file_response.status_code})")

            # Small delay to be respectful to SEC.gov servers
            time.sleep(0.5)

        except Exception as error:
            print(f"Failed ({error})")

    return downloaded_count


# ============================================================
# EARNINGS TRANSCRIPT DOWNLOADER
# ============================================================

def find_available_transcripts(ticker, start_year, end_year):
    """
    Searches for which earnings call transcripts are available.
    Returns a list of (year, quarter) tuples we can download.

    We do this first because not every company has transcripts for every quarter,
    and it's faster to check availability than to blindly request each one.
    """
    params = {
        "ticker": ticker.upper(),
        "start": f"{start_year}-01-01",
        "end": f"{end_year}-12-31",
    }

    results = call_api("earningstranscriptsearch", params)

    if results is None or len(results) == 0:
        return []

    # Pull out the year and quarter, but only keep ones in our date range.
    # The API sometimes returns ALL transcripts regardless of date filters,
    # so we filter here to make sure we only get what the user asked for.
    available = []
    for item in results:
        year = item.get("year")
        quarter = item.get("quarter")
        if year and quarter and start_year <= int(year) <= end_year:
            available.append((year, quarter))

    return available


def download_transcripts(ticker, start_year, end_year, company_folder):
    """
    Downloads earnings call transcripts for a given ticker and year range.

    How it works:
    1. First searches for which transcripts exist (not all quarters have them)
    2. Downloads each transcript's full text
    3. Saves them as .txt files in the Transcripts subfolder
    """
    print(f"\n  Searching for earnings transcripts ({start_year}-{end_year})...")

    # Step 1: Find out which transcripts are available
    available = find_available_transcripts(ticker, start_year, end_year)

    if len(available) == 0:
        print("  No earnings transcripts found for that date range.")
        return 0

    print(f"  Found {len(available)} transcript(s). Downloading...")

    # Create the Transcripts subfolder under Filings\
    transcript_folder = company_folder / "Filings" / "Transcripts"
    transcript_folder.mkdir(parents=True, exist_ok=True)

    downloaded_count = 0

    for year, quarter in available:
        filename = f"{year}_Q{quarter}_transcript.txt"
        filepath = transcript_folder / filename

        # Skip if already downloaded
        if filepath.exists():
            print(f"    Already have: {filename} (skipping)")
            downloaded_count += 1
            continue

        print(f"    Downloading: {filename}...", end=" ")

        # Call the transcript API for this specific quarter
        result = call_api("earningstranscript", {
            "ticker": ticker.upper(),
            "year": year,
            "quarter": quarter,
        })

        if result is None:
            print("Failed (API error)")
            continue

        # The transcript text is in the 'transcript' field
        transcript_text = result.get("transcript", "")

        if not transcript_text:
            print("Failed (empty transcript)")
            continue

        # Build a nice header for the text file
        earnings_date = result.get("date", "Unknown date")
        header = (
            f"{'='*60}\n"
            f"Earnings Call Transcript\n"
            f"Company: {ticker.upper()}\n"
            f"Period: Q{quarter} {year}\n"
            f"Date: {earnings_date}\n"
            f"{'='*60}\n\n"
        )

        # Save the transcript to a text file
        filepath.write_text(header + transcript_text, encoding="utf-8")
        downloaded_count += 1
        print("Done!")

        # Small delay between API calls
        time.sleep(0.3)

    return downloaded_count


# ============================================================
# CLAUDE PROJECT CORPUS BUILDER
# ============================================================

def select_filings(filings, limit):
    """
    Picks which filings actually make it into the corpus.

    Two jobs:
      1. Deduplicate by fiscal period. A company that amends a 10-K files it
         twice for the same period; without this we'd burn a slot on the
         duplicate. We keep whichever was filed LAST (the amended version).
      2. Cap the count. This is what keeps the corpus inside the token budget
         no matter how wide a year range the user typed.
    """
    by_period = {}
    for filing in filings:
        period = filing.get("report_date") or filing.get("filing_date")
        existing = by_period.get(period)
        if existing is None or filing.get("filing_date", "") > existing.get("filing_date", ""):
            by_period[period] = filing

    unique = sorted(by_period.values(),
                    key=lambda f: f.get("report_date") or f.get("filing_date"),
                    reverse=True)
    return unique[:limit]


def build_claude_corpus(ticker, company_name, company_folder, start_year, end_year):
    """
    Builds the Claude Project upload set.

    Writes to its own folder, wiped and rebuilt each run so a stale file from
    a previous run can never sneak into an upload. The raw Filings\\ archive
    is never touched.
    """
    out_dir = company_folder / corpus.CORPUS_FOLDER_NAME

    # Wipe and rebuild. Only ever deletes .md files inside our own folder —
    # if anything else is in there, leave it alone and say so.
    if out_dir.exists():
        for existing in out_dir.iterdir():
            if existing.is_file() and existing.suffix.lower() == ".md":
                existing.unlink()
            else:
                print(f"  NOTE: leaving non-Markdown file in place: {existing.name}")
    out_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "-" * 60)
    print("  BUILDING CLAUDE PROJECT CORPUS")
    print("-" * 60)
    print(f"  Output: {out_dir}")
    print(f"  Caps: {corpus.CORPUS_LIMITS['10-K']} annual, "
          f"{corpus.CORPUS_LIMITS['10-Q']} quarterly, "
          f"{corpus.CORPUS_LIMITS['DEF 14A']} proxy, "
          f"{corpus.CORPUS_LIMITS['transcripts']} transcripts")

    written = []
    limits_used = {}

    # ---- Derived financials (the single most valuable file) ----------
    cik = get_cik_for_ticker(ticker)
    if cik is not None:
        print("\n  Building financial statement time series from SEC XBRL...", end=" ")
        financials = corpus.build_financials_timeseries(cik, ticker, company_name)
        if financials:
            path = out_dir / f"{ticker}_00_Financials_Timeseries.md"
            path.write_text(financials, encoding="utf-8")
            written.append({
                "filename": path.name,
                "chars": len(financials),
                "section": "Derived financial statements",
                "form": "XBRL",
                "period_end": "multi-period",
                "source": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
                "accession": "",
            })
            print("Done!")
        else:
            print("Skipped (no XBRL data).")

    # ---- Filings -----------------------------------------------------
    for form in ("10-K", "10-Q", "DEF 14A"):
        limit = corpus.CORPUS_LIMITS[form]
        print(f"\n  Finding {form} filings...")
        found = get_filings_from_edgar(ticker, form, start_year, end_year)

        # If nothing in the requested window, widen it — a corpus missing its
        # annual report is not worth shipping just because the years were off.
        if not found and form == "10-K":
            print("    None in range; widening search to the last 5 years...")
            found = get_filings_from_edgar(ticker, form, end_year - 5, end_year)

        selected = select_filings(found, limit)
        if not selected:
            print(f"    No {form} found.")
            limits_used[form] = 0
            continue

        print(f"    Found {len(found)}, using the {len(selected)} most recent.")
        limits_used[form] = len(selected)

        for position, filing in enumerate(selected):
            period = filing.get("report_date", "?")
            print(f"    Processing {form} (period end {period})...", end=" ")

            # Item 1 (Business) is near-identical year to year. Keep it from
            # the most recent 10-K only; a second copy costs ~25K tokens and
            # tells you nothing new.
            skip_items = ("1",) if (form == "10-K" and position > 0) else ()
            try:
                response = requests.get(filing["filing_url"], headers=SEC_HEADERS, timeout=90)
                if response.status_code != 200:
                    print(f"Failed (status {response.status_code})")
                    continue

                meta = {
                    "ticker": ticker,
                    "company": company_name,
                    "form": form,
                    "report_date": filing.get("report_date", ""),
                    "filed": filing.get("filing_date", ""),
                    "accession": filing.get("accession", ""),
                    "url": filing["filing_url"],
                }
                files = corpus.process_filing(response.text, meta, out_dir, skip_items)
                written.extend(files)
                print(f"Done ({len(files)} section file(s))")

            except Exception as error:
                print(f"Failed ({error})")

            time.sleep(0.5)  # be polite to SEC.gov

    # ---- Transcripts -------------------------------------------------
    limit = corpus.CORPUS_LIMITS["transcripts"]
    if not API_KEY:
        print("\n  Skipping transcripts (no API Ninjas key set in .env).")
        limits_used["transcripts"] = 0
    else:
        print("\n  Finding earnings transcripts...")
        available = find_available_transcripts(ticker, start_year, end_year)
        # Newest first, then cap
        available = sorted(set(available),
                           key=lambda yq: (int(yq[0]), int(yq[1])),
                           reverse=True)[:limit]

        if not available:
            print("    No transcripts found.")
            limits_used["transcripts"] = 0
        else:
            print(f"    Using the {len(available)} most recent.")
            limits_used["transcripts"] = len(available)

            for year, quarter in available:
                print(f"    Processing Q{quarter} {year}...", end=" ")
                result = call_api("earningstranscript", {
                    "ticker": ticker.upper(), "year": year, "quarter": quarter,
                })
                if result is None:
                    print("Failed (API error)")
                    continue
                text = result.get("transcript", "")
                if not text:
                    print("Failed (empty)")
                    continue

                meta = {
                    "ticker": ticker,
                    "company": company_name,
                    "year": year,
                    "quarter": quarter,
                    "date": result.get("date", ""),
                }
                files = corpus.process_transcript(text, meta, out_dir)
                written.extend(files)
                print(f"Done ({len(files)} file(s))")
                time.sleep(0.3)

    # ---- Index -------------------------------------------------------
    corpus.write_index(out_dir, ticker, company_name, written, limits_used)

    total_chars = sum(item["chars"] for item in written)
    print("\n" + "=" * 60)
    print(f"  CORPUS BUILT: {len(written) + 1} file(s)")
    print(f"  Size: {total_chars:,} chars (roughly {total_chars // 4:,} tokens)")
    if total_chars // 4 > 250000:
        print("  WARNING: over the ~250K token target. Lower the caps in")
        print("           corpus.py (CORPUS_LIMITS) if Claude misses content.")
    print(f"  Folder: {out_dir}")
    print("\n  Upload the WHOLE folder to your Claude Project.")
    print("=" * 60)

    return len(written) + 1


# ============================================================
# MAIN MENU & USER INTERACTION
# ============================================================

def get_year_range():
    """
    Asks the user for a start year and end year.
    Returns (start_year, end_year) as integers.

    Includes basic validation so you can't enter 'abc' or year 1800.
    """
    # Read the year from the clock. This used to be hardcoded, which silently
    # blocked filings once the calendar rolled past it.
    current_year = date.today().year

    while True:
        start_input = input("\n  Start year (e.g., 2020): ").strip()
        try:
            start_year = int(start_input)
            if start_year < 1993 or start_year > current_year:
                print(f"  Please enter a year between 1993 and {current_year}.")
                continue
            break
        except ValueError:
            print("  That's not a valid year. Try again.")

    while True:
        end_input = input(f"  End year (e.g., {current_year}): ").strip()
        try:
            end_year = int(end_input)
            if end_year < start_year:
                print(f"  End year must be {start_year} or later.")
                continue
            if end_year > current_year:
                print(f"  Can't go beyond {current_year}.")
                continue
            break
        except ValueError:
            print("  That's not a valid year. Try again.")

    return start_year, end_year


def get_download_choice():
    """
    Shows a menu of what to download and returns the user's choice.
    """
    print("\n  What would you like to do?")
    print("  1. 10-K filings (annual reports)")
    print("  2. 10-Q filings (quarterly reports)")
    print("  3. DEF 14A filings (proxy statements)")
    print("  4. Earnings call transcripts")
    print("  5. All of the above (raw text archive)")
    print("  6. Build Claude Project corpus  <-- use this one for AI research")

    while True:
        choice = input("\n  Enter your choice (1-6): ").strip()
        if choice in ("1", "2", "3", "4", "5", "6"):
            return choice
        print("  Please enter a number from 1 to 6.")


def find_existing_ticker_folder(ticker_root, ticker):
    """
    Look for an existing folder for this ticker by 'TICKER - ' prefix.

    Avoids creating duplicate folders when the API's company name differs
    from the user's existing folder name (e.g., 'CROX - Crocs' already exists
    but the API returns 'Crocs, Inc' — without this check we'd create a
    second 'CROX - Crocs, Inc' folder).

    Returns the existing folder NAME (string) if found, else None.
    Case-sensitive prefix match using the ticker symbol.
    """
    if not ticker_root.exists():
        return None
    prefix = f"{ticker.upper()} - "
    for d in ticker_root.iterdir():
        if d.is_dir() and d.name.startswith(prefix):
            return d.name
    return None


def ensure_canonical_structure(company_folder, ticker, company_name):
    """
    Make sure a ticker folder has the full canonical structure
    (Filings/<type>, Models, Memos & Theses, Earnings/Prep, Earnings/Reaction, _Links.md).

    Idempotent — only creates what's missing. Safe to call every run.
    """
    company_folder.mkdir(parents=True, exist_ok=True)
    for sub in CANONICAL_SUBFOLDERS:
        (company_folder / sub).mkdir(parents=True, exist_ok=True)
    links_md = company_folder / "_Links.md"
    if not links_md.exists():
        links_md.write_text(
            LINKS_MD_TEMPLATE.format(ticker=ticker, company=company_name),
            encoding="utf-8",
        )


def run_one_company():
    """
    Runs the whole flow for a single ticker.

    Split out from main() so main() can loop. It used to call itself
    recursively for "download another company", which grew the call stack
    with every company you did.
    """
    output_dir = TICKERS_DIR
    print(f"\n  Output folder: {output_dir}")

    # Step 1: Get the ticker symbol
    while True:
        ticker = input("\n  Enter a ticker symbol (e.g., AAPL): ").strip().upper()

        if not ticker:
            print("  Please enter a ticker symbol.")
            continue

        company_name = validate_ticker(ticker)

        if company_name is None:
            retry = input("  Try another ticker? (y/n): ").strip().lower()
            if retry != "y":
                return False
            continue

        confirm = input("  Is this correct? (y/n): ").strip().lower()
        if confirm == "y":
            break

    # Step 2: Choose what to do
    choice = get_download_choice()

    # Step 3: Work out the year range.
    # The corpus builder picks its own window - it caps by COUNT, not by year,
    # so asking the user for years would just be a way to get it wrong.
    if choice == "6":
        end_year = date.today().year
        start_year = end_year - 3
        include_20f = False
        print(f"\n  Searching {start_year}-{end_year}, keeping the most recent of each.")
    else:
        include_20f = input(
            "\n  Also download 20-F filings (foreign issuer annual reports)? (y/n): "
        ).strip().lower() in ("y", "yes")
        start_year, end_year = get_year_range()

    # Set up the company folder. Check for an EXISTING folder for this ticker
    # first so we don't create a duplicate when the API's company name differs
    # from the folder name already on disk.
    existing_name = find_existing_ticker_folder(output_dir, ticker)
    if existing_name:
        company_folder = output_dir / existing_name
        print(f"\n  Found existing folder: {existing_name}")
    else:
        safe_name = make_safe_folder_name(company_name)
        company_folder = output_dir / f"{ticker} - {safe_name}"
        print(f"\n  Creating new folder: {company_folder.name}")

    ensure_canonical_structure(company_folder, ticker, company_name)

    print(f"\n  Saving to: {company_folder}")
    print("-" * 60)

    # Step 4: Do the work
    if choice == "6":
        build_claude_corpus(ticker, company_name, company_folder, start_year, end_year)
        return True

    total_files = 0

    if choice in ("1", "5"):
        total_files += download_sec_filings(ticker, "10-K", start_year, end_year, company_folder)

    if choice in ("2", "5"):
        total_files += download_sec_filings(ticker, "10-Q", start_year, end_year, company_folder)

    if choice in ("3", "5"):
        total_files += download_sec_filings(ticker, "DEF 14A", start_year, end_year, company_folder)

    if choice in ("4", "5"):
        total_files += download_transcripts(ticker, start_year, end_year, company_folder)

    if include_20f:
        total_files += download_sec_filings(ticker, "20-F", start_year, end_year, company_folder)

    print("\n" + "=" * 60)
    print(f"  DONE! {total_files} file(s) saved.")
    print(f"  Location: {company_folder}")
    print("=" * 60)
    return True


def main():
    """
    The main function that runs when you execute the script.
    Loops so you can do several companies in one sitting.
    """
    print("\n" + "=" * 60)
    print("  SEC Filing & Earnings Transcript Downloader")
    print("=" * 60)

    # The API Ninjas key is now OPTIONAL. Filings come from SEC EDGAR, which
    # needs no key at all; the key is only used for earnings transcripts.
    # Previously the script refused to start without it, which blocked the
    # whole tool over a feature most runs don't need.
    if not API_KEY or API_KEY == "your_api_key_here":
        print("\n  NOTE: No API Ninjas key found in .env.")
        print("  SEC filings will still work - they come from EDGAR, no key needed.")
        print("  Earnings transcripts will be skipped.")
        print(f"  To enable transcripts, add your key to: {Path(__file__).parent / '.env'}")

    while True:
        completed = run_one_company()
        if not completed:
            break
        again = input("\n  Do another company? (y/n): ").strip().lower()
        if again != "y":
            break

    print("\n  Goodbye!\n")


# This is the entry point - runs main() when you execute "python downloader.py"
if __name__ == "__main__":
    main()
