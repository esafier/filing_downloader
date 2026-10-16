"""
Command line and interactive menu.

    python downloader.py                      interactive (what the .bat runs)
    python downloader.py add NVDA --full      full corpus, all sections
    python downloader.py add XYZ --light      watchlist name: folder + financials
    python downloader.py add NVDA --full --sections 10-K,DEF14A --years 4
    python downloader.py rebuild NVDA         wipe and rebuild from the saved profile
    python downloader.py refresh NVDA         incremental: only new filings
    python downloader.py refresh --all        every ticker folder (the weekly job)
    python downloader.py list                 every ticker folder and its tier

Console output sticks to ASCII: the .bat launcher's terminal is cp1252 and
turns anything else into question marks.
"""

import argparse
import sys
from datetime import date

from . import __version__
from .config import TICKERS_DIR, SECTION_CHOICES, ALL_SECTIONS, DEFAULT_YEARS_BACK
from . import edgar
from .profile import new_profile, load_profile, save_profile, mark_run
from .scaffold import (resolve_company_folder, ensure_canonical_structure, list_ticker_folders,
                       find_existing_ticker_folder)
from .corpus import build_corpus
from .corpus.build import refresh_corpus
from .guide import write_ticker_guide

REFRESH_LOG_FILENAME = "_refresh_log.txt"


# ------------------------------------------------------------------
# Shared pieces
# ------------------------------------------------------------------

def parse_section_list(text):
    """
    Turns "1,3,5", "10-K,DEF14A" or "all" into a list of section keys.
    Numbers refer to the menu order; names are matched loosely (DEF14A ==
    DEF 14A). Returns None if nothing valid was given.
    """
    if not text:
        return None
    text = text.strip().lower()
    if text in ("all", "a", "*"):
        return list(ALL_SECTIONS)
    by_name = {key.replace(" ", "").lower(): key for key in ALL_SECTIONS}
    chosen = []
    for piece in text.replace(";", ",").replace(" ", ",").split(","):
        piece = piece.strip()
        if not piece:
            continue
        if piece.isdigit() and 1 <= int(piece) <= len(ALL_SECTIONS):
            key = ALL_SECTIONS[int(piece) - 1]
        else:
            key = by_name.get(piece.replace("-", "").replace(" ", "")) or by_name.get(piece)
            if key is None:
                # allow "10k" / "10q" / "8k" without the dash
                key = next((k for n, k in by_name.items() if n.replace("-", "") == piece.replace("-", "")), None)
        if key and key not in chosen:
            chosen.append(key)
    return chosen or None


def run_build(ticker, tier, sections=None, years_back=DEFAULT_YEARS_BACK, root=None):
    """Looks the ticker up, sets up the folder, saves the profile, builds."""
    root = root or TICKERS_DIR
    ticker = ticker.upper()
    found = edgar.lookup_company(ticker)
    if found is None:
        print(f"  ERROR: '{ticker}' is not in the SEC's company list.")
        return False
    cik, company_name = found

    company_folder, existed = resolve_company_folder(root, ticker, company_name)
    print(f"\n  {'Found existing' if existed else 'Creating'} folder: {company_folder.name}")
    ensure_canonical_structure(company_folder, ticker, company_name, cik=cik)

    profile = new_profile(ticker, company_name, cik, tier, sections, years_back)
    return _build_and_save(company_folder, profile, root)


def _build_and_save(company_folder, profile, root=None):
    root = root or TICKERS_DIR
    try:
        build_corpus(company_folder, profile)
    except edgar.EdgarError as error:
        print(f"\n  ERROR: SEC EDGAR problem: {error}")
        return False
    write_ticker_guide(root, company_folder, profile)
    save_profile(company_folder, mark_run(profile))
    print(f"  Profile saved: {company_folder.name}\\_ticker.json")
    return True


# ------------------------------------------------------------------
# Subcommands
# ------------------------------------------------------------------

def cmd_add(args):
    tier = "light" if args.light else "full"
    sections = parse_section_list(args.sections) if args.sections else None
    if args.sections and sections is None:
        print(f"  ERROR: could not understand sections '{args.sections}'.")
        return 1
    ok = run_build(args.ticker, tier, sections, args.years)
    return 0 if ok else 1


