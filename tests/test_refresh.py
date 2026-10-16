"""Incremental refresh: accession diff, cap enforcement, guide, prior-work listing.

Everything runs against tmp_path with EDGAR stubbed out; no network.
"""

import json

import pytest

from filing_downloader.corpus import build
from filing_downloader.corpus.index import list_prior_work, write_index
from filing_downloader.config import CORPUS_FOLDER_NAME
from filing_downloader.profile import new_profile, accession_record
from filing_downloader.guide import (ensure_master_guide, render_guide, write_ticker_guide,
                                     MASTER_GUIDE_FILENAME, GUIDE_FILENAME, GUIDE_TEMPLATE)


def filing(form, accession, report_date, filed=None):
    return {"form": form, "accession": accession, "report_date": report_date,
            "filing_date": filed or report_date, "filing_url": f"https://sec.test/{accession}",
            "items": "2.02" if form == "8-K" else "", "cik": 1}


def fake_files(kind, f, ticker="TST"):
    """The section filenames a real splitter would produce for this filing."""
    if kind == "10-K":
        return [f"{ticker}_FYE{f['report_date']}_10-K_01_Business.md",
                f"{ticker}_FYE{f['report_date']}_10-K_07_MDA.md",
                f"{ticker}_FYE{f['report_date']}_10-K_08N_Notes.md"]
    if kind == "10-Q":
        return [f"{ticker}_QE{f['report_date']}_10-Q_MDA.md",
                f"{ticker}_QE{f['report_date']}_10-Q_Notes.md"]
    if kind == "DEF 14A":
        return [f"{ticker}_FYE{f['report_date']}_DEF14A_Comp.md"]
    return [f"{ticker}_REL{f['filing_date']}_EarningsRelease.md"]


def write_fake(out_dir, name, kind, f):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_text(
        f"---\nticker: TST\nform: {kind}\nperiod_end: {f['report_date']}\nfiled: {f['filing_date']}\n"
        f"accession: {f['accession']}\nsection: Section of {name}\n---\nbody of {name}\n",
        encoding="utf-8")


def stub_process_one(kind, f, ticker, company, out_dir, plan):
    """Writes the fake files and returns index records like the splitters do."""
    skip_items, include_notes = build.filing_flags(kind, f, plan)
    records = []
    for name in fake_files(kind, f):
        if name.endswith("_01_Business.md") and skip_items:
            continue
        if name.endswith(("_08N_Notes.md", "_10-Q_Notes.md")) and not include_notes:
            continue
        write_fake(out_dir, name, kind, f)
        records.append(build.read_file_record(out_dir / name))
    return records


@pytest.fixture
def ticker_folder(tmp_path, monkeypatch):
    """A TST ticker folder with EDGAR replaced by a plan we control."""
    folder = tmp_path / "TST - Test Co"
    folder.mkdir()
    state = {"plan": {}}
    monkeypatch.setattr(build, "plan_filings", lambda cik, sections, s, e: state["plan"])
    monkeypatch.setattr(build, "process_one", stub_process_one)
    monkeypatch.setattr(build, "_add_financials", lambda *a, **k: None)
    monkeypatch.setattr(build.time, "sleep", lambda s: None)
    state["folder"] = folder
    state["corpus"] = folder / CORPUS_FOLDER_NAME
    state["profile"] = new_profile("TST", "Test Co", 1, "full", ["10-K", "10-Q"])
    return state


# ------------------------------------------------------------------
# Accession diff
# ------------------------------------------------------------------

def test_diff_filings_splits_new_kept_dropped(tmp_path):
    old = filing("10-K", "0001-old", "2023-12-31")
    kept = filing("10-K", "0001-kept", "2024-12-31")
    new = filing("10-K", "0001-new", "2025-12-31")
    for name in fake_files("10-K", kept):
        write_fake(tmp_path, name, "10-K", kept)
    records = [accession_record("10-K", old, fake_files("10-K", old)),
               accession_record("10-K", kept, fake_files("10-K", kept))]

    new_list, kept_list, dropped = build.diff_filings({"10-K": [new, kept]}, records, tmp_path)

    assert [f["accession"] for _, f in new_list] == ["0001-new"]
    assert [f["accession"] for _, f, _ in kept_list] == ["0001-kept"]
    assert [r["accession"] for r in dropped] == ["0001-old"]


