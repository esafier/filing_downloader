"""
10-K splitting: by Item number, with risk-factor condensation.

Also home to the generic item splitter, which the 10-Q splitter reuses with
its own item order.
"""

import re

from ..config import MIN_SECTION_CHARS
from .common import (NOTES_HEADER, period_label, write_section,
                     split_statements_and_notes, maybe_condense_mda)

# Canonical 10-K item order. Used to reject out-of-sequence matches, which
# are nearly always cross-references ("see Item 7") rather than headings.
TENK_ITEM_ORDER = [
    "1", "1A", "1B", "1C", "2", "3", "4", "5", "6", "7", "7A", "8",
    "9", "9A", "9B", "9C", "10", "11", "12", "13", "14", "15", "16",
]

ITEM_PATTERN = re.compile(
    r"^\**\s*ITEM\s+(\d{1,2}[A-C]?)\s*[\.\:\-–—]?\s*(.*?)\**\s*$",
    re.IGNORECASE,
)

# Which 10-K items make it into the corpus, and how each is treated.
# Everything not listed is dropped: exhibit lists, signature pages, controls
# boilerplate, market-for-stock tables, and so on.
TENK_KEEP = {
    "1":  ("01_Business",         "Item 1 - Business",             "verbatim"),
    "1A": ("1A_RiskFactors",      "Item 1A - Risk Factors",        "condense"),
    "3":  ("03_LegalProceedings", "Item 3 - Legal Proceedings",    "verbatim_if_long"),
    "7":  ("07_MDA",              "Item 7 - MD&A",                 "verbatim"),
    "7A": ("07_MDA",              "Item 7A - Market Risk",         "append_mda"),
    "8":  ("08_Financials",       "Item 8 - Financial Statements", "financials"),
}


def find_item_candidates(text):
    """
    Every line that looks like an 'Item N.' heading, as
    (char_offset, item_number, heading_text). These are CANDIDATES: the table
    of contents produces a full set of false positives.
    """
    candidates = []
    offset = 0
    for line in text.splitlines(keepends=True):
        match = ITEM_PATTERN.match(line.strip())
        if match:
            item = match.group(1).upper()
            title = match.group(2).strip().strip("*").strip()
            candidates.append((offset, item, title))
        offset += len(line)
    return candidates


def split_by_item(text, item_order):
    """
    Splits filing text into {item_number: {"title", "text"}}.

    'Item 7' appears in the table of contents, in cross-references and as
    the real heading. Two rules filter the fakes: the heading must start a
    line, and the section must be at least MIN_SECTION_CHARS long. Then the
    canonical order is enforced so anything still out of place is dropped.

    Returns {} if no believable set of sections is found; the caller then
    writes the filing whole rather than producing wrong splits.
    """
    candidates = find_item_candidates(text)
    if not candidates:
        return {}

    sized = []
    for index, (start, item, title) in enumerate(candidates):
        end = candidates[index + 1][0] if index + 1 < len(candidates) else len(text)
        if end - start >= MIN_SECTION_CHARS:
            sized.append((start, end, item, title))
    if not sized:
        return {}

    rank = {item: position for position, item in enumerate(item_order)}
    sections = {}
    last_rank = -1
    for start, end, item, title in sized:
        if item not in rank:
            continue
        if rank[item] <= last_rank:
            continue  # out of order -> cross-reference, not a heading
        sections[item] = {"start": start, "title": title}
        last_rank = rank[item]

    if len(sections) < 2:
        return {}

    ordered = sorted(sections.items(), key=lambda kv: kv[1]["start"])
    result = {}
    for index, (item, meta) in enumerate(ordered):
        end = ordered[index + 1][1]["start"] if index + 1 < len(ordered) else len(text)
        result[item] = {"title": meta["title"], "text": text[meta["start"]:end].strip()}
    return result


