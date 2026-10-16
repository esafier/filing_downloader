"""
Claude Project corpus builder
=============================
Turns raw SEC filings into a SMALL, well-labelled set of Markdown files that a
connected project reads well.

Why it looks like this
----------------------
One raw 10-K is ~600K characters. A two-year dump came out around 730K
tokens, far past what a project loads into context, so the model only ever
saw retrieved fragments and answered as if half the filings did not exist.

This package fixes that four ways:
  1. Caps the corpus by COUNT (config.CORPUS_LIMITS).
  2. Converts HTML tables into real Markdown tables (html.py).
  3. Splits each filing into per-section files (split_10k.py, split_10q.py,
     split_proxy.py) so retrieval pulls MD&A when you ask about margins.
  4. Condenses low-value sections (risk factors) and drops worthless ones
     (exhibit lists, signature pages).

build.py is the driver; index.py writes the 00_INDEX file the project reads
first.
"""

from .build import build_corpus  # noqa: F401
