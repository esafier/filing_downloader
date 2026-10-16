"""
Writes <TICKER>_00_INDEX.md, the first thing the project should read.

Tells it what is in the corpus, what period each file covers, what was
deliberately left out, and what lives in Quartr instead, so a gap is never
mistaken for "the company never disclosed this".

The "left out" section is generated from the files actually written, not
from a description of the rule; an index that describes a filter drifts
away from the filter (tasks/lessons.md).
"""

from datetime import date

from ..config import CORPUS_LIMITS

# Everything the corpus does not carry and Quartr does. Shown on every index;
# a light-tier index is essentially this list plus the financials file.
LIGHT_TIER_QUARTR_ITEMS = [
    "Annual and quarterly reports (10-K, 10-Q, 20-F, 40-F) - this is a light-tier "
    "folder, so no filing sections are stored locally",
    "Proxy statements and earnings press releases",
]

QUARTR_ITEMS = [
    "Earnings call transcripts (every quarter; Quartr's read_transcript has a "
    "Q&A-only mode and keyword search)",
    "Earnings slide decks and investor presentations",
    "Conference, fireside chat and investor day transcripts",
    "Anything released after the as-of date above",
    "Other 8-Ks (deals, executive changes, financings) and Form 4 insider filings",
    "Peer companies' filings and standardized financials",
]


def write_index(out_dir, ticker, company_name, written, limits_used, tier="full"):
    total_chars = sum(item["chars"] for item in written)
    approx_tokens = total_chars // 4
    dates = [item.get("filed", "") for item in written]
    newest = max((d for d in dates if d[:4].isdigit()), default="unknown")

    lines = [
        f"# {ticker} - {company_name}: corpus index",
        "",
        "Read this first. It lists every document in this project and what it covers.",
        "",
        f"- **Tier:** {tier}" + ("  (watchlist name: financials only; use Quartr for filings and calls)"
                                 if tier == "light" else ""),
        f"- **As of:** built {date.today().isoformat()}; newest document dated {newest}",
        f"- Files: {len(written)}",
        f"- Total size: {total_chars:,} characters (roughly {approx_tokens:,} tokens)",
        "",
        "## Coverage",
        "",
        f"- Annual reports: {limits_used.get('10-K', 0)}",
        f"- Quarterly reports: {limits_used.get('10-Q', 0)}",
        f"- Proxy statements: {limits_used.get('DEF 14A', 0)}",
        f"- Earnings press releases: {limits_used.get('earnings releases', 0)}",
        "- Earnings call transcripts: 0 (all transcripts via Quartr)",
        "",
        "## Files",
        "",
        "| File | Form | Period | Section |",
        "| --- | --- | --- | --- |",
    ]
    for item in sorted(written, key=lambda i: i["filename"]):
        lines.append(f"| `{item['filename']}` | {item['form']} | {item['period_end']} | {item['section']} |")

    if tier == "full":
        lines += ["", "## What was deliberately left out", ""] + _left_out_lines(written)

    lines += ["", "## Not included - use Quartr for these", ""]
    if tier == "light":
        lines += [f"- {item}" for item in LIGHT_TIER_QUARTR_ITEMS]
    lines += [f"- {item}" for item in QUARTR_ITEMS]
    lines.append("")

    (out_dir / f"{ticker}_00_INDEX.md").write_text("\n".join(lines), encoding="utf-8")


def _left_out_lines(written):
    notes_files = [item for item in written if "Notes (all)" in item["section"]]
    notes_periods = ", ".join(
        f"{item['form']} {item['period_end']}"
        for item in sorted(notes_files, key=lambda i: i["period_end"])
    )
    return [
        "- **Risk factors are condensed** to their headings plus the first sentence",
        "  of each. The headings carry the substance; the paragraphs beneath are",
        "  templated legal hedging.",
        f"- **Full notes, verbatim, for the newest reports only:** {notes_periods or 'none'}.",
        "  Older 10-Ks and 10-Qs keep their statements but not their notes - the",
        "  newest 10-K repeats the prior year. Within those `Notes` files nothing is",
        "  filtered, so a topic missing there wasn't disclosed.",
        "- **Business (Item 1) is kept from the newest 10-K only**; it barely",
        "  changes year to year.",
        "- **10-Qs keep only MD&A, the financial statements and their notes** - the",
        "  rest repeats the 10-K nearly verbatim.",
        "- **Proxy keeps comp, ownership and governance sections**; the",
        "  meeting-logistics half is dropped.",
        "- **Dropped entirely:** exhibit indexes, signature pages, auditor consents,",
        "  cover-page checkboxes, internal-controls boilerplate, and 10-K items not",
        "  listed above (properties, mine safety, market for equity, etc.).",
        f"- **Caps:** {CORPUS_LIMITS['10-K']} annual, {CORPUS_LIMITS['10-Q']} quarterly, "
        f"{CORPUS_LIMITS['DEF 14A']} proxy, {CORPUS_LIMITS['earnings releases']} releases.",
        "",
        "If you need something that was cut, every file's `source` front-matter field",
        "links to the full filing on EDGAR, and Quartr has the same documents.",
    ]
