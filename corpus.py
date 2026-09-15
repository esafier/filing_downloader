"""
Claude Project corpus builder
=============================
Turns raw SEC filings + earnings transcripts into a SMALL, well-labelled set of
Markdown files that fit inside a Claude Project's knowledge budget.

Why this exists
---------------
The old pipeline wrote one giant .txt per filing (~600K chars for a 10-K). A
normal 2-year corpus came out around 730K tokens, which is far more than a
Claude Project can load into context. Claude then only sees retrieved fragments,
so it answers as if half the corpus doesn't exist.

This module fixes that four ways:
  1. Caps the corpus (2 annual, 4 quarterly, 1 proxy, 4 earnings releases,
     4 transcripts).
  2. Converts HTML tables into real Markdown tables, so financial statements
     stay readable instead of collapsing into a column of orphan numbers.
  3. Splits each filing into per-section files, so retrieval pulls MD&A when
     you ask about margins instead of dragging in 150K tokens of risk factors.
  4. Condenses the low-value sections (risk factors, boilerplate) and drops
     the worthless ones (exhibit lists, signature pages).

Output goes to its own folder and NEVER touches the raw Filings\\ archive.
"""

import re
import time
import requests
from datetime import date
from bs4 import BeautifulSoup

# ============================================================
# CONFIGURATION
# ============================================================

# How much of each document type ends up in the corpus.
# These are the caps the whole design depends on - raising them is what
# blows the token budget, so change them deliberately.
CORPUS_LIMITS = {
    "10-K": 2,
    "10-Q": 4,
    "DEF 14A": 1,
    "earnings releases": 4,
    "transcripts": 4,
}

# Folder name for the generated corpus. Kept separate from Filings\ so the
# raw archive is never mixed in with the upload set.
CORPUS_FOLDER_NAME = "Claude Project Knowledge"

SEC_HEADERS = {
    "User-Agent": "Ely Safier elymsafier@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

# A table wider than this is almost always page-layout scaffolding rather than
# data, and converting it produces unreadable noise. We leave those as text.
MAX_TABLE_COLS = 10

# A "section" shorter than this is a table-of-contents line or a cross
# reference, not a real section. Used to filter out false Item matches.
MIN_SECTION_CHARS = 500

# MD&A is kept verbatim by default, because it is the section an analyst
# actually reads and the one worth having in the company's own words.
#
# It is also, by a wide margin, the biggest thing left in the corpus - on a
# heavy filer it is around 70% of the total. If Claude is still missing
# content in your project, flip this to True: it keeps every table and every
# heading, and trims the prose to the first two sentences of each paragraph.
# That roughly halves the corpus. Set it back to False to get full text again.
CONDENSE_MDA = False


# ============================================================
# HTML -> MARKDOWN
# ============================================================

def _cell_text(cell):
    """Flatten one table cell into a single clean line."""
    text = cell.get_text(separator=" ", strip=True)
    # Pipes would break Markdown table syntax
    return text.replace("|", "/").strip()


def _table_to_markdown(table):
    """
    Converts one HTML <table> into a Markdown pipe table.

    SEC tables use rowspan/colspan heavily, so we expand them into a proper
    grid first - otherwise columns shift and numbers land under the wrong
    heading, which is worse than not converting at all.

    Returns None if this table shouldn't be converted (nested tables, too
    wide, or no real content). The caller then leaves it as plain text.
    """
    # Nested tables mean the layout is doing something we can't reliably
    # flatten. Bail out rather than produce a scrambled grid.
    if table.find("table") is not None:
        return None

    rows = table.find_all("tr")
    if not rows:
        return None

    # grid[row][col] = text. We fill it while honouring rowspan/colspan.
    grid = {}

    for row_index, row in enumerate(rows):
        col_index = 0
        for cell in row.find_all(["td", "th"]):
            # Skip forward past any cell already filled by a rowspan above us
            while (row_index, col_index) in grid:
                col_index += 1

            try:
                colspan = int(cell.get("colspan", 1))
                rowspan = int(cell.get("rowspan", 1))
            except (TypeError, ValueError):
                colspan = rowspan = 1

            # Guard against absurd spans in malformed filings
            colspan = max(1, min(colspan, MAX_TABLE_COLS))
            rowspan = max(1, min(rowspan, len(rows)))

            text = _cell_text(cell)

            for row_offset in range(rowspan):
                for col_offset in range(colspan):
                    # Only the top-left cell of a span carries the text;
                    # the rest are placeholders so the grid stays aligned
                    value = text if (row_offset == 0 and col_offset == 0) else ""
                    grid[(row_index + row_offset, col_index + col_offset)] = value

            col_index += colspan

    if not grid:
        return None

    max_row = max(r for r, _ in grid)
    max_col = max(c for _, c in grid)

    # Build the rectangular table
    table_rows = []
    for r in range(max_row + 1):
        table_rows.append([grid.get((r, c), "") for c in range(max_col + 1)])

    # Drop columns that are empty everywhere. SEC filings pad tables with
    # spacer columns holding "$" or nothing, which triples the width.
    keep_cols = [
        c for c in range(max_col + 1)
        if any(row[c].strip() not in ("", "$", ")", "(") for row in table_rows)
    ]
    if not keep_cols:
        return None
    table_rows = [[row[c] for c in keep_cols] for row in table_rows]

    # Drop rows that are entirely empty
    table_rows = [row for row in table_rows if any(cell.strip() for cell in row)]
    if len(table_rows) < 2:
        return None  # a single row isn't a table worth converting

    # A "table" with no digits anywhere is almost always layout scaffolding
    if not any(any(ch.isdigit() for ch in cell) for row in table_rows for cell in row):
        return None

    # Width check goes HERE, after the spacer columns are gone - not before.
    # SEC tables pad every figure with separate columns for "$", the number,
    # and ")", so a normal three-period table is 12+ raw columns. Checking
    # width first rejected essentially every real financial table, which is
    # what left the statements as unreadable one-number-per-line runs.
    width = len(table_rows[0])
    if width > MAX_TABLE_COLS:
        return None

    lines = []
    header = table_rows[0]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * width) + " |")
    for row in table_rows[1:]:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


