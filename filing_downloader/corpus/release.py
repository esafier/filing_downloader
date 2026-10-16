"""
Earnings press releases (8-K Item 2.02, exhibit 99.1).

Kept whole: releases are short, and the KPI tables and non-GAAP
reconciliations are the reason to have them at all.
"""

from ..config import MIN_SECTION_CHARS
from .common import write_section
from .html import html_to_markdown


def process_earnings_release(html, meta, out_dir):
    """Writes one release as a single file. meta: ticker, company, filed, accession, url."""
    written = []
    body = html_to_markdown(html)

    # EDGAR wraps exhibits in a few lines of filing metadata ("EX-99.1", the
    # file name, "Document") at the very top. Peel them off.
    lines = body.splitlines()
    while lines and (not lines[0].strip()
                     or lines[0].strip().upper().startswith("EX-99")
                     or lines[0].strip().lower().endswith((".htm", ".html"))
                     or lines[0].strip() == "Document"):
        lines.pop(0)
    body = "\n".join(lines)

    # Anything this short is a cover page or a broken exhibit, not a release
    if len(body) < MIN_SECTION_CHARS * 2:
        return written

    ticker = meta["ticker"]
    fields = {
        "ticker": ticker,
        "company": meta.get("company", ""),
        "form": "8-K Item 2.02 - earnings release (EX-99.1)",
        # 8-Ks carry no fiscal period; the quarter is in the release headline.
        "period_end": f"see headline (released {meta['filed']})",
        "filed": meta.get("filed", ""),
        "accession": meta.get("accession", ""),
        "source": meta.get("url", ""),
        "section": "Earnings press release (full)",
    }
    write_section(out_dir, f"{ticker}_REL{meta['filed']}_EarningsRelease.md", fields, body, written)
    return written
