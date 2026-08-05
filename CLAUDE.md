# CLAUDE.md — FutureEdge project context

Multi-agent AI trading system for NSE/BSE, Python/FastAPI + LangGraph backend,
Next.js 14 frontend, Zerodha Kite Connect for live broker access. Read
`docs/IMPLEMENTATION_PLAN.md` for the current active work — this file is
persistent context, that file is the task list.

## Stack & conventions (match these — don't introduce new patterns)

- Backend: Python 3.11+, FastAPI, async everywhere, SQLAlchemy async ORM (no raw SQL string interpolation, ever), Pydantic settings in `app/core/config.py`
- Logging: `loguru`, not stdlib `logging` — `from loguru import logger`
- Broker access: always through `app/brokers/base.py::get_broker()`, never instantiate `ZerodhaBroker`/`PaperBroker` directly — this is what makes paper/live swappable
- Agents live in `app/agents/`, one file per agent, wired into the LangGraph pipeline in `app/graph/builder.py`
- Docstring style: module-level docstring explaining WHY the file exists and HOW to use it, not just what it does — follow the existing files as the template
- Tests in `tests/`, mirroring the `app/` structure; run with `pytest tests/ --asyncio-mode=auto`

## Hard rules — do not violate these regardless of what a task seems to ask for

- **Never remove or weaken HITL gating on live (non-paper) order execution.** Trading mode uses risk-score-based auto-approval already — investing mode (CNC/delivery orders) must ALWAYS require human approval, no exceptions, per `docs/IMPLEMENTATION_PLAN.md` §Investing Mode.
- **Never commit `.env`, `broker_token.json`, or any file containing a real API key/secret/access token.** Both are already gitignored — keep them that way.
- **Never log a Zerodha access token, JWT, or password in plaintext**, even at debug level.
- **The kill switch must fail closed.** Any change to `kill_switch_service.py` must preserve "halted persists until an explicit human action releases it" — do not reintroduce a silent auto-expiry as the default behavior.
- Any change to `app/api/routes/workflow_router.py::resume_workflow` or the order-placement path in `execution_agent.py` / `brokers/zerodha.py` needs a corresponding test — these are money-moving paths with a documented race-condition history (see plan, Security §2).

## Commands

```bash
# Backend tests (run from root)
PYTHONPATH=backend ./.venv/bin/pytest tests

# Lint
ruff check backend/app --select E,F --ignore E501,E402

# Frontend
cd frontend && npm run build

# Local stack
docker compose up -d postgres redis qdrant
docker compose run --rm backend python -m app.scripts.create_tables
docker compose up
```

## Where things are (so you don't have to rediscover this every session)

- Broker abstraction: `backend/app/brokers/base.py`, `paper.py`, `zerodha.py`
- LangGraph pipeline definition: `backend/app/graph/builder.py`, state in `graph/state.py`
- Config/settings: `backend/app/core/config.py` (pydantic-settings, reads `.env`)
- Existing technical analysis: `backend/app/agents/signal_agent.py` (RSI/MACD/BB/MTF)
- New fundamental analysis (this phase): `backend/app/agents/fundamental_agent.py`, `backend/app/data/fundamentals_scraper.py`
- Kill switch: `backend/app/services/kill_switch_service.py`
- CI: `.github/workflows/ci.yml` (backend-only currently — no frontend job, see plan Phase 0)
