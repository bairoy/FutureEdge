"""
main.py
========
FutureEdge FastAPI application entry point.

HOW TO RUN:
-----------
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Or inside Docker (recommended):
    docker compose up

SETUP ORDER (first time only):
--------------------------------
1. Start services:
       docker compose up -d db redis

2. Create all database tables:
       docker compose exec backend python -m app.scripts.create_tables

3. Create the first admin user:
       docker compose exec backend python -m app.scripts.create_admin

4. Start the app:
       docker compose up backend

5. Visit the API docs:
       http://localhost:8000/docs

API ENDPOINTS:
--------------
POST /auth/login                    → get JWT tokens
POST /auth/refresh                  → refresh access token
POST /auth/logout                   → revoke refresh token
GET  /auth/me                       → current user profile

POST /users                         → create user (admin only)
GET  /users                         → list all users (admin only)
PUT  /users/{id}/role               → change role (admin only)
POST /users/{id}/deactivate         → disable user (admin only)

POST /api/v1/workflow/run           → start agent cycle (trader+)
POST /api/v1/workflow/resume        → approve/reject HITL (risk_manager+)
GET  /api/v1/workflow/{id}/status   → check workflow status (viewer+)

POST /api/v1/kill-switch/halt       → halt trading (risk_manager+)
POST /api/v1/kill-switch/resume     → resume trading (risk_manager+)
GET  /api/v1/kill-switch/status     → check kill switch (viewer+)

WS   /api/v1/market/stream?token=.. → live WebSocket stream (viewer+)
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.graph.runtime import lifespan

# Auth endpoints (login, refresh, logout, /me)
from app.api.routes.auth_router import router as auth_router

# User management (admin only: create, list, change role)
from app.api.routes.users_router import router as users_router

# Workflow endpoints (run, resume HITL, status)
from app.api.routes.workflow_router import router as workflow_router

# Kill switch (halt/resume trading)
from app.api.routes.kill_switch_router import router as kill_switch_router

# WebSocket live stream
from app.api.routes.market_router import router as market_router

from app.api.routes.zerodha_router import router as zerodha_router
from app.api.routes.trades_router       import router as trades_router
from app.api.routes.backtest_router import router as backtest_router
from app.core.logging import setup_logging

# Initialize structured logging
setup_logging()

# ============================================================
# CREATE APP
# ============================================================

app = FastAPI(
    title       = "FutureEdge — Indian Market AI Trading System",
    description = (
        "Multi-agent AI trading system for NSE/BSE via Zerodha Kite. "
        "Supports multi-user with role-based access control."
    ),
    version     = "1.0.0",
    lifespan    = lifespan,   # handles startup/shutdown
)

# CORS configuration (Phase 2 — new)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify actual domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# REGISTER ROUTERS
# ============================================================

# Auth — no prefix, routes are /auth/login etc.
app.include_router(auth_router)

# Users — prefix /users
app.include_router(users_router)

# Workflow, kill switch, market — all under /api/v1/
app.include_router(workflow_router)
app.include_router(kill_switch_router)
app.include_router(market_router)
app.include_router(zerodha_router)
app.include_router(trades_router)       # /api/v1/trades
app.include_router(backtest_router)


# ============================================================
# HEALTH CHECK
# ============================================================

from sqlalchemy import text
from app.db.postgres import AsyncSessionLocal
from app.db.redis import redis_client

async def check_postgres() -> bool:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"HealthCheck: Postgres check failed: {e}")
        return False

async def check_redis() -> bool:
    try:
        await redis_client.ping()
        return True
    except Exception as e:
        logger.error(f"HealthCheck: Redis check failed: {e}")
        return False

async def check_qdrant() -> bool:
    try:
        import asyncio
        from app.memory.qdrant_store import get_qdrant_client
        def _check():
            client = get_qdrant_client()
            client.get_collections()
            return True
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _check)
    except Exception as e:
        logger.error(f"HealthCheck: Qdrant check failed: {e}")
        return False

@app.get("/health", tags=["ops"], summary="Service health check")
async def health():
    """
    Service health check endpoint verifying core dependencies.
    """
    pg_ok = await check_postgres()
    redis_ok = await check_redis()
    qdrant_ok = await check_qdrant()

    status = "ok" if (pg_ok and redis_ok and qdrant_ok) else "degraded"

    return {
        "status": status,
        "service": "futureedge",
        "dependencies": {
            "postgres": "ok" if pg_ok else "down",
            "redis": "ok" if redis_ok else "down",
            "qdrant": "ok" if qdrant_ok else "down",
        }
    }