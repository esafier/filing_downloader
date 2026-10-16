"""
Per-ticker profile: <TICKER - Name>/_ticker.json

Records what this ticker is and how its folder should be built, so a rebuild
or a scheduled refresh never has to ask. Two tiers:

  light  folder + links + financials time series + index routing everything
         else to Quartr. For names on the watchlist you are not deep in.
  full   the corpus: filing sections, proxy, releases, financials.

"accessions" is the manifest the weekly refresh diffs against: one record
per filing in the corpus, {accession, form, report_date, filed, files},
where files are the section files that filing produced. A filing whose
accession is recorded and whose files are present is never re-downloaded.
"""

import json
from datetime import date

from .config import PROFILE_FILENAME, ALL_SECTIONS, LIGHT_SECTIONS, DEFAULT_YEARS_BACK

TIERS = ("light", "full")


def new_profile(ticker, company, cik, tier, sections=None, years_back=DEFAULT_YEARS_BACK):
    """Builds a fresh profile dict. Light tier ignores 'sections'."""
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}, got {tier!r}")
    if tier == "light":
        sections = list(LIGHT_SECTIONS)
    else:
        sections = [s for s in (sections or ALL_SECTIONS) if s in ALL_SECTIONS]
        if not sections:
            raise ValueError("a full profile needs at least one section")
    today = date.today().isoformat()
    return {
        "ticker": ticker.upper(),
        "company": company,
        "cik": f"{cik:010d}" if isinstance(cik, int) else str(cik),
        "tier": tier,
        "filer_type": "us",          # Phase 3: "foreign" (20-F/6-K) or "canada" (40-F)
        "sections": sections,
        "years_back": int(years_back),
        "created": today,
        "last_run": None,
        "accessions": [],            # filled by build/refresh, see accession_record
    }


def accession_record(kind, filing, files):
    """One manifest entry: which corpus files came from which filing."""
    return {
        "accession": filing["accession"],
        "form": kind,
        "report_date": filing.get("report_date", ""),
        "filed": filing.get("filing_date", ""),
        "files": list(files),
    }


def load_profile(company_folder):
    """Reads the profile, or returns None if the folder has none yet."""
    path = company_folder / PROFILE_FILENAME
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_profile(company_folder, profile):
    """Writes the profile. Pretty-printed so it is easy to edit by hand."""
    path = company_folder / PROFILE_FILENAME
    path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    return path


def mark_run(profile):
    """Stamps today's date as the last run."""
    profile["last_run"] = date.today().isoformat()
    return profile
