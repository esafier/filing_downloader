# Claude setup for ticker projects

How the projects get their instructions: in a claude.ai project, "Add
context" > OneDrive > pick the ticker folder (e.g. `CBOE - Cboe Global
Markets, Inc`, not its `Claude Project Knowledge` subfolder, so
`RESEARCH_GUIDE.md` is included). Everything the project needs to know (role,
sources, Open Brain, how to analyze, style, citations, where to save) lives in
that `RESEARCH_GUIDE.md`, and the weekly refresh keeps it current. One account
preference tells Claude to read it, so no project needs pasted instructions.

## 1. Account preference (once, covers every project)

claude.ai > Settings > Profile > "Instructions for Claude". Add:

```
When a project's files include a RESEARCH_GUIDE.md (from a ticker folder I added as context), read it before answering anything in that project and follow it. It is my standing instructions for that project.
```

This applies to every conversation, so existing and new projects need nothing
else. Test it once: in a project with a ticker folder attached, ask "what are
your instructions here?" and check that it read the guide.

## 2. Project instructions (optional backup, same text for every project)

If a project ever ignores the preference, paste this into its instructions.
It has no ticker in it, so it never needs editing:

```
You are my buy-side research analyst for the company in this project's ticker folder. Before answering anything, read RESEARCH_GUIDE.md in the project's files and follow it: it holds your role, where to look (local corpus, Open Brain, Quartr, web), how to analyze, style, citations and where to save outputs. If you cannot find it, tell me.
```

## 3. Connectors

Turn on the Quartr, Exa and Open Brain connectors; the guide tells Claude to
say so in one line if Open Brain is missing.