def condense_risk_factors(text):
    """
    Shrinks Item 1A from ~150K chars to ~15K: every bold heading plus the
    first sentence beneath it. The headings carry nearly all the information.
    """
    lines = text.splitlines()
    output = []
    kept_headings = 0

    index = 0
    while index < len(lines):
        line = lines[index].strip()
        is_heading = line.startswith("**") and line.endswith("**") and 15 < len(line) < 250
        if is_heading:
            output.append("")
            output.append(line)
            kept_headings += 1
            body = []
            look = index + 1
            while look < len(lines) and len(body) < 6:
                candidate = lines[look].strip()
                if candidate.startswith("**"):
                    break
                if candidate:
                    body.append(candidate)
                look += 1
            if body:
                sentence = re.split(r"(?<=[.!?])\s+", " ".join(body))[0]
                output.append(sentence.strip())
            index = look
            continue
        index += 1

    # No bold headings: fall back to the first sentence of each paragraph
    # (one line per <p> in SEC HTML).
    if kept_headings < 3:
        output = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("|"):
                continue
            sentence = re.split(r"(?<=[.!?])\s+", stripped)[0]
            if len(sentence) > 60:
                output.append(sentence.strip())
        output.insert(0, "_(No bold risk headings found; first sentence of each paragraph kept.)_")

    condensed = "\n".join(output).strip()

    # Safety net: an empty risk-factors file reads as "no risks disclosed",
    # which is never true. Keep the original if condensation gutted it.
    if len(text) > 20000 and len(condensed) < 2000:
        return ("_Condensation produced too little to be trustworthy, so the full "
                "section is kept here._\n\n" + text)

    header = ("_Condensed. Risk headings kept verbatim with the first sentence of each. "
              "Full text is in the filing on EDGAR (see source)._\n")
    return header + condensed


def process_10k(text, meta, out_dir, skip_items=(), include_notes=True):
    """
    Writes the section files for one annual report (10-K; 20-F reuses this
    in Phase 3). skip_items drops sections already held from a newer filing;
    include_notes=False keeps the statements but not the notes.
    Returns the written-file records for the index.
    """
    written = []
    ticker, form = meta["ticker"], meta["form"]
    label = period_label(meta.get("report_date"), form)
    base_fields = _base_fields(meta)

    sections = split_by_item(text, TENK_ITEM_ORDER)
    if not sections:
        write_section(out_dir, f"{ticker}_{label}_{form}_Full.md",
                      dict(base_fields, section=f"{form} (unsplit)"),
                      f"_Section splitting failed on this {form}; full text kept._\n\n" + text,
                      written)
        return written

    # Many filers (NVIDIA among them) make Item 8 a one-line cross-reference
    # and put the statements under Item 15. Without this they would get no
    # financial statements and no notes at all.
    if "15" in sections:
        item8 = sections.get("8", {}).get("text", "")
        if len(item8) < MIN_SECTION_CHARS * 4:
            sections["8"] = {"title": "Financial Statements (filed under Item 15)",
                             "text": sections["15"]["text"]}

    mda_parts = []
    for item, (suffix, section_name, mode) in TENK_KEEP.items():
        if item not in sections or item in skip_items:
            continue
        body = sections[item]["text"]

        if mode == "condense":
            body = condense_risk_factors(body)
        elif mode == "verbatim_if_long":
            if len(body) < 2000:
                continue  # Item 3 is usually "none"; not worth a file
        elif mode == "append_mda":
            mda_parts.append(body)
            continue
        elif mode == "financials":
            body, notes = split_statements_and_notes(body)
            if notes and include_notes:
                write_section(out_dir, f"{ticker}_{label}_{form}_08N_Notes.md",
                              dict(base_fields, section="Item 8 - Notes (all)"),
                              NOTES_HEADER + notes, written)

        if item == "7":
            mda_parts.insert(0, body)
            continue

        write_section(out_dir, f"{ticker}_{label}_{form}_{suffix}.md",
                      dict(base_fields, section=section_name), body, written)

    if mda_parts:
        write_section(out_dir, f"{ticker}_{label}_{form}_07_MDA.md",
                      dict(base_fields, section="Item 7/7A - MD&A and market risk"),
                      maybe_condense_mda("\n\n".join(mda_parts)), written)
    return written


def _base_fields(meta):
    """Front-matter fields every section file of a filing shares."""
    return {
        "ticker": meta["ticker"],
        "company": meta.get("company", ""),
        "form": meta["form"],
        "period_end": meta.get("report_date", ""),
        "filed": meta.get("filed", ""),
        "accession": meta.get("accession", ""),
        "source": meta.get("url", ""),
    }