# font-weight:bold, or any numeric weight of 600+ (600/700/800/900)
BOLD_STYLE = re.compile(r"font-weight\s*:\s*(bold|[6-9]00)", re.IGNORECASE)


def _is_bold_tag(tag):
    """
    True if this tag renders as bold.

    Covers both the old way (<b>, <strong>, <h1>-<h6>) and the way every
    modern SEC filing actually does it (<span style="font-weight:700">).
    """
    if tag.name in ("b", "strong", "h1", "h2", "h3", "h4", "h5", "h6"):
        return True
    style = tag.get("style")
    return bool(style and BOLD_STYLE.search(style))


def html_to_markdown(html_content):
    """
    Converts raw SEC filing HTML into Markdown.

    Two things this does that the old plain-text converter did not:
      - Tables become real Markdown tables (rows stay rows).
      - Bold/heading text is marked with ** ** so we can find section
        headings later. Without this, risk-factor headings are invisible.
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # Remove things that are never readable content
    for tag in soup.find_all(["style", "script", "xbrl", "ix:header", "noscript"]):
        tag.decompose()

    # Convert tables first, then REMOVE them from the tree so their text
    # doesn't get emitted a second time by get_text() below.
    table_markers = {}
    for index, table in enumerate(soup.find_all("table")):
        markdown = _table_to_markdown(table)
        if markdown is None:
            continue  # leave it in the tree; it'll come out as plain text
        marker = f"\n\n@@TABLE{index}@@\n\n"
        table_markers[f"@@TABLE{index}@@"] = markdown
        table.replace_with(marker)

    # Mark headings and bold runs so section detection has something to grip.
    # Only short runs - a whole bold paragraph isn't a heading.
    #
    # IMPORTANT: modern SEC filings almost never use <b> or <strong>. They use
    # <span style="font-weight:700">. Checking only for <b> finds zero headings
    # on a current 10-K, which silently breaks risk-factor condensation, note
    # selection and proxy splitting - they all rely on finding headings.
    for tag in list(soup.find_all(_is_bold_tag)):
        if tag.parent is None:
            continue  # already replaced as part of an ancestor
        text = tag.get_text(separator=" ", strip=True)
        if text and len(text) < 250:
            tag.replace_with(f"\n@@B@@{text}@@/B@@\n")

    raw_text = soup.get_text(separator="\n")

    # Tidy whitespace
    lines = [line.strip() for line in raw_text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Turn our bold markers into Markdown bold, on their own line
    text = re.sub(r"@@B@@(.*?)@@/B@@", lambda m: f"**{m.group(1).strip()}**", text, flags=re.S)

    # Put the converted tables back
    for marker, markdown in table_markers.items():
        text = text.replace(marker, "\n\n" + markdown + "\n\n")

    text = _strip_page_furniture(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_page_furniture(text):
    """
    Removes the running header/footer junk SEC filings repeat on every page.

    A 150-page 10-K carries 150 copies of "Table of Contents" plus a bare page
    number, and often the company name too. It is pure noise to a reader and
    it breaks up paragraphs mid-sentence.
    """
    cleaned = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower() in ("table of contents", "table of contents.", "index"):
            continue
        # A line that is nothing but a page number
        if re.fullmatch(r"\**\s*\d{1,4}\s*\**", stripped):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


# ============================================================
# SECTION SPLITTING
# ============================================================

# Canonical 10-K item order. Used to reject out-of-sequence matches, which
# are nearly always cross-references ("see Item 7") rather than real headings.
TENK_ITEM_ORDER = [
    "1", "1A", "1B", "1C", "2", "3", "4", "5", "6", "7", "7A", "8",
    "9", "9A", "9B", "9C", "10", "11", "12", "13", "14", "15", "16",
]

# 10-Q uses a completely different numbering, and it RESTARTS at Part II.
TENQ_PART1_ORDER = ["1", "2", "3", "4"]
TENQ_PART2_ORDER = ["1", "1A", "2", "3", "4", "5", "6"]

ITEM_PATTERN = re.compile(
    r"^\**\s*ITEM\s+(\d{1,2}[A-C]?)\s*[\.\:\-–—]?\s*(.*?)\**\s*$",
    re.IGNORECASE,
)

PART_PATTERN = re.compile(r"^\**\s*PART\s+(I{1,3}|IV)\b.*$", re.IGNORECASE)


def _find_item_candidates(text):
    """
    Finds every line that looks like an 'Item N.' heading.

    Returns a list of (char_offset, item_number, heading_text, line_index).
    These are CANDIDATES - the table of contents produces a full set of false
    positives, which the caller filters out by section length.
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


