"""
Proxy (DEF 14A) splitting.

Proxies do not use 10-K item numbering, so they get a keyword-driven
splitter. Everything outside the five sections below (meeting logistics,
voting mechanics, auditor ratification) is dropped.
"""

import re

from ..config import MIN_SECTION_CHARS
from .common import period_label, write_section
from .split_10k import _base_fields

PROXY_SECTIONS = [
    ("CDA", re.compile(r"compensation\s+discussion\s+and\s+analysis", re.I)),
    ("SummaryComp", re.compile(r"summary\s+compensation\s+table", re.I)),
    ("PayVersusPerformance", re.compile(r"pay\s+versus\s+performance", re.I)),
    ("Ownership", re.compile(r"(security\s+ownership|beneficial\s+ownership)", re.I)),
    ("Governance", re.compile(r"(corporate\s+governance|board\s+of\s+directors)", re.I)),
]

# A proxy section that runs past this is spilling into the next topic
# because its end heading was missed. Proxy detail is the least valuable
# content in the corpus, so it is bounded hard.
MAX_PROXY_SECTION_CHARS = 30000


def split_proxy(text):
    """Splits a DEF 14A into {name: body} for the sections worth keeping."""
    lines = text.splitlines(keepends=True)

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

    for name, body in list(sections.items()):
        if len(body) > MAX_PROXY_SECTION_CHARS:
            sections[name] = body[:MAX_PROXY_SECTION_CHARS] + "\n\n_[section truncated - see source filing]_"
    return sections


def process_proxy(text, meta, out_dir):
    """Writes one file per proxy section (or the whole proxy if split fails)."""
    written = []
    ticker = meta["ticker"]
    label = period_label(meta.get("report_date"), meta["form"])
    base_fields = _base_fields(meta)

    sections = split_proxy(text)
    if not sections:
        write_section(out_dir, f"{ticker}_{label}_DEF14A_Full.md",
                      dict(base_fields, section="Proxy statement (unsplit)"),
                      "_Section splitting failed on this proxy; full text kept._\n\n" + text,
                      written)
        return written
    for name, body in sections.items():
        write_section(out_dir, f"{ticker}_{label}_DEF14A_{name}.md",
                      dict(base_fields, section=name), body, written)
    return written