def test_diff_reprocesses_a_recorded_filing_whose_files_went_missing(tmp_path):
    f = filing("10-Q", "0002", "2025-06-30")
    records = [accession_record("10-Q", f, fake_files("10-Q", f))]  # nothing on disk
    new_list, kept_list, dropped = build.diff_filings({"10-Q": [f]}, records, tmp_path)
    assert len(new_list) == 1 and not kept_list and not dropped


def test_second_refresh_is_a_no_op(ticker_folder):
    k1 = filing("10-K", "K1", "2025-01-26")
    q1 = filing("10-Q", "Q1", "2025-04-27")
    ticker_folder["plan"] = {"10-K": [k1], "10-Q": [q1]}
    profile = ticker_folder["profile"]

    first = build.refresh_corpus(ticker_folder["folder"], profile)
    assert sorted(first["added"]) == sorted(fake_files("10-K", k1) + fake_files("10-Q", q1))
    assert {r["accession"] for r in profile["accessions"]} == {"K1", "Q1"}

    second = build.refresh_corpus(ticker_folder["folder"], profile)
    assert second == {"added": [], "removed": [], "errors": []}
    # the index is regenerated and lists the kept files
    index = (ticker_folder["corpus"] / "TST_00_INDEX.md").read_text(encoding="utf-8")
    assert "TST_FYE2025-01-26_10-K_07_MDA.md" in index


# ------------------------------------------------------------------
# Cap enforcement and supersession
# ------------------------------------------------------------------

def test_select_filings_caps_and_prefers_amendment():
    original = filing("10-K", "A", "2024-12-31", filed="2025-02-01")
    amended = filing("10-K/A", "A2", "2024-12-31", filed="2025-03-01")
    older = filing("10-K", "B", "2023-12-31", filed="2024-02-01")
    oldest = filing("10-K", "C", "2022-12-31", filed="2023-02-01")
    chosen = build.select_filings([original, amended, older, oldest], 2)
    assert [f["accession"] for f in chosen] == ["A2", "B"]


def test_refresh_removes_files_of_filing_outside_the_cap(ticker_folder):
    k_old = filing("10-K", "K-OLD", "2023-01-29")
    k_mid = filing("10-K", "K-MID", "2024-01-28")
    k_new = filing("10-K", "K-NEW", "2025-01-26")
    profile = ticker_folder["profile"]

    ticker_folder["plan"] = {"10-K": [k_mid, k_old]}
    build.refresh_corpus(ticker_folder["folder"], profile)
    corpus = ticker_folder["corpus"]
    assert (corpus / "TST_FYE2023-01-29_10-K_07_MDA.md").exists()
    assert (corpus / "TST_FYE2024-01-28_10-K_08N_Notes.md").exists()

    # A new 10-K arrives; the 2-annual cap pushes the oldest out
    ticker_folder["plan"] = {"10-K": [k_new, k_mid]}
    summary = build.refresh_corpus(ticker_folder["folder"], profile)

    assert not (corpus / "TST_FYE2023-01-29_10-K_07_MDA.md").exists()
    assert "TST_FYE2023-01-29_10-K_07_MDA.md" in summary["removed"]
    # the surviving older 10-K keeps its MD&A under the same name but loses
    # Business and Notes, which now come from the newest 10-K
    assert (corpus / "TST_FYE2024-01-28_10-K_07_MDA.md").exists()
    assert not (corpus / "TST_FYE2024-01-28_10-K_08N_Notes.md").exists()
    assert not (corpus / "TST_FYE2024-01-28_10-K_01_Business.md").exists()
    assert (corpus / "TST_FYE2025-01-26_10-K_08N_Notes.md").exists()
    assert {r["accession"] for r in profile["accessions"]} == {"K-NEW", "K-MID"}
    mid = next(r for r in profile["accessions"] if r["accession"] == "K-MID")
    assert mid["files"] == ["TST_FYE2024-01-28_10-K_07_MDA.md"]


def test_refresh_drops_10q_notes_once_a_newer_10k_lands(ticker_folder):
    q = filing("10-Q", "Q", "2024-10-27")
    k_old = filing("10-K", "K-OLD", "2024-01-28")
    k_new = filing("10-K", "K-NEW", "2025-01-26")
    profile = ticker_folder["profile"]
    ticker_folder["plan"] = {"10-K": [k_old], "10-Q": [q]}
    build.refresh_corpus(ticker_folder["folder"], profile)
    assert (ticker_folder["corpus"] / "TST_QE2024-10-27_10-Q_Notes.md").exists()

    ticker_folder["plan"] = {"10-K": [k_new, k_old], "10-Q": [q]}
    summary = build.refresh_corpus(ticker_folder["folder"], profile)
    assert "TST_QE2024-10-27_10-Q_Notes.md" in summary["removed"]
    assert (ticker_folder["corpus"] / "TST_QE2024-10-27_10-Q_MDA.md").exists()