def _split_on_parts(text):
    """
    Splits a 10-Q into its Part I and Part II halves.

    Needed because 10-Q item numbers restart at Part II - without this,
    'Part II Item 1' (Legal Proceedings) gets mistaken for
    'Part I Item 1' (Financial Statements).
    """
    part_starts = []
    offset = 0
    for line in text.splitlines(keepends=True):
        match = PART_PATTERN.match(line.strip())
        if match:
            numeral = match.group(1).upper()
            part_starts.append((offset, numeral))
        offset += len(line)

    # Keep the LAST occurrence of each part marker that has real content after
    # it - earlier ones are table-of-contents entries.
    parts = {}
    for index, (start, numeral) in enumerate(part_starts):
        end = part_starts[index + 1][0] if index + 1 < len(part_starts) else len(text)
        if end - start >= MIN_SECTION_CHARS:
            parts.setdefault(numeral, (start, end))
            # a later, longer occurrence wins
            if end - start > parts[numeral][1] - parts[numeral][0]:
                parts[numeral] = (start, end)

    return {numeral: text[start:end] for numeral, (start, end) in parts.items()}


def split_by_item(text, item_order):
    """
    Splits filing text into {item_number: section_text}.

    The hard part is that 'Item 7' appears in three places: the table of
    contents, cross-references inside prose, and the actual heading. We filter
    those out with two rules:
      1. The heading must start a line (kills mid-sentence cross-references).
      2. The section must be at least MIN_SECTION_CHARS long (kills the table
         of contents, where each 'section' is one line).
    Then we enforce the canonical item order so anything still out of place
    is dropped.

    Returns {} if we can't find a believable set of sections - the caller then
    falls back to writing the filing whole rather than producing wrong splits.
    """
    candidates = _find_item_candidates(text)
    if not candidates:
        return {}

    # Rule 2: drop candidates whose section body is too short.
    sized = []
    for index, (start, item, title) in enumerate(candidates):
        end = candidates[index + 1][0] if index + 1 < len(candidates) else len(text)
        if end - start >= MIN_SECTION_CHARS:
            sized.append((start, end, item, title))

    if not sized:
        return {}

    # Enforce canonical order. Walk forward, only accepting an item that comes
    # later in the official sequence than the last one we accepted.
    rank = {item: position for position, item in enumerate(item_order)}
    sections = {}
    last_rank = -1
    for start, end, item, title in sized:
        if item not in rank:
            continue
        if rank[item] <= last_rank:
            continue  # out of order -> cross-reference, not a heading
        # This section runs until the next ACCEPTED heading, so recompute the
        # end as the start of the next accepted item (done in a second pass).
        sections[item] = {"start": start, "title": title}
        last_rank = rank[item]

    if len(sections) < 2:
        return {}

    # Second pass: each section ends where the next accepted one begins
    ordered = sorted(sections.items(), key=lambda kv: kv[1]["start"])
    result = {}
    for index, (item, meta) in enumerate(ordered):
        end = ordered[index + 1][1]["start"] if index + 1 < len(ordered) else len(text)
        result[item] = {
            "title": meta["title"],
            "text": text[meta["start"]:end].strip(),
        }
    return result


# ============================================================
# CONDENSATION
# ============================================================

def condense_risk_factors(text):
    """
    Shrinks Item 1A from ~150K chars to ~15K.

    Risk factor HEADINGS carry nearly all the information ("We depend on a
    small number of customers"); the paragraphs beneath them are templated
    legal hedging that says the same thing at length. So we keep every
    heading plus the first sentence under it.
    """
    lines = text.splitlines()
    output = []
    kept_headings = 0

    index = 0
    while index < len(lines):
        line = lines[index].strip()

        # A bold line that isn't a full paragraph is a risk heading
        is_heading = (
            line.startswith("**")
            and line.endswith("**")
            and 15 < len(line) < 250
        )

        if is_heading:
            output.append("")
            output.append(line)
            kept_headings += 1
            # Grab the first sentence of the body that follows
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
                paragraph = " ".join(body)
                sentence = re.split(r"(?<=[.!?])\s+", paragraph)[0]
                output.append(sentence.strip())
            index = look
            continue

        index += 1

    # If the filing had no bold headings we can't do heading-based
    # condensation. Fall back to the first sentence of each PARAGRAPH, where a
    # paragraph is one line - SEC HTML puts each <p> on its own line and does
    # not leave blank lines between them, so splitting on blank lines finds
    # almost nothing and throws the section away.
    if kept_headings < 3:
        output = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("|"):
                continue  # skip blanks and tables
            sentence = re.split(r"(?<=[.!?])\s+", stripped)[0]
            if len(sentence) > 60:
                output.append(sentence.strip())
        output.insert(0, "_(No bold risk headings found; first sentence of each paragraph kept.)_")

    condensed = "\n".join(output).strip()

    # Safety net: if condensation gutted the section, keep the original. An
    # empty risk-factors file is worse than a large one - it reads as "this
    # company disclosed no risks", which is never true.
    if len(text) > 20000 and len(condensed) < 2000:
        return (
            "_Condensation produced too little to be trustworthy, so the full "
            "section is kept here._\n\n" + text
        )

    header = (
        "_Condensed. Risk headings kept verbatim with the first sentence of each. "
        "Full text is in the raw archive._\n"
    )
    return header + condensed


def condense_mda(text):
    """
    Optional MD&A trim, controlled by CONDENSE_MDA.

    Keeps every table and every heading untouched - those carry the numbers
    and the structure. Trims running prose to its first two sentences, which
    is where management states the direction and the driver; the rest is
    usually the same point restated with qualifiers.
    """
    output = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            output.append("")
            continue
        # Tables and headings pass through untouched
        if stripped.startswith("|") or stripped.startswith("**"):
            output.append(line)
            continue
        sentences = re.split(r"(?<=[.!?])\s+", stripped)
        output.append(" ".join(sentences[:2]))

    header = (
        "_MD&A prose trimmed to the first two sentences per paragraph; all "
        "tables and headings kept in full. Set CONDENSE_MDA=False in corpus.py "
        "for the complete text._\n"
    )
    return header + "\n".join(output).strip()