def cmd_rebuild(args):
    ticker = args.ticker.upper()
    folder = find_existing_ticker_folder(TICKERS_DIR, ticker)
    if folder is None:
        print(f"  ERROR: no folder for {ticker} under {TICKERS_DIR}. Use 'add' first.")
        return 1
    profile = load_profile(folder)
    if profile is None:
        print(f"  ERROR: {folder.name} has no _ticker.json. Use 'add' to create one.")
        return 1
    return 0 if _build_and_save(folder, profile) else 1


def cmd_list(args):
    folders = list_ticker_folders(TICKERS_DIR)
    if not folders:
        print(f"  No ticker folders under {TICKERS_DIR}")
        return 0
    print(f"\n  {'Folder':<45} {'Tier':<7} {'Last run':<12} Sections")
    print("  " + "-" * 90)
    for folder in folders:
        profile = load_profile(folder)
        if profile is None:
            print(f"  {folder.name:<45} {'(none)':<7} {'':<12} no _ticker.json")
            continue
        print(f"  {folder.name:<45} {profile['tier']:<7} {str(profile.get('last_run') or ''):<12} "
              f"{', '.join(profile['sections'])}")
    return 0


def refresh_one(folder, root=None):
    """
    Refreshes one ticker folder and returns its log line. Never raises: a
    broken ticker must not stop the weekly run, so every failure becomes an
    error in the line instead.
    """
    root = root or TICKERS_DIR
    today = date.today().isoformat()
    ticker = folder.name.split(" - ")[0]
    profile = load_profile(folder)
    if profile is None:
        return f"{today} {ticker:<6} skipped: no _ticker.json (use 'add' first)"
    ticker = profile["ticker"]
    try:
        ensure_canonical_structure(folder, ticker, profile["company"], cik=profile["cik"])
        summary = refresh_corpus(folder, profile)
        write_ticker_guide(root, folder, profile)
        save_profile(folder, mark_run(profile))
    except Exception as error:  # EdgarError, OneDrive locks, anything
        print(f"\n  ERROR refreshing {ticker}: {error}")
        return f"{today} {ticker:<6} added=0 removed=0 errors=1 | FAILED: {error}"

    details = [f"+{name}" for name in summary["added"]] + [f"-{name}" for name in summary["removed"]]
    line = (f"{today} {ticker:<6} added={len(summary['added'])} removed={len(summary['removed'])} "
            f"errors={len(summary['errors'])}")
    if details:
        line += " | " + " ".join(details)
    if summary["errors"]:
        line += " | " + "; ".join(summary["errors"])
    return line


