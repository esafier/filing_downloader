"""
10-Q splitting: Part I / Part II first, then items.

10-Q item numbers RESTART at Part II, so without the part split "Part II
Item 1" (Legal Proceedings) gets mistaken for "Part I Item 1" (Financial
Statements).
"""

import re

from ..config import MIN_SECTION_CHARS
from .common import (NOTES_HEADER, period_label, write_section,
                     split_statements_and_notes, maybe_condense_mda)
from .split_10k import split_by_item, _base_fields

TENQ_PART1_ORDER = ["1", "2", "3", "4"]
TENQ_PART2_ORDER = ["1", "1A", "2", "3", "4", "5", "6"]

PART_PATTERN = re.compile(r"^\**\s*PART\s+(I{1,3}|IV)\b.*$", re.IGNORECASE)

# Only MD&A and the statements are worth keeping. The rest of a 10-Q repeats
# the 10-K almost verbatim.
TENQ_KEEP = {
    "1": ("Financials", "Part I Item 1 - Financial Statements"),
    "2": ("MDA",        "Part I Item 2 - MD&A"),
}


def split_on_parts(text):
    """Splits a 10-Q into {"I": text, "II": text}, ignoring TOC entries."""
    part_starts = []
    offset = 0
    for line in text.splitlines(keepends=True):
        match = PART_PATTERN.match(line.strip())
        if match:
            part_starts.append((offset, match.group(1).upper()))
        offset += len(line)

    parts = {}
    for index, (start, numeral) in enumerate(part_starts):
        end = part_starts[index + 1][0] if index + 1 < len(part_starts) else len(text)
        if end - start >= MIN_SECTION_CHARS:
            parts.setdefault(numeral, (start, end))
            if end - start > parts[numeral][1] - parts[numeral][0]:
                parts[numeral] = (start, end)  # a later, longer occurrence wins

    return {numeral: text[start:end] for numeral, (start, end) in parts.items()}


def process_10q(text, meta, out_dir, include_notes=True):
    """Writes Financials, Notes (optional) and MD&A files for one 10-Q."""
    written = []
    ticker, form = meta["ticker"], meta["form"]
    label = period_label(meta.get("report_date"), form)
    base_fields = _base_fields(meta)

    part_one = split_on_parts(text).get("I", text)
    sections = split_by_item(part_one, TENQ_PART1_ORDER)
    if not sections:
        write_section(out_dir, f"{ticker}_{label}_10-Q_Full.md",
                      dict(base_fields, section="10-Q (unsplit)"),
                      "_Section splitting failed on this 10-Q; full text kept._\n\n" + text,
                      written)
        return written

    for item, (suffix, section_name) in TENQ_KEEP.items():
        if item not in sections:
            continue
        body = sections[item]["text"]
        if item == "1":
            # Part I Item 1 carries the statements PLUS every note.
            body, notes = split_statements_and_notes(body)
            if notes and include_notes:
                write_section(out_dir, f"{ticker}_{label}_10-Q_Notes.md",
                              dict(base_fields, section="Part I Item 1 - Notes (all)"),
                              NOTES_HEADER + notes, written)
        elif item == "2":
            body = maybe_condense_mda(body)
        write_section(out_dir, f"{ticker}_{label}_10-Q_{suffix}.md",
                      dict(base_fields, section=section_name), body, written)
    return written