# Top line of every notes file. Tells the reader nothing was filtered, so a
# missing topic means the company didn't disclose it - not that we cut it.
NOTES_HEADER = "_All notes to the financial statements, verbatim. Nothing filtered._\n\n"


# ============================================================
# PROXY (DEF 14A) SECTIONS
# ============================================================

# Proxies do not use 10-K item numbering at all, so they get their own
# keyword-driven splitter.
PROXY_SECTIONS = [
    ("CDA", re.compile(r"compensation\s+discussion\s+and\s+analysis", re.I)),
    ("SummaryComp", re.compile(r"summary\s+compensation\s+table", re.I)),
    ("PayVersusPerformance", re.compile(r"pay\s+versus\s+performance", re.I)),
    ("Ownership", re.compile(r"(security\s+ownership|beneficial\s+ownership)", re.I)),
    ("Governance", re.compile(r"(corporate\s+governance|board\s+of\s+directors)", re.I)),
]


def split_proxy(text):
    """
    Splits a DEF 14A into the five sections worth keeping.

    Everything else in a proxy - meeting logistics, voting mechanics, auditor
    ratification - is dropped. Those are the bulk of the page count and carry
    no analytical content.
    """
    lines = text.splitlines(keepends=True)

    # Find where each section starts. We take the LAST match that has enough
    # content after it, because the first matches are table-of-contents rows.
    hits = []
    offset = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("**") and len(stripped) < 250:
            for name, pattern in PROXY_SECTIONS:
                if pattern.search(stripped):
                    hits.append((offset, name))
                    break
        offset += len(line)

    if not hits:
        return {}

    sections = {}
    for index, (start, name) in enumerate(hits):
        end = hits[index + 1][0] if index + 1 < len(hits) else len(text)
        body = text[start:end].strip()
        if len(body) < MIN_SECTION_CHARS:
            continue
        # Later, longer occurrence beats an earlier table-of-contents hit
        if name not in sections or len(body) > len(sections[name]):
            sections[name] = body

    # Cap each section. A proxy section that runs past this is spilling into
    # the next topic because its end heading was missed - and proxy detail is
    # the least valuable content in the corpus, so we bound it hard.
    MAX_PROXY_SECTION_CHARS = 30000
    for name, body in list(sections.items()):
        if len(body) > MAX_PROXY_SECTION_CHARS:
            sections[name] = (body[:MAX_PROXY_SECTION_CHARS]
                              + "\n\n_[section truncated - see raw filing]_")

    return sections


# ============================================================
# TRANSCRIPTS
# ============================================================

QA_MARKERS = re.compile(
    r"(question[\-\s]and[\-\s]answer|q\s*&\s*a\s+session|"
    r"we\s+will\s+now\s+begin\s+the\s+question|"
    r"floor\s+is\s+now\s+open\s+for\s+questions|"
    r"(take|for)\s+(your|our)\s+first\s+question|"
    # The most common real-world phrasing by far - the operator opening the
    # queue. Without these the split silently never fires.
    r"(would\s+)?like\s+to\s+ask\s+a\s+question|"
    r"press\s+(star|\*)\s*(one|1)|"
    r"star\s+(one|1)\s+on\s+your\s+(telephone|touch)|"
    r"open\s+the\s+(call|floor|line)s?\s+(up\s+)?for\s+questions)",
    re.IGNORECASE,
)


def split_transcript(text):
    """
    Splits an earnings call into prepared remarks and Q&A.

    Worth doing because they answer different questions: prepared remarks give
    you the company's framing, Q&A gives you what analysts actually pressed on
    and where management got uncomfortable. Keeping them separate means a
    question about pushback retrieves the Q&A and not the scripted intro.
    """
    # Take the first marker that appears past the first fifth of the call.
    # The operator's OPENING almost always says "after the speakers' remarks
    # there will be a question and answer session" - matching that would cut
    # the transcript at line one and produce an empty prepared-remarks file.
    matches = list(QA_MARKERS.finditer(text))
    if not matches:
        return text, None

    # Try each candidate marker and take the first that splits the call
    # sensibly. A real earnings call is roughly 30-60% Q&A, so a "split" that
    # leaves 2KB of prepared remarks or a 700-byte Q&A file is a false match,
    # not a short Q&A - and shipping it would be actively misleading.
    total = len(text)
    for match in matches:
        line_start = text.rfind("\n", 0, match.start())
        cut = line_start if line_start != -1 else match.start()
        prepared = text[:cut].strip()
        qa = text[cut:].strip()

        if len(prepared) >= total * 0.20 and len(qa) >= total * 0.20:
            return prepared, qa

    # No believable split point - keep the call whole rather than invent one
    return text, None


# ============================================================
# DERIVED FINANCIALS (SEC XBRL Company Facts)
# ============================================================

# Each line item lists its XBRL tags in priority order. Companies switch tags
# between years (revenue alone has four common spellings), so a single tag
# name silently produces gaps in the time series.
INCOME_STATEMENT_CONCEPTS = [
    ("Revenue", ["RevenueFromContractWithCustomerExcludingAssessedTax",
                 "RevenueFromContractWithCustomerIncludingAssessedTax",
                 "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"]),
    ("Cost of revenue", ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]),
    ("Gross profit", ["GrossProfit"]),
    ("R&D expense", ["ResearchAndDevelopmentExpense"]),
    ("SG&A expense", ["SellingGeneralAndAdministrativeExpense",
                      "GeneralAndAdministrativeExpense"]),
    ("Operating income", ["OperatingIncomeLoss"]),
    ("Pre-tax income", ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]),
    ("Income tax expense", ["IncomeTaxExpenseBenefit"]),
    ("Net income", ["NetIncomeLoss", "ProfitLoss"]),
    ("Diluted EPS", ["EarningsPerShareDiluted"]),
    ("Diluted shares", ["WeightedAverageNumberOfDilutedSharesOutstanding"]),
]

