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
python downloader.py refresh NVDA
```

```bash
python downloader.py refresh --all
```

```bash
python downloader.py list
```

Set `TICKERS_ROOT` to point the output somewhere else (used by the tests and
golden runs). No API keys are needed; everything comes from SEC EDGAR.

`rebuild` wipes the corpus folder and rebuilds it. `refresh` is incremental:
it compares EDGAR with the accession manifest in `_ticker.json`, downloads
only new filings, keeps existing section files under the same names, removes
the files of any filing that fell outside the caps, and regenerates the
financials time series and the index. Light-tier folders refresh only the
financials and the index. One line per ticker is appended to
`Tickers\_refresh_log.txt` (date, ticker, added, removed, errors, file names)
and printed at the end; one ticker failing never stops the others.

## Weekly refresh (Task Scheduler)

`Run_Weekly_Refresh.bat` runs `refresh --all` unattended (no pause) and
appends console output to `refresh_console.log` next to it. Register it once
from a normal (non-admin) PowerShell or Command Prompt:

```bash
schtasks /Create /TN "Filing Downloader Weekly Refresh" /TR "\"C:\Users\ely\Code\Filing_downloader\Run_Weekly_Refresh.bat\"" /SC WEEKLY /D SAT /ST 07:00
```

Check it with `schtasks /Query /TN "Filing Downloader Weekly Refresh"`, run
it now with `schtasks /Run /TN "..."`, remove it with `schtasks /Delete /TN
"..." /F`. The PC has to be awake at the scheduled time; if it often is not,
open Task Scheduler, find the task, and on the Settings tab tick "Run task as
soon as possible after a scheduled start is missed".

GUI alternative: Task Scheduler > Create Basic Task > name it > Weekly, pick a
day and time > Start a program > browse to `Run_Weekly_Refresh.bat` > Finish.

## RESEARCH_GUIDE.md

Every ticker folder carries a `RESEARCH_GUIDE.md`, and it is the project's
full set of instructions: role, where to start, a routing table (what to read
locally, what to ask Quartr, what to look up on the web), source order, how
to use my notes in Open Brain, how to analyze, style, the folder layout,
citation rules and where to write outputs. It is copied from the master
`Tickers\_RESEARCH_GUIDE.md` on every add, rebuild and refresh with the
ticker, company, tier and date filled in. Edit the master to change every
folder on the next refresh; the master is created from the template in
`filing_downloader/guide.py` if it is missing.

When the template gains a new `## ` section, the next add, rebuild or
refresh inserts it into the existing master (in template order) and prints
`Master guide: added ...`. Sections already in the master are never
touched, so hand edits survive. To switch a section off, keep its heading
and empty its body; deleting the heading brings it back. The index also lists the files
already in `Memos & Theses`, `Earnings\Prep`, `Earnings\Reaction` and
`Models` so a new conversation knows its prior work.

## Tiers

| Tier | What you get | For |
| --- | --- | --- |
| `light` | folder, `_Links.md`, XBRL financials time series, index routing everything else to Quartr | watchlist names you are not deep in |
| `full` | the above plus 10-K / 10-Q sections, proxy sections, earnings releases | names you research |

A ticker can start light and be upgraded later with `add TICKER --full`.

## Folder convention

```
Tickers\
  _RESEARCH_GUIDE.md           master guide, copied into every ticker folder
  _refresh_log.txt             one line per ticker per refresh
  NVDA - NVIDIA Corp\
    RESEARCH_GUIDE.md            routing: local vs Quartr vs web, where to write outputs
    _ticker.json                 profile: tier, sections, years, last run, accession manifest
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

Alongside these, my own notes and Current view in Open Brain (stock folder
`$ticker`), read first and treated as views, not facts.

`claude_project_instructions.md` has the one-time account preference that
points every project at its `RESEARCH_GUIDE.md`, so new projects need no
pasted instructions, plus an optional ticker-free backup prompt.

## Development

```bash
python -m pytest -q
```

Package layout: `filing_downloader/` with `edgar.py` (SEC client),
`profile.py`, `scaffold.py`, `cli.py`, and `corpus/` (HTML conversion,
splitters, financials, index, build driver). `downloader.py` is a thin shim.

Roadmap: Phase 3 foreign filers (20-F, 40-F, 6-K); Phase 4 amendments,
broader 8-K coverage, keep-original-HTML option.
