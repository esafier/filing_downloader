"""Profile round trip, section parsing and the scaffold."""

import pytest

from filing_downloader.profile import new_profile, save_profile, load_profile
from filing_downloader.cli import parse_section_list, build_parser
from filing_downloader.scaffold import (ensure_canonical_structure, resolve_company_folder,
                                        list_ticker_folders)
from filing_downloader.config import ALL_SECTIONS, LIGHT_SECTIONS, CANONICAL_SUBFOLDERS


def test_light_profile_ignores_sections():
    profile = new_profile("nvda", "NVIDIA Corp", 1045810, "light", sections=["10-K"])
    assert profile["ticker"] == "NVDA"
    assert profile["cik"] == "0001045810"
    assert profile["sections"] == LIGHT_SECTIONS


def test_full_profile_defaults_to_all_sections():
    profile = new_profile("NVDA", "NVIDIA Corp", 1045810, "full")
    assert profile["sections"] == ALL_SECTIONS
    assert profile["years_back"] == 3


def test_full_profile_rejects_empty_sections():
    with pytest.raises(ValueError):
        new_profile("NVDA", "NVIDIA Corp", 1045810, "full", sections=["bogus"])


def test_profile_round_trip(tmp_path):
    profile = new_profile("NVDA", "NVIDIA Corp", 1045810, "full", sections=["10-K", "XBRL"])
    save_profile(tmp_path, profile)
    assert load_profile(tmp_path) == profile


@pytest.mark.parametrize("text,expected", [
    ("all", ALL_SECTIONS),
    ("1,3,5", ["10-K", "DEF 14A", "XBRL"]),
    ("1 2", ["10-K", "10-Q"]),
    ("10-K,DEF14A", ["10-K", "DEF 14A"]),
    ("10k, 8k", ["10-K", "8-K"]),
    ("xbrl", ["XBRL"]),
    ("9", None),
    ("", None),
])
def test_parse_section_list(text, expected):
    assert parse_section_list(text) == expected


def test_parser_add_flags():
    args = build_parser().parse_args(["add", "nvda", "--light", "--years", "4"])
    assert args.ticker == "nvda" and args.light and args.years == 4


def test_scaffold_creates_structure_once(tmp_path):
    folder, existed = resolve_company_folder(tmp_path, "CROX", "Crocs, Inc.")
    assert not existed and folder.name == "CROX - Crocs, Inc"
    ensure_canonical_structure(folder, "CROX", "Crocs, Inc.", cik=1334036)
    for sub in CANONICAL_SUBFOLDERS:
        assert (folder / sub).is_dir()
    assert "CIK=1334036" in (folder / "_Links.md").read_text(encoding="utf-8")
    # No raw Filings\\ folders any more
    assert not (folder / "Filings").exists()

    # A second lookup with a different company name reuses the folder
    again, existed = resolve_company_folder(tmp_path, "CROX", "Crocs")
    assert existed and again == folder
    assert list_ticker_folders(tmp_path) == [folder]
