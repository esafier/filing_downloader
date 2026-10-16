"""
Configuration shared by every module.

Everything that used to be scattered across downloader.py and corpus.py
(two copies of the SEC headers, the output root, the caps) lives here once.
"""

import os
from pathlib import Path

# Canonical output location: OneDrive\Tickers.
# Override with the TICKERS_ROOT environment variable to test against a
# different folder without touching the real one.
TICKERS_DIR = Path(os.environ.get("TICKERS_ROOT", str(Path.home() / "OneDrive" / "Tickers")))

# Headers for anything fetched from SEC.gov / EDGAR.
# The SEC REQUIRES a User-Agent identifying you (name + email) or it blocks
# you with a 403.
SEC_HEADERS = {
    "User-Agent": "Ely Safier elymsafier@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

# Folder inside each ticker folder that holds the generated corpus. The name
# predates the Quartr era; it is kept so existing connected projects keep
# working.
CORPUS_FOLDER_NAME = "Claude Project Knowledge"

# Per-ticker profile file. Records tier, chosen sections and years so a
# rebuild (and, later, a scheduled refresh) knows what to do without asking.
PROFILE_FILENAME = "_ticker.json"

# Subfolders created for every ticker. These are where the project writes its
# own outputs (memos, models, earnings notes). The old Filings\\ raw-text
# folders are no longer created; existing ones are left alone.
CANONICAL_SUBFOLDERS = [
    "Models",
    "Memos & Theses",
    "Earnings/Prep",
    "Earnings/Reaction",
]

# How many of each document type end up in a FULL corpus. Caps by count, not
# by year, so a wide year range cannot blow the token budget.
CORPUS_LIMITS = {
    "10-K": 2,
    "10-Q": 4,
    "DEF 14A": 1,
    "earnings releases": 4,
}

# The pieces a full corpus can contain. The CLI shows these as a menu; the
# profile stores which ones the user picked.
SECTION_CHOICES = [
    ("10-K", "Annual report sections (10-K)"),
    ("10-Q", "Quarterly report sections (10-Q)"),
    ("DEF 14A", "Proxy statement sections (DEF 14A)"),
    ("8-K", "Earnings press releases (8-K Item 2.02)"),
    ("XBRL", "Financial statements time series (SEC XBRL)"),
]
ALL_SECTIONS = [key for key, _ in SECTION_CHOICES]

# A light-tier ticker gets only the cheap, always-useful piece. Everything
# else is routed to Quartr by the index.
LIGHT_SECTIONS = ["XBRL"]

DEFAULT_YEARS_BACK = 3

# A table wider than this is almost always page-layout scaffolding rather
# than data. Those are left as plain text.
MAX_TABLE_COLS = 10

# A "section" shorter than this is a table-of-contents line or a cross
# reference, not a real section.
MIN_SECTION_CHARS = 500

# MD&A is kept verbatim by default. It is also the biggest thing in the
# corpus (~70% on a heavy filer). Flip to True to trim prose to two sentences
# per paragraph while keeping every table and heading.
CONDENSE_MDA = False

LINKS_MD_TEMPLATE = """# Links - {ticker}

Cross-surface pointers for {company}. Fill in as you go.

- **Claude.ai Project:** _(paste URL when you create one)_
- **Cowork artifacts:** _(e.g., 'Insider Buys Explorer' filtered to this name)_
- **SEC EDGAR:** https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=
- **Company IR page:** _(paste URL)_
- **Notes:**
"""
