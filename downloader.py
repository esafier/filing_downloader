"""
Filing Downloader - entry point.

Double-click Run_Filing_Downloader.bat (or run `python downloader.py`) for
the interactive menu. Run `python downloader.py --help` for the command
line. All the code lives in the filing_downloader/ package; this file only
starts it.
"""

import sys

from filing_downloader.cli import main

if __name__ == "__main__":
    sys.exit(main())