def test_one_failing_filing_is_logged_and_retried(ticker_folder, monkeypatch):
    good = filing("10-Q", "GOOD", "2025-04-27")
    bad = filing("10-Q", "BAD", "2025-07-27")

    def flaky(kind, f, *args):
        if f["accession"] == "BAD":
            raise RuntimeError("status 503")
        return stub_process_one(kind, f, *args)

    monkeypatch.setattr(build, "process_one", flaky)
    ticker_folder["plan"] = {"10-Q": [bad, good]}
    summary = build.refresh_corpus(ticker_folder["folder"], ticker_folder["profile"])
    assert len(summary["errors"]) == 1 and "503" in summary["errors"][0]
    assert {r["accession"] for r in ticker_folder["profile"]["accessions"]} == {"GOOD"}


# ------------------------------------------------------------------
# Guide
# ------------------------------------------------------------------

def test_guide_master_created_once_and_substituted(tmp_path):
    master = ensure_master_guide(tmp_path)
    assert master.name == MASTER_GUIDE_FILENAME and "{ticker}" in master.read_text(encoding="utf-8")

    # edits to the master survive and flow into the copies
    master.write_text(master.read_text(encoding="utf-8") + "\nHouse rule for {ticker}.\n", encoding="utf-8")
    ensure_master_guide(tmp_path)
    folder = tmp_path / "NVDA - NVIDIA Corp"
    folder.mkdir()
    profile = new_profile("NVDA", "NVIDIA Corp", 1045810, "light")
    path = write_ticker_guide(tmp_path, folder, profile, as_of="2026-10-16")
    text = path.read_text(encoding="utf-8")
    assert path.name == GUIDE_FILENAME
    assert "House rule for NVDA." in text
    assert "Tier: **light**" in text and "2026-10-16" in text and "NVIDIA Corp" in text
    for token in ("{ticker}", "{company}", "{tier}", "{as_of}", "{corpus_folder}"):
        assert token not in text


def test_render_guide_leaves_unknown_braces_alone():
    assert render_guide("{ticker} {other}", "X", "Y", "full", "d") == "X {other}"
    assert "{corpus_folder}" in GUIDE_TEMPLATE


# ------------------------------------------------------------------
# Prior work in the index
# ------------------------------------------------------------------

def test_index_lists_prior_work(tmp_path):
    folder = tmp_path / "TST - Test Co"
    (folder / "Memos & Theses").mkdir(parents=True)
    (folder / "Earnings" / "Prep").mkdir(parents=True)
    (folder / "Models").mkdir()
    (folder / "Memos & Theses" / "TST_Thesis_2026-09-01.md").write_text("x", encoding="utf-8")
    (folder / "Earnings" / "Prep" / "TST_Prep_Q3.md").write_text("x", encoding="utf-8")
    (folder / "Models" / "~$lock.xlsx").write_text("x", encoding="utf-8")  # Office lock file

    names = [name for name, _ in list_prior_work(folder)]
    assert len(names) == 2
    assert "Memos & Theses\\TST_Thesis_2026-09-01.md" in names
    assert "Earnings\\Prep\\TST_Prep_Q3.md" in names
    assert not any("lock" in n for n in names)

    corpus = folder / CORPUS_FOLDER_NAME
    corpus.mkdir()
    write_index(corpus, "TST", "Test Co", [], {}, tier="light", company_folder=folder)
    index = (corpus / "TST_00_INDEX.md").read_text(encoding="utf-8")
    assert "## Prior work in this folder" in index
    assert "| `Memos & Theses\\TST_Thesis_2026-09-01.md` |" in index
    assert "RESEARCH_GUIDE.md" in index


def test_index_prior_work_empty(tmp_path):
    write_index(tmp_path, "TST", "Test Co", [], {}, tier="light", prior_work=[])
    assert "- (none yet)" in (tmp_path / "TST_00_INDEX.md").read_text(encoding="utf-8")


def test_accession_record_round_trips_through_json(tmp_path):
    rec = accession_record("8-K", filing("8-K", "R1", "2025-08-27"), ["TST_REL2025-08-27_EarningsRelease.md"])
    assert json.loads(json.dumps(rec)) == rec
    assert rec["form"] == "8-K" and rec["filed"] == "2025-08-27"
