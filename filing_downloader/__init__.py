"""
Filing Downloader
=================
Builds one research folder per ticker under OneDrive\\Tickers, with a compact
Markdown corpus of SEC filings that a Claude (or ChatGPT) project reads live.

Package map
-----------
config.py        paths, SEC headers, corpus caps, folder convention
edgar.py         everything that talks to SEC EDGAR
profile.py       the per-ticker _ticker.json (tier, sections, years)
scaffold.py      creates the canonical ticker folder
corpus/          HTML -> Markdown, section splitting, financials, index, driver
cli.py           interactive menu and command-line subcommands
"""

__version__ = "2.0.0"