def append_refresh_log(root, lines):
    """Appends the per-ticker lines to <root>\\_refresh_log.txt."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / REFRESH_LOG_FILENAME
    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")
    return path


def cmd_refresh(args):
    if not args.all and not args.ticker:
        print("  Give a ticker, or --all for every folder under the Tickers root.")
        return 1
    if args.all:
        folders = list_ticker_folders(TICKERS_DIR)
        if not folders:
            print(f"  No ticker folders under {TICKERS_DIR}")
            return 0
    else:
        folder = find_existing_ticker_folder(TICKERS_DIR, args.ticker.upper())
        if folder is None:
            print(f"  ERROR: no folder for {args.ticker.upper()} under {TICKERS_DIR}. Use 'add' first.")
            return 1
        folders = [folder]

    print(f"\n  Refreshing {len(folders)} ticker folder(s) under {TICKERS_DIR}")
    lines = [refresh_one(folder) for folder in folders]
    log_path = append_refresh_log(TICKERS_DIR, lines)

    print("\n" + "=" * 60)
    print("  REFRESH SUMMARY (also in " + str(log_path) + ")")
    print("=" * 60)
    for line in lines:
        print("  " + line)
    # Exit 1 only when a whole ticker failed; per-filing errors are in the log.
    failed = sum(1 for line in lines if "| FAILED:" in line)
    return 1 if failed else 0


# ------------------------------------------------------------------
# Interactive menu (what the .bat launcher runs)
# ------------------------------------------------------------------

def _ask(prompt, default=None):
    answer = input(prompt).strip()
    return answer if answer else default


def interactive_one():
    """One ticker, start to finish. Returns False if the user bailed."""
    while True:
        ticker = _ask("\n  Enter a ticker symbol (e.g., AAPL): ", "").upper()
        if not ticker:
            print("  Please enter a ticker symbol.")
            continue
        print(f"  Looking up {ticker}...")
        try:
            found = edgar.lookup_company(ticker)
        except edgar.EdgarError as error:
            print(f"  ERROR: could not reach SEC EDGAR ({error}).")
            return False
        if found is None:
            print(f"  '{ticker}' is not in the SEC's company list.")
            if _ask("  Try another ticker? (y/n): ", "n").lower() != "y":
                return False
            continue
        cik, company_name = found
        print(f"  Found: {company_name} (CIK {cik})")
        if _ask("  Is this correct? (y/n): ", "n").lower() == "y":
            break

    print("\n  Tier:")
    print("    [L] Light - folder, links, financials time series. Quartr for everything else.")
    print("    [F] Full  - the corpus: filing sections, proxy, releases, financials.")
    tier = "light" if _ask("  Choose L or F [F]: ", "f").lower().startswith("l") else "full"

    sections = None
    years_back = DEFAULT_YEARS_BACK
    if tier == "full":
        print("\n  Pick sections (comma list like 1,3,5 or 'all'):")
        for number, (_, label) in enumerate(SECTION_CHOICES, start=1):
            print(f"    {number}. {label}")
        while True:
            sections = parse_section_list(_ask("  Sections [all]: ", "all"))
            if sections:
                break
            print("  Please enter numbers from the list, or 'all'.")
        while True:
            raw = _ask(f"  Years back to search [{DEFAULT_YEARS_BACK}]: ", str(DEFAULT_YEARS_BACK))
            if raw.isdigit() and 1 <= int(raw) <= 15:
                years_back = int(raw)
                break
            print("  Enter a number between 1 and 15.")

    company_folder, existed = resolve_company_folder(TICKERS_DIR, ticker, company_name)
    print(f"\n  {'Found existing' if existed else 'Creating'} folder: {company_folder.name}")
    ensure_canonical_structure(company_folder, ticker, company_name, cik=cik)
    profile = new_profile(ticker, company_name, cik, tier, sections, years_back)
    return _build_and_save(company_folder, profile)


def interactive_main():
    print("\n" + "=" * 60)
    print(f"  Filing Downloader v{__version__} - ticker research folders")
    print("=" * 60)
    print(f"\n  Output folder: {TICKERS_DIR}")
    while True:
        completed = interactive_one()
        if not completed:
            break
        if _ask("\n  Do another company? (y/n): ", "n").lower() != "y":
            break
    print("\n  Goodbye!\n")
    return 0


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(prog="downloader", description="Build ticker research folders.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    add = sub.add_parser("add", help="create or rebuild a ticker folder")
    add.add_argument("ticker")
    tier = add.add_mutually_exclusive_group()
    tier.add_argument("--light", action="store_true", help="watchlist tier: folder + financials only")
    tier.add_argument("--full", action="store_true", help="full corpus (default)")
    add.add_argument("--sections", help="comma list: 1,3,5 or 10-K,10-Q,DEF14A,8-K,XBRL or all")
    add.add_argument("--years", type=int, default=DEFAULT_YEARS_BACK, help="years back to search")
    add.set_defaults(func=cmd_add)

    rebuild = sub.add_parser("rebuild", help="rebuild a ticker from its saved _ticker.json")
    rebuild.add_argument("ticker")
    rebuild.set_defaults(func=cmd_rebuild)

    sub.add_parser("list", help="show every ticker folder and its tier").set_defaults(func=cmd_list)
    refresh = sub.add_parser("refresh", help="incremental update: add new filings, keep the rest")
    refresh.add_argument("ticker", nargs="?", help="one ticker (omit with --all)")
    refresh.add_argument("--all", action="store_true", help="every ticker folder under the Tickers root")
    refresh.set_defaults(func=cmd_refresh)
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return interactive_main()
    args = build_parser().parse_args(argv)
    return args.func(args)
