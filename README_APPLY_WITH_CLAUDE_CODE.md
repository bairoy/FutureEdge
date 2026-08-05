# How to use this with Claude Code

This bundle mirrors your repo's directory structure, so you can drop it straight in.

## 1. Unzip into your repo root

```bash
cd /path/to/FutureEdge
unzip futureedge-patch.zip -d .
```

This places:
- `CLAUDE.md` at your repo root — Claude Code loads this automatically at the start of every session in this repo. It has the persistent project context and hard rules (kill switch behavior, HITL requirements, broker abstraction pattern) so you don't have to re-explain them each time.
- `docs/IMPLEMENTATION_PLAN.md` — the full ordered task checklist.
- The three already-written files at their real target paths:
  - `backend/app/data/fundamentals_scraper.py`
  - `backend/app/agents/fundamental_agent.py`
  - `backend/app/db/models/fundamental_scorecard.py`

## 2. Start Claude Code in the repo

```bash
cd /path/to/FutureEdge
claude
```

It will auto-load `CLAUDE.md`. Then just say:

```
Read docs/IMPLEMENTATION_PLAN.md and work through Phase 0, checking off each
task as you complete it. Stop after Phase 0 so I can review before you continue.
```

Working phase-by-phase like this (rather than "do everything") gives you a natural review checkpoint, and matches how the plan is already ordered — Phase 0 items are small and independent, later phases build on them.

## 3. Subsequent sessions

Since `CLAUDE.md` stays in the repo and `docs/IMPLEMENTATION_PLAN.md` has checkboxes, you can just start a new Claude Code session later and say "continue with the implementation plan" — it'll read the current checkbox state and pick up where it left off.

## A note on the three code files already included

They were written against the actual current state of your repo (I pulled and read it directly), matching your existing conventions (loguru, async patterns, docstring style). But I couldn't execute the Screener.in scraper against a live BeautifulSoup parse from inside this environment — the selectors were verified against a real fetched page, not run end-to-end. Treat `fundamentals_scraper.py` as needing a real test run against a couple of symbols before you trust it broadly; the module is written to fail loudly (raise, not silently return empty data) if Screener's markup doesn't match what's expected, specifically so this is easy to catch.