BALANCE_SHEET_CONCEPTS = [
    ("Cash & equivalents", ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsAndShortTermInvestments"]),
    ("Total current assets", ["AssetsCurrent"]),
    ("Total assets", ["Assets"]),
    ("Total current liabilities", ["LiabilitiesCurrent"]),
    ("Long-term debt", ["LongTermDebtNoncurrent", "LongTermDebt"]),
    ("Total liabilities", ["Liabilities"]),
    ("Stockholders equity", ["StockholdersEquity",
                             "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
]

CASH_FLOW_CONCEPTS = [
    ("Operating cash flow", ["NetCashProvidedByUsedInOperatingActivities",
                             "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
    ("Capital expenditure", ["PaymentsToAcquirePropertyPlantAndEquipment",
                             "PaymentsToAcquireProductiveAssets"]),
    ("Share repurchases", ["PaymentsForRepurchaseOfCommonStock"]),
    ("Dividends paid", ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"]),
]


def fetch_company_facts(cik):
    """Downloads the SEC's XBRL Company Facts file for one company."""
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    try:
        response = requests.get(url, headers=SEC_HEADERS, timeout=60)
        if response.status_code != 200:
            print(f"  WARNING: Company Facts returned status {response.status_code}")
            return None
        return response.json()
    except requests.exceptions.RequestException as error:
        print(f"  WARNING: Company Facts request failed ({error})")
        return None


def _pick_observations(facts, tags, period_type):
    """
    Pulls one line item's time series out of Company Facts.

    Company Facts is messier than it looks. Three things have to be handled or
    the numbers come out wrong:
      - The same period appears many times (original + every restatement).
        We keep the LATEST FILED value, which is the restated one.
      - Annual and quarterly figures are mixed together and are only
        distinguishable by how long the period is.
      - Units vary; we keep USD (or USD/shares for per-share lines).

    Returns {period_end: value} plus the tag actually used, so the output can
    record which spelling of 'revenue' this company uses.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})

    # Merge ACROSS tags rather than taking the first one that has any data.
    # Companies switch tags mid-history (NetIncomeLoss -> ProfitLoss, and so
    # on), so stopping at the first hit leaves holes in the middle of the
    # series. Higher-priority tags win where both have a value; lower-priority
    # tags fill the gaps.
    merged = {}
    tags_hit = []
    unit_hit = None

    for tag in tags:
        if tag not in us_gaap:
            continue

        units = us_gaap[tag].get("units", {})
        unit_key = None
        for candidate in ("USD", "USD/shares", "shares"):
            if candidate in units:
                unit_key = candidate
                break
        if unit_key is None:
            continue

        # Collect, keeping the latest-filed value for each period
        best = {}
        for entry in units[unit_key]:
            end = entry.get("end")
            start = entry.get("start")
            filed = entry.get("filed", "")
            form = entry.get("form", "")
            value = entry.get("val")
            if end is None or value is None:
                continue

            if period_type == "instant":
                # Balance sheet items have no start date
                if start is not None:
                    continue
                if form not in ("10-K", "10-Q", "20-F"):
                    continue
                key = end
            else:
                if start is None:
                    continue
                days = _days_between(start, end)
                if period_type == "annual":
                    if not (330 <= days <= 400) or form not in ("10-K", "20-F"):
                        continue
                else:  # quarterly
                    if not (80 <= days <= 100) or form != "10-Q":
                        continue
                key = end

            previous = best.get(key)
            if previous is None or filed > previous["filed"]:
                best[key] = {"val": value, "filed": filed}

        if best:
            tags_hit.append(tag)
            if unit_hit is None:
                unit_hit = unit_key
            for period, entry in best.items():
                # setdefault: the first (highest-priority) tag to supply a
                # period keeps it; later tags only fill what's still missing
                merged.setdefault(period, entry["val"])

    if merged:
        return merged, " / ".join(tags_hit), unit_hit

    return {}, None, None


def _days_between(start, end):
    """Days between two YYYY-MM-DD strings, without importing datetime math."""
    from datetime import date
    try:
        y1, m1, d1 = (int(x) for x in start.split("-"))
        y2, m2, d2 = (int(x) for x in end.split("-"))
        return (date(y2, m2, d2) - date(y1, m1, d1)).days
    except (ValueError, TypeError):
        return -1


def _format_value(value, unit):
    """Formats a raw XBRL number for display."""
    if value is None:
        return ""
    if unit == "USD/shares":
        return f"{value:,.2f}"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:,.1f}"  # millions
    return f"{value:,.0f}"


def _build_table(facts, concepts, period_type, max_periods):
    """Builds one Markdown table (income statement / balance sheet / cash flow)."""
    series = {}
    tags_used = {}
    units_used = {}
    all_periods = set()

    for label, tags in concepts:
        values, tag, unit = _pick_observations(facts, tags, period_type)
        if not values:
            continue
        series[label] = values
        tags_used[label] = tag
        units_used[label] = unit
        all_periods.update(values.keys())

    if not series:
        return None, {}

    periods = sorted(all_periods, reverse=True)[:max_periods]
    periods = sorted(periods)  # oldest -> newest reads better left to right

    lines = []
    lines.append("| Line item (USD millions unless noted) | " + " | ".join(periods) + " |")
    lines.append("| --- | " + " | ".join(["---"] * len(periods)) + " |")
    for label, _ in concepts:
        if label not in series:
            continue
        unit = units_used[label]
        cells = [_format_value(series[label].get(p), unit) for p in periods]
        display = label
        if unit == "USD/shares":
            display = f"{label} (USD)"
        elif unit == "shares":
            display = f"{label} (millions)"
        lines.append(f"| {display} | " + " | ".join(cells) + " |")

    return "\n".join(lines), tags_used


def build_financials_timeseries(cik, ticker, company_name):
    """
    Builds the single most useful file in the corpus.

    One Markdown file with income statement, balance sheet and cash flow as
    clean tables across every available period. Sourced from SEC's XBRL
    Company Facts API, which is already structured - no HTML parsing, no
    mangled tables, no guessing.

    This is what stops Claude having to reconstruct financials out of prose.
    """
    facts = fetch_company_facts(cik)
    if facts is None:
        return None

    parts = [
        f"# {ticker} - Financial statements time series",
        "",
        f"Company: {company_name}  ",
        f"Source: SEC XBRL Company Facts (CIK {cik:010d})  ",
        "Values are as most recently restated by the company.",
        "",
        "> Segment revenue is deliberately NOT in this file. Segment data is",
        "> dimensional and Company Facts drops the dimensions, so there is no",
        "> reliable series to pull. See the 10-K notes file for segments.",
        "",
        "> The quarterly table has no Q4 column. That is correct, not a gap:",
        "> companies file a 10-K instead of a Q4 10-Q, so Q4 is only ever",
        "> reported as the annual figure less the first three quarters.",
        "",
    ]

    all_tags = {}

    for heading, concepts, period_type, limit in [
        ("Income statement - annual", INCOME_STATEMENT_CONCEPTS, "annual", 6),
        ("Income statement - quarterly", INCOME_STATEMENT_CONCEPTS, "quarterly", 12),
        ("Balance sheet", BALANCE_SHEET_CONCEPTS, "instant", 8),
        ("Cash flow - annual", CASH_FLOW_CONCEPTS, "annual", 6),
    ]:
        table, tags = _build_table(facts, concepts, period_type, limit)
        if table is None:
            continue
        parts.append(f"## {heading}")
        parts.append("")
        parts.append(table)
        parts.append("")
        all_tags.update(tags)

    # Record which XBRL tag each line came from. Tags change between years, so
    # if a number ever looks wrong this is the first place to check.
    if all_tags:
        parts.append("## XBRL tags used")
        parts.append("")
        parts.append("| Line item | Tag |")
        parts.append("| --- | --- |")
        for label, tag in sorted(all_tags.items()):
            parts.append(f"| {label} | `{tag}` |")
        parts.append("")

    return "\n".join(parts)


# ============================================================
# FILE ASSEMBLY
# ============================================================

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
    Builds an unambiguous period label for filenames.

    We deliberately do NOT guess a fiscal year from the calendar year. NVIDIA's
    year ending January 2025 is 'fiscal 2025'; Target's year ending February
    2025 is 'fiscal 2024'. There is no rule that gets both right, and a wrong
    label is worse than an ugly one - Claude would cite the wrong year.
    So we label from the period-end date itself, which cannot be wrong.
    """
    if not report_date or len(report_date) < 10:
        return "UnknownPeriod"
    prefix = "QE" if form == "10-Q" else "FYE"
    return f"{prefix}{report_date}"


# Which 10-K items make it into the corpus, and how each is treated.
# Everything not listed here is dropped: exhibit lists, signature pages,
# controls boilerplate, market-for-stock tables, and so on.
TENK_KEEP = {
    "1":  ("01_Business",         "Item 1 - Business",             "verbatim"),
    "1A": ("1A_RiskFactors",      "Item 1A - Risk Factors",        "condense"),
    "3":  ("03_LegalProceedings", "Item 3 - Legal Proceedings",    "verbatim_if_long"),
    "7":  ("07_MDA",              "Item 7 - MD&A",                 "verbatim"),
    "7A": ("07_MDA",              "Item 7A - Market Risk",         "append_mda"),
    "8":  ("08_Financials",       "Item 8 - Financial Statements", "financials"),
}

# 10-Q: only MD&A and the statements are worth keeping. The rest of a 10-Q
# repeats the 10-K almost verbatim and is pure token waste in a project.
TENQ_KEEP = {
    "1": ("Financials", "Part I Item 1 - Financial Statements", "verbatim"),
    "2": ("MDA",        "Part I Item 2 - MD&A",                 "verbatim"),
}


def _write(out_dir, filename, meta_fields, body, written):
    """Writes one corpus file and records it for the index."""
    path = out_dir / filename
    path.write_text(front_matter(meta_fields) + body.strip() + "\n", encoding="utf-8")
    written.append({
        "filename": filename,
        "chars": len(body),
        "section": meta_fields.get("section", ""),
        "form": meta_fields.get("form", ""),
        "period_end": meta_fields.get("period_end", ""),
        "filed": meta_fields.get("filed", "") or meta_fields.get("call_date", ""),
        "source": meta_fields.get("source", ""),
        "accession": meta_fields.get("accession", ""),
    })


# Where the notes begin. Matches "Note 1.", "NOTE 1 -", "(1) Organization",
# "1. Summary of Significant Accounting Policies", and the section header
# "Notes to Consolidated Financial Statements".
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

    Every note is kept, verbatim. An earlier version kept only notes matching
    a keyword list, which silently dropped stock comp, related parties,
    subsequent events, leases and taxes - the notes where accounting and
    governance problems actually show up.

    Splitting at ONE point means the two files can never overlap or leave a
    gap. If the start of the notes can't be found, notes comes back None and
    the statements file keeps everything, so nothing is ever lost.
    """
    lines = item_text.splitlines()
    for index, line in enumerate(lines):
        # Companies write the first note four different ways, and matching
        # only "Note 1" misses most of them.
        if not NOTES_START.match(line.strip()):
            continue
        statements = "\n".join(lines[:index]).strip()
        # A match this early is the section's own table of contents
        # ("Notes to Consolidated Financial Statements ... 58"), not the real
        # start of the notes - keep looking.
        if len(statements) > 2000:
            return statements, "\n".join(lines[index:]).strip()
    return item_text, None


def process_filing(html, meta, out_dir, skip_items=(), include_notes=True):
    """
    Turns one filing's HTML into a handful of section files.

    meta needs: ticker, company, form, report_date, filed, accession, url.
    skip_items lets the caller drop sections it already has from a newer
    filing - Item 1 (Business) is nearly identical year to year, so keeping
    two copies costs ~25K tokens and adds nothing.
    include_notes=False keeps the statements but drops the notes, for older
    filings whose notes a newer 10-K already repeats.

    Returns a list of written-file records for the index.
    """
    written = []
    ticker = meta["ticker"]
    form = meta["form"]
    label = period_label(meta.get("report_date"), form)

    text = html_to_markdown(html)

    base_fields = {
        "ticker": ticker,
        "company": meta.get("company", ""),
        "form": form,
        "period_end": meta.get("report_date", ""),
        "filed": meta.get("filed", ""),
        "accession": meta.get("accession", ""),
        "source": meta.get("url", ""),
    }

    # ---- DEF 14A: keyword-driven, not item-driven --------------------
    if form == "DEF 14A":
        sections = split_proxy(text)
        if not sections:
            _write(out_dir, f"{ticker}_{label}_DEF14A_Full.md",
                   dict(base_fields, section="Proxy statement (unsplit)"),
                   "_Section splitting failed on this proxy; full text kept._\n\n" + text,
                   written)
            return written
        for name, body in sections.items():
            _write(out_dir, f"{ticker}_{label}_DEF14A_{name}.md",
                   dict(base_fields, section=name), body, written)
        return written

    # ---- 10-Q: split into parts first, then items --------------------
    if form == "10-Q":
        parts = _split_on_parts(text)
        part_one = parts.get("I", text)
        sections = split_by_item(part_one, TENQ_PART1_ORDER)
        if not sections:
            _write(out_dir, f"{ticker}_{label}_10-Q_Full.md",
                   dict(base_fields, section="10-Q (unsplit)"),
                   "_Section splitting failed on this 10-Q; full text kept._\n\n" + text,
                   written)
            return written
        for item, (suffix, section_name, _mode) in TENQ_KEEP.items():
            if item not in sections:
                continue
            body = sections[item]["text"]
            # Part I Item 1 carries the statements PLUS every note. Same
            # treatment as the 10-K: statements in one file, all notes in another.
            if item == "1":
                body, notes = split_statements_and_notes(body)
                if notes and include_notes:
                    _write(out_dir, f"{ticker}_{label}_10-Q_Notes.md",
                           dict(base_fields, section="Part I Item 1 - Notes (all)"),
                           NOTES_HEADER + notes, written)
            elif item == "2" and CONDENSE_MDA:
                body = condense_mda(body)
            _write(out_dir, f"{ticker}_{label}_10-Q_{suffix}.md",
                   dict(base_fields, section=section_name), body, written)
        return written

    # ---- 10-K / 20-F -------------------------------------------------
    sections = split_by_item(text, TENK_ITEM_ORDER)
    if not sections:
        _write(out_dir, f"{ticker}_{label}_{form}_Full.md",
               dict(base_fields, section=f"{form} (unsplit)"),
               f"_Section splitting failed on this {form}; full text kept._\n\n" + text,
               written)
        return written

    # Many filers (NVIDIA among them) make Item 8 a one-line cross-reference
    # and put the actual financial statements under Item 15, "Exhibits and
    # Financial Statement Schedules". Without this, those companies get no
    # financial statements and no notes at all in the corpus.
    if "15" in sections:
        item8 = sections.get("8", {}).get("text", "")
        if len(item8) < MIN_SECTION_CHARS * 4:
            sections["8"] = {
                "title": "Financial Statements (filed under Item 15)",
                "text": sections["15"]["text"],
            }

    mda_parts = []
    for item, (suffix, section_name, mode) in TENK_KEEP.items():
        if item not in sections or item in skip_items:
            continue
        body = sections[item]["text"]

        if mode == "condense":
            body = condense_risk_factors(body)
        elif mode == "verbatim_if_long":
            # Item 3 is usually "none" or one sentence - not worth a file
            if len(body) < 2000:
                continue
        elif mode == "append_mda":
            mda_parts.append(body)
            continue
        elif mode == "financials":
            # Item 8 splits into the statements plus every note
            body, notes = split_statements_and_notes(body)
            if notes and include_notes:
                _write(out_dir, f"{ticker}_{label}_{form}_08N_Notes.md",
                       dict(base_fields, section="Item 8 - Notes (all)"),
                       NOTES_HEADER + notes, written)

        if item == "7":
            mda_parts.insert(0, body)
            continue

        _write(out_dir, f"{ticker}_{label}_{form}_{suffix}.md",
               dict(base_fields, section=section_name), body, written)

    if mda_parts:
        mda_body = "\n\n".join(mda_parts)
        if CONDENSE_MDA:
            mda_body = condense_mda(mda_body)
        _write(out_dir, f"{ticker}_{label}_{form}_07_MDA.md",
               dict(base_fields, section="Item 7/7A - MD&A and market risk"),
               mda_body, written)

    return written


def process_earnings_release(html, meta, out_dir):
    """
    Writes one earnings press release (8-K exhibit 99.1) as a single file.

    Kept whole: releases are short, and the KPI tables and non-GAAP
    reconciliations are the reason to have them at all.

    meta needs: ticker, company, filed, accession, url.
    """
    written = []
    body = html_to_markdown(html)
    # EDGAR wraps exhibits in a few lines of filing metadata ("EX-99.1",
    # the file name, "Document") that land at the very top. Peel them off.
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
        # 8-Ks carry no fiscal period, and guessing it from the date is how
        # wrong labels happen. The quarter is stated in the release's headline.
        "period_end": f"see headline (released {meta['filed']})",
        "filed": meta.get("filed", ""),
        "accession": meta.get("accession", ""),
        "source": meta.get("url", ""),
        "section": "Earnings press release (full)",
    }
    _write(out_dir, f"{ticker}_REL{meta['filed']}_EarningsRelease.md",
           fields, body, written)
    return written


def process_transcript(text, meta, out_dir):
    """Splits one earnings call into prepared remarks and Q&A files."""
    written = []
    ticker = meta["ticker"]
    year = meta["year"]
    quarter = meta["quarter"]
    label = f"{year}Q{quarter}"

    base_fields = {
        "ticker": ticker,
        "company": meta.get("company", ""),
        "form": "Earnings call transcript",
        "period_end": f"Q{quarter} {year}",
        "call_date": meta.get("date", ""),
        "source": "API Ninjas earnings transcript",
    }

    prepared, qa = split_transcript(text)

    if qa is None:
        _write(out_dir, f"{ticker}_{label}_Transcript.md",
               dict(base_fields, section="Full call"), prepared, written)
        return written

    _write(out_dir, f"{ticker}_{label}_Transcript_Prepared.md",
           dict(base_fields, section="Prepared remarks"), prepared, written)
    _write(out_dir, f"{ticker}_{label}_Transcript_QA.md",
           dict(base_fields, section="Q&A"), qa, written)
    return written


def write_index(out_dir, ticker, company_name, written, limits_used):
    """
    Writes 00_INDEX.md - the first thing Claude should read.

    Tells it what's in the corpus, what period each file covers, and what was
    deliberately left out, so it doesn't assume a gap means the company never
    disclosed something.
    """
    total_chars = sum(item["chars"] for item in written)
    approx_tokens = total_chars // 4
    # Newest document date across the corpus. Anything the company released
    # after this is not here - that's the cue to check a live source.
    dates = [item.get("filed", "") for item in written]
    newest = max((d for d in dates if d[:4].isdigit()), default="unknown")

    # Built from the files actually written, not from a description of the
    # rule - an index that describes a filter drifts away from the filter.
    notes_files = [item for item in written if "Notes (all)" in item["section"]]
    notes_periods = ", ".join(
        f"{item['form']} {item['period_end']}"
        for item in sorted(notes_files, key=lambda i: i["period_end"])
    )
    notes_line = (
        f"- **Full notes, verbatim, for the newest reports only:** {notes_periods or 'none'}.\n"
        "  Older 10-Ks and 10-Qs keep their statements but not their notes - the\n"
        "  newest 10-K repeats the prior year. Within those `Notes` files nothing is\n"
        "  filtered, so a topic missing there wasn't disclosed."
    )

    lines = [
        f"# {ticker} - {company_name}: corpus index",
        "",
        "Read this first. It lists every document in this project and what it covers.",
        "",
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
        f"- Earnings call transcripts: {limits_used.get('transcripts', 0)}",
        "",
        "## Files",
        "",
        "| File | Form | Period | Section |",
        "| --- | --- | --- | --- |",
    ]

    for item in sorted(written, key=lambda i: i["filename"]):
        lines.append(
            f"| `{item['filename']}` | {item['form']} | "
            f"{item['period_end']} | {item['section']} |"
        )

    lines += [
        "",
        "## What was deliberately left out",
        "",
        "- **Risk factors are condensed** to their headings plus the first sentence",
        "  of each. The headings carry the substance; the paragraphs beneath are",
        "  templated legal hedging.",
        notes_line,
        "- **Business (Item 1) is kept from the newest 10-K only**; it barely",
        "  changes year to year.",
        "- **10-Qs keep only MD&A, the financial statements and their notes** - the",
        "  rest repeats the 10-K nearly verbatim.",
        "- **Proxy keeps comp, ownership and governance sections**; the",
        "  meeting-logistics half is dropped.",
        "- **Dropped entirely:** exhibit indexes, signature pages, auditor consents,",
        "  cover-page checkboxes, internal-controls boilerplate, and 10-K items not",
        "  listed above (properties, mine safety, market for equity, etc.).",
        "",
        "If you need something that was cut, the full filings are archived separately",
        "under the Filings folder and can be added back.",
        "",
        "## Not included - use Quartr (or another live source) for these",
        "",
        "- Earnings slide decks and investor presentations",
        "- Conference, fireside chat and investor day transcripts",
        "- Anything released after the as-of date above",
        "- Other 8-Ks (deals, executive changes, financings) and Form 4 insider filings",
        "",
    ]

    (out_dir / f"{ticker}_00_INDEX.md").write_text("\n".join(lines), encoding="utf-8")
