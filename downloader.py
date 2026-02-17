"""
SEC Filing & Earnings Transcript Downloader
============================================
Downloads 10-K, 10-Q filings and earnings call transcripts
for any public company using API Ninja's APIs.

Usage: python downloader.py
"""

import os
import re
import sys
import time
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ============================================================
# CONFIGURATION
# ============================================================

# Load the API key from .env file (keeps it private and out of the code)
load_dotenv()
API_KEY = os.getenv("API_NINJAS_KEY")

# Base URL for all API Ninja endpoints
BASE_URL = "https://api.api-ninjas.com/v1"

# Where downloaded files go: Documents/SEC_Filings/
OUTPUT_DIR = Path.home() / "Documents" / "SEC_Filings"

# Headers sent with every API request (authentication)
HEADERS = {"X-Api-Key": API_KEY}


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
            print(f"\n  ERROR: API returned status {response.status_code}")
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

    result = call_api("ticker", {"ticker": ticker.upper()})

    if result is None:
        return None

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

    # Build the date range for the API (Jan 1 of start year to Dec 31 of end year)
    params = {
        "ticker": ticker.upper(),
        "filing": filing_type,
        "start": f"{start_year}-01-01",
        "end": f"{end_year}-12-31",
        "limit": 100  # Get up to 100 filings (premium feature)
    }

    filings = call_api("sec", params)

    if filings is None:
        return 0

    if len(filings) == 0:
        print(f"  No {filing_type} filings found for that date range.")
        return 0

    print(f"  Found {len(filings)} {filing_type} filing(s). Downloading...")

    # Create the subfolder for this filing type (e.g., AAPL/10-K/)
    filing_folder = company_folder / filing_type
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

            # SEC.gov REQUIRES a User-Agent with your name and email, or they block you (403).
            # This is their policy for automated access. Replace with your own info.
            sec_headers = {
                "User-Agent": "SEC Filing Downloader personal@example.com",
                "Accept-Encoding": "gzip, deflate",
            }
            file_response = requests.get(filing_url, headers=sec_headers, timeout=60)

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

    # Create the Transcripts subfolder
    transcript_folder = company_folder / "Transcripts"
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
# MAIN MENU & USER INTERACTION
# ============================================================

def get_year_range():
    """
    Asks the user for a start year and end year.
    Returns (start_year, end_year) as integers.

    Includes basic validation so you can't enter 'abc' or year 1800.
    """
    current_year = 2026  # Update this if running in a future year

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
    print("\n  What would you like to download?")
    print("  1. 10-K filings (annual reports)")
    print("  2. 10-Q filings (quarterly reports)")
    print("  3. Earnings call transcripts")
    print("  4. All of the above")

    while True:
        choice = input("\n  Enter your choice (1-4): ").strip()
        if choice in ("1", "2", "3", "4"):
            return choice
        print("  Please enter 1, 2, 3, or 4.")


def main():
    """
    The main function that runs when you execute the script.
    Walks you through the whole process step by step.
    """
    print("\n" + "="*60)
    print("  SEC Filing & Earnings Transcript Downloader")
    print("="*60)

    # Check that the API key is set up
    if not API_KEY or API_KEY == "your_api_key_here":
        print("\n  First-time setup needed!")
        print("  1. Open the .env file in this folder")
        print("  2. Replace 'your_api_key_here' with your API Ninja's key")
        print("  3. Save the file and run this script again")
        print(f"\n  .env file location: {Path(__file__).parent / '.env'}")
        sys.exit(1)

    # Step 1: Get the ticker symbol
    while True:
        ticker = input("\n  Enter a ticker symbol (e.g., AAPL): ").strip().upper()

        if not ticker:
            print("  Please enter a ticker symbol.")
            continue

        # Validate the ticker and get the company name
        company_name = validate_ticker(ticker)

        if company_name is None:
            retry = input("  Try another ticker? (y/n): ").strip().lower()
            if retry != "y":
                print("\n  Goodbye!")
                sys.exit(0)
            continue

        # Confirm with the user
        confirm = input(f"  Is this correct? (y/n): ").strip().lower()
        if confirm == "y":
            break

    # Step 2: Choose what to download
    choice = get_download_choice()

    # Step 3: Get the year range
    start_year, end_year = get_year_range()

    # Set up the company folder (e.g., Documents/SEC_Filings/AAPL - Apple Inc/)
    safe_name = make_safe_folder_name(company_name)
    company_folder = OUTPUT_DIR / f"{ticker} - {safe_name}"
    company_folder.mkdir(parents=True, exist_ok=True)

    print(f"\n  Saving to: {company_folder}")
    print("-" * 60)

    # Step 4: Download based on the user's choice
    total_files = 0

    if choice in ("1", "4"):
        # Download 10-K filings
        count = download_sec_filings(ticker, "10-K", start_year, end_year, company_folder)
        total_files += count

    if choice in ("2", "4"):
        # Download 10-Q filings
        count = download_sec_filings(ticker, "10-Q", start_year, end_year, company_folder)
        total_files += count

    if choice in ("3", "4"):
        # Download earnings transcripts
        count = download_transcripts(ticker, start_year, end_year, company_folder)
        total_files += count

    # Step 5: Show the final summary
    print("\n" + "=" * 60)
    print(f"  DONE! {total_files} file(s) saved.")
    print(f"  Location: {company_folder}")
    print("=" * 60)

    # Ask if they want to download for another company
    again = input("\n  Download for another company? (y/n): ").strip().lower()
    if again == "y":
        main()  # Run the whole thing again
    else:
        print("\n  Goodbye!\n")


# This is the entry point - runs main() when you execute "python downloader.py"
if __name__ == "__main__":
    main()
