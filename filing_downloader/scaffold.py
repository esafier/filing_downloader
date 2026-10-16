"""
Creates and finds the canonical ticker folder.

    <TICKERS_DIR>\\<TICKER - Company Name>\\
      _ticker.json                 profile (see profile.py)
      _Links.md
      Claude Project Knowledge\\    the corpus (see corpus/)
      Models\\  Memos & Theses\\  Earnings\\Prep\\  Earnings\\Reaction\\

Existing folders are reused and never renamed: the project connected to
them must keep working.
"""

from .config import CANONICAL_SUBFOLDERS, LINKS_MD_TEMPLATE


def make_safe_folder_name(name):
    """Removes characters Windows does not allow in folder names."""
    for char in '<>:"/\\|?*':
        name = name.replace(char, "")
    return name.strip(". ")


def find_existing_ticker_folder(ticker_root, ticker):
    """
    Returns the existing folder for this ticker (matched on 'TICKER - '
    prefix) or None. Avoids creating 'CROX - Crocs, Inc' next to an existing
    'CROX - Crocs' when the SEC's company name differs from the folder's.
    """
    if not ticker_root.exists():
        return None
    prefix = f"{ticker.upper()} - "
    for entry in ticker_root.iterdir():
        if entry.is_dir() and entry.name.startswith(prefix):
            return entry
    return None


def resolve_company_folder(ticker_root, ticker, company_name):
    """Existing folder if there is one, otherwise the path a new one gets."""
    existing = find_existing_ticker_folder(ticker_root, ticker)
    if existing is not None:
        return existing, True
    return ticker_root / f"{ticker.upper()} - {make_safe_folder_name(company_name)}", False


def ensure_canonical_structure(company_folder, ticker, company_name, cik=""):
    """
    Makes sure the ticker folder has the canonical subfolders and _Links.md.
    Idempotent: only creates what is missing. Safe to call every run.
    """
    company_folder.mkdir(parents=True, exist_ok=True)
    for sub in CANONICAL_SUBFOLDERS:
        (company_folder / sub).mkdir(parents=True, exist_ok=True)
    links_md = company_folder / "_Links.md"
    if not links_md.exists():
        links_md.write_text(
            LINKS_MD_TEMPLATE.format(ticker=ticker, company=company_name, cik=cik),
            encoding="utf-8",
        )
    return company_folder


def list_ticker_folders(ticker_root):
    """Every 'TICKER - Name' folder under the root, sorted. The watchlist."""
    if not ticker_root.exists():
        return []
    return sorted(
        entry for entry in ticker_root.iterdir()
        if entry.is_dir() and " - " in entry.name and not entry.name.startswith(("_", "."))
    )
