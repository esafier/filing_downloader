# Claude setup for ticker projects

Everything the project needs to know (role, sources, Open Brain, how to
analyze, style, citations, where to save) lives in `RESEARCH_GUIDE.md` in each
ticker folder. The weekly refresh keeps that file current, so the text below
never has to change and never has to be re-pasted.

## 1. Account preference (once, covers every project)

claude.ai > Settings > Profile > "What personal preferences should Claude
consider in responses?" Add:

```
When a project has a connected folder containing RESEARCH_GUIDE.md, read that file before answering anything in the project and follow it. It is my standing instructions for that project.
```

This applies to every conversation, so new projects need no instructions at
all. Test it once in a fresh project: ask "what are your instructions here?"
and check that it read the guide.

## 2. Project instructions (optional backup, same text for every project)

Projects that already have the older, longer instructions can keep them: they
already say to read `RESEARCH_GUIDE.md` and follow it, so they pick up new
rules (Open Brain included) from the guide. For a new project, paste this if
you want a belt-and-braces copy; it has no ticker in it, so it never needs
editing:

```
You are my buy-side research analyst for the company in this project's connected folder. Before answering anything, read RESEARCH_GUIDE.md in that folder and follow it: it holds your role, where to look (local corpus, Open Brain, Quartr, web), how to analyze, style, citations and where to save outputs. If you cannot find it, tell me.
```

## 3. Connectors

Each project needs the Quartr, Exa and Open Brain connectors turned on; the
guide tells Claude to say so in one line if Open Brain is missing.
