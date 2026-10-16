# Filing Downloader

Builds one research folder per ticker under `OneDrive\Tickers`, with a compact
Markdown corpus of SEC filings. Each ticker folder is connected live to a
Claude (or ChatGPT) project; the project also has the Quartr MCP, which covers
everything the corpus deliberately leaves out (transcripts, slides, 8-Ks,
anything newer than the corpus).

## Running it

Double-click `Run_Filing_Downloader.bat` for the interactive menu, or:

```bash
python downloader.py add NVDA --full
```

```bash
python downloader.py add XYZ --light
```

```bash
python downloader.py add NVDA --full --sections 10-K,DEF14A --years 4
```

```bash
python downloader.py rebuild NVDA
```

```bash
python downloader.py list
```

Set `TICKERS_ROOT` to point the output somewhere else (used by the tests and
golden runs). No API keys are needed; everything comes from SEC EDGAR.

## Tiers

| Tier | What you get | For |
| --- | --- | --- |
| `light` | folder, `_Links.md`, XBRL financials time series, index routing everything else to Quartr | watchlist names you are not deep in |
| `full` | the above plus 10-K / 10-Q sections, proxy sections, earnings releases | names you research |

A ticker can start light and be upgraded later with `add TICKER --full`.

## Folder convention

```
Tickers\
  NVDA - NVIDIA Corp\
    _ticker.json                 profile: tier, sections, years, last run
    _Links.md
    Claude Project Knowledge\    the corpus (connect this folder's parent to the project)
      NVDA_00_INDEX.md           read first: files, as-of date, what is in Quartr instead
      NVDA_00_Financials_Timeseries.md
      NVDA_FYE2026-01-25_10-K_07_MDA.md ...
    Models\  Memos & Theses\  Earnings\Prep\  Earnings\Reaction\
```

Older ticker folders may still carry a `Filings\` folder of raw text from the
retired raw-archive mode. It is never touched or recreated.

## What the corpus contains (full tier)

Caps are by count (`config.CORPUS_LIMITS`): 2 annual reports, 4 quarterlies,
1 proxy, 4 earnings releases. Each filing is split by section into its own
Markdown file with YAML front matter (ticker, form, period end, filed,
accession, source URL). Risk factors are condensed to headings plus first
sentence; MD&A is verbatim; notes are kept in full for the newest reports.

## Source routing (what the project should do)

1. Corpus first: index, financials, MD&A, notes, proxy.
2. Quartr for transcripts, slides, other 8-Ks, peers, anything after the
   as-of date.
3. Exa or web for the outside view.

`claude_project_instructions.md` is the prompt to paste into the project.

## Development

```bash
python -m pytest -q
```

Package layout: `filing_downloader/` with `edgar.py` (SEC client),
`profile.py`, `scaffold.py`, `cli.py`, and `corpus/` (HTML conversion,
splitters, financials, index, build driver). `downloader.py` is a thin shim.

Roadmap: Phase 2 incremental `refresh` + Task Scheduler job + per-folder
`RESEARCH_GUIDE.md`; Phase 3 foreign filers (20-F, 40-F, 6-K); Phase 4
amendments, broader 8-K coverage, keep-original-HTML option.
