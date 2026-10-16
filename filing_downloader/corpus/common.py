"""
Pieces shared by every splitter: file assembly, period labels, the
statements/notes split and the optional MD&A trim.
"""

import re

from ..config import CONDENSE_MDA

# Top line of every notes file. Tells the reader nothing was filtered, so a
# missing topic means the company did not disclose it, not that it was cut.
NOTES_HEADER = "_All notes to the financial statements, verbatim. Nothing filtered._\n\n"


def front_matter(fields):
    """Builds the YAML block that makes each file self-describing."""
    lines = ["---"]
    for key, value in fields.items():
        if value is None or value == "":
            continue
        text = str(value)
        if any(ch in text for ch in ':#"') or text.startswith(" "):
            text = '"' + text.replace('"', "'") + '"'
        lines.append(f"{key}: {text}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def period_label(report_date, form):
    """
    Unambiguous period label for filenames, from the period-end date.

    Deliberately NOT a guessed fiscal year: NVIDIA's year ending January 2025
    is "fiscal 2025", Target's year ending February 2025 is "fiscal 2024".
    No rule gets both right, and a wrong label means a wrong citation.
    """
    if not report_date or len(report_date) < 10:
        return "UnknownPeriod"
    prefix = "QE" if form == "10-Q" else "FYE"
    return f"{prefix}{report_date}"


def write_section(out_dir, filename, meta_fields, body, written):
    """Writes one corpus file and records it for the index."""
    path = out_dir / filename
    body = body.strip()
    path.write_text(front_matter(meta_fields) + body + "\n", encoding="utf-8")
    written.append({
        "filename": filename,
        "chars": len(body),  # same measure read_file_record uses on a refresh
        "section": meta_fields.get("section", ""),
        "form": meta_fields.get("form", ""),
        "period_end": meta_fields.get("period_end", ""),
        "filed": meta_fields.get("filed", ""),
        "source": meta_fields.get("source", ""),
        "accession": meta_fields.get("accession", ""),
    })


# Where the notes begin. Matches "Note 1.", "NOTE 1 -", "(1) Organization",
# "1. Summary of Significant Accounting Policies", and the header "Notes to
# Consolidated Financial Statements".
NOTES_START = re.compile(
    r"^\**\s*(notes?\s+to\s+(the\s+)?(consolidated\s+|condensed\s+)*"
    r"(unaudited\s+)?financial\s+statements"
    r"|note\s*1\b"
    r"|\(\s*1\s*\)\s*\w"
    r"|1\s*[\.\-]\s*(organization|summary|basis|description|nature)\b)",
    re.IGNORECASE,
)


def split_statements_and_notes(item_text):
    """
    Splits the financial statements section into (statements, notes).

    Every note is kept verbatim. Splitting at ONE point means the two files
    can never overlap or leave a gap. If the start of the notes cannot be
    found, notes comes back None and the statements keep everything.
    """
    lines = item_text.splitlines()
    for index, line in enumerate(lines):
        if not NOTES_START.match(line.strip()):
            continue
        statements = "\n".join(lines[:index]).strip()
        # A match this early is the section's own table of contents, not the
        # real start of the notes; keep looking.
        if len(statements) > 2000:
            return statements, "\n".join(lines[index:]).strip()
    return item_text, None


def condense_mda(text):
    """
    Optional MD&A trim, controlled by config.CONDENSE_MDA.

    Keeps every table and heading; trims running prose to its first two
    sentences, where management states the direction and the driver.
    """
    output = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            output.append("")
            continue
        if stripped.startswith("|") or stripped.startswith("**"):
            output.append(line)
            continue
        sentences = re.split(r"(?<=[.!?])\s+", stripped)
        output.append(" ".join(sentences[:2]))

    header = (
        "_MD&A prose trimmed to the first two sentences per paragraph; all "
        "tables and headings kept in full. Set CONDENSE_MDA=False in config.py "
        "for the complete text._\n"
    )
    return header + "\n".join(output).strip()


def maybe_condense_mda(text):
    """Applies the MD&A trim only when the config flag is on."""
    return condense_mda(text) if CONDENSE_MDA else text
