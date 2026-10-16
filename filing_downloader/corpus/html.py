"""
HTML -> Markdown for SEC filings.

Two things this does that a plain get_text() does not:
  - Tables become real Markdown tables (rows stay rows).
  - Bold/heading text is marked with ** ** so section detection has
    something to grip. Without this, risk-factor headings are invisible.
"""

import re
from bs4 import BeautifulSoup

from ..config import MAX_TABLE_COLS


def _cell_text(cell):
    """Flatten one table cell into a single clean line."""
    text = cell.get_text(separator=" ", strip=True)
    # Pipes would break Markdown table syntax
    return text.replace("|", "/").strip()


def table_to_markdown(table):
    """
    Converts one HTML <table> into a Markdown pipe table.

    SEC tables use rowspan/colspan heavily, so they are expanded into a
    proper grid first; otherwise columns shift and numbers land under the
    wrong heading, which is worse than not converting at all.

    Returns None if this table should not be converted (nested tables, too
    wide, no real content). The caller then leaves it as plain text.
    """
    if table.find("table") is not None:
        return None  # nested layout we cannot flatten reliably

    rows = table.find_all("tr")
    if not rows:
        return None

    grid = {}  # grid[(row, col)] = text, honouring rowspan/colspan
    for row_index, row in enumerate(rows):
        col_index = 0
        for cell in row.find_all(["td", "th"]):
            while (row_index, col_index) in grid:
                col_index += 1  # skip cells filled by a rowspan above
            try:
                colspan = int(cell.get("colspan", 1))
                rowspan = int(cell.get("rowspan", 1))
            except (TypeError, ValueError):
                colspan = rowspan = 1
            colspan = max(1, min(colspan, MAX_TABLE_COLS))
            rowspan = max(1, min(rowspan, len(rows)))

            text = _cell_text(cell)
            for row_offset in range(rowspan):
                for col_offset in range(colspan):
                    value = text if (row_offset == 0 and col_offset == 0) else ""
                    grid[(row_index + row_offset, col_index + col_offset)] = value
            col_index += colspan

    if not grid:
        return None

    max_row = max(r for r, _ in grid)
    max_col = max(c for _, c in grid)
    table_rows = [[grid.get((r, c), "") for c in range(max_col + 1)] for r in range(max_row + 1)]

    # Drop columns that are empty everywhere. SEC filings pad tables with
    # spacer columns holding "$" or ")" which triples the width.
    keep_cols = [
        c for c in range(max_col + 1)
        if any(row[c].strip() not in ("", "$", ")", "(") for row in table_rows)
    ]
    if not keep_cols:
        return None
    table_rows = [[row[c] for c in keep_cols] for row in table_rows]
    table_rows = [row for row in table_rows if any(cell.strip() for cell in row)]
    if len(table_rows) < 2:
        return None

    # A "table" with no digits anywhere is almost always layout scaffolding
    if not any(any(ch.isdigit() for ch in cell) for row in table_rows for cell in row):
        return None

    # Width check AFTER spacer columns are gone. Checking first rejected
    # essentially every real financial table (see tasks/lessons.md).
    width = len(table_rows[0])
    if width > MAX_TABLE_COLS:
        return None

    lines = ["| " + " | ".join(table_rows[0]) + " |",
             "| " + " | ".join(["---"] * width) + " |"]
    for row in table_rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


# font-weight:bold, or any numeric weight of 600+
BOLD_STYLE = re.compile(r"font-weight\s*:\s*(bold|[6-9]00)", re.IGNORECASE)


def _is_bold_tag(tag):
    """
    True if this tag renders as bold. Modern SEC filings almost never use
    <b>; they use <span style="font-weight:700">.
    """
    if tag.name in ("b", "strong", "h1", "h2", "h3", "h4", "h5", "h6"):
        return True
    style = tag.get("style")
    return bool(style and BOLD_STYLE.search(style))


def html_to_markdown(html_content):
    """Converts raw SEC filing HTML into Markdown text."""
    soup = BeautifulSoup(html_content, "html.parser")

    for tag in soup.find_all(["style", "script", "xbrl", "ix:header", "noscript"]):
        tag.decompose()

    # Convert tables first, then REMOVE them from the tree so their text is
    # not emitted a second time by get_text() below.
    table_markers = {}
    for index, table in enumerate(soup.find_all("table")):
        markdown = table_to_markdown(table)
        if markdown is None:
            continue
        marker = f"\n\n@@TABLE{index}@@\n\n"
        table_markers[f"@@TABLE{index}@@"] = markdown
        table.replace_with(marker)

    # Mark short bold runs as headings. A whole bold paragraph is not one.
    for tag in list(soup.find_all(_is_bold_tag)):
        if tag.parent is None:
            continue  # already replaced as part of an ancestor
        text = tag.get_text(separator=" ", strip=True)
        if text and len(text) < 250:
            tag.replace_with(f"\n@@B@@{text}@@/B@@\n")

    raw_text = soup.get_text(separator="\n")
    lines = [line.strip() for line in raw_text.splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    text = re.sub(r"@@B@@(.*?)@@/B@@", lambda m: f"**{m.group(1).strip()}**", text, flags=re.S)

    for marker, markdown in table_markers.items():
        text = text.replace(marker, "\n\n" + markdown + "\n\n")

    text = strip_page_furniture(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_page_furniture(text):
    """
    Removes the running header/footer junk SEC filings repeat on every page:
    "Table of Contents" lines and bare page numbers.
    """
    cleaned = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower() in ("table of contents", "table of contents.", "index"):
            continue
        if re.fullmatch(r"\**\s*\d{1,4}\s*\**", stripped):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)
