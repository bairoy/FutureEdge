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


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health", tags=["ops"], summary="Service health check")
async def health():
    """
    Simple health check endpoint.
    Returns 200 OK if the service is running.
    Used by Docker health checks and load balancers.
    No authentication required.
    """
    return {"status": "ok", "service": "futureedge"}