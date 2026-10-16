"""Item splitting and the statements/notes split on a synthetic 10-K."""

from filing_downloader.corpus.split_10k import split_by_item, TENK_ITEM_ORDER, process_10k
from filing_downloader.corpus.common import split_statements_and_notes

FILLER = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 12 + "\n") * 2  # > 500 chars


def _fake_10k():
    return "\n".join([
        # Table of contents: short "sections" that must be ignored
        "**Item 1. Business**",
        "**Item 1A. Risk Factors**",
        "**Item 7. MD&A**",
        "**Item 8. Financial Statements**",
        "",
        "**Item 1. Business**", FILLER,
        "**Item 1A. Risk Factors**",
        "**We depend on a small number of customers.**",
        "Losing one would hurt. More text here that is not needed.",
        FILLER,
        "**Item 7. Management's Discussion and Analysis**", FILLER,
        "See Item 8 for details.",  # cross-reference mid-prose: not a heading
        "**Item 8. Financial Statements and Supplementary Data**",
        "Consolidated Statements of Income", FILLER, FILLER, FILLER,
        "**Notes to Consolidated Financial Statements**",
        "**Note 1 - Organization**", FILLER,
        "**Item 9. Changes in Accountants**", FILLER,
    ])


def test_split_by_item_ignores_toc_and_cross_references():
    sections = split_by_item(_fake_10k(), TENK_ITEM_ORDER)
    assert set(sections) >= {"1", "1A", "7", "8", "9"}
    assert sections["7"]["text"].startswith("**Item 7.")
    assert "Consolidated Statements" in sections["8"]["text"]
    assert "Consolidated Statements" not in sections["7"]["text"]


def test_statements_and_notes_split_at_one_point():
    sections = split_by_item(_fake_10k(), TENK_ITEM_ORDER)
    statements, notes = split_statements_and_notes(sections["8"]["text"])
    assert notes is not None
    assert notes.startswith("**Notes to Consolidated")
    assert "Note 1" not in statements


def test_process_10k_writes_expected_files(tmp_path):
    meta = {"ticker": "TEST", "company": "Test Co", "form": "10-K",
            "report_date": "2025-12-31", "filed": "2026-02-15",
            "accession": "0000000000-26-000001", "url": "https://example.test/10k.htm"}
    written = process_10k(_fake_10k(), meta, tmp_path)
    names = sorted(item["filename"] for item in written)
    assert names == [
        "TEST_FYE2025-12-31_10-K_01_Business.md",
        "TEST_FYE2025-12-31_10-K_07_MDA.md",
        "TEST_FYE2025-12-31_10-K_08N_Notes.md",
        "TEST_FYE2025-12-31_10-K_08_Financials.md",
        "TEST_FYE2025-12-31_10-K_1A_RiskFactors.md",
    ]
    risk = (tmp_path / "TEST_FYE2025-12-31_10-K_1A_RiskFactors.md").read_text(encoding="utf-8")
    assert risk.startswith("---\nticker: TEST\n")  # YAML front matter
    assert "accession: 0000000000-26-000001" in risk


def test_process_10k_skip_items_and_no_notes(tmp_path):
    meta = {"ticker": "TEST", "company": "Test Co", "form": "10-K",
            "report_date": "2024-12-31", "filed": "2025-02-15", "accession": "", "url": ""}
    written = process_10k(_fake_10k(), meta, tmp_path, skip_items=("1",), include_notes=False)
    names = {item["filename"] for item in written}
    assert not any("01_Business" in n for n in names)
    assert not any("08N_Notes" in n for n in names)
