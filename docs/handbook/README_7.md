# README 7 — REBUILD GUIDE: FROM SCRATCH

## If you lost the entire codebase, here is the exact build order.

---

## Phase 0: Infrastructure Setup

### What to spin up first
```yaml
# docker-compose.yml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: futureedge
      POSTGRES_PASSWORD: futureedge
      POSTGRES_DB: futureedge
    ports: ["5432:5432"]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  qdrant:
    image: qdrant/qdrant:latest
    ports: ["6333:6333", "6334:6334"]
```

### `.env` file template
```
# Database
POSTGRES_USER=futureedge
POSTGRES_PASSWORD=futureedge
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=futureedge

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0

# Qdrant
QDRANT_HOST=localhost
QDRANT_PORT=6333
QDRANT_COLLECTION=trade_memories

# Auth
JWT_SECRET_KEY=your-secret-key-change-in-production
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

# App
APP_ENV=development
DEBUG=true
ALLOWED_ORIGINS=http://localhost:3000

# Broker (start with mock)
ACTIVE_BROKER=mock
ACTIVE_FEED=mock

# Zerodha (fill in when ready for live)
ZERODHA_API_KEY=
ZERODHA_ACCESS_TOKEN=

# AI features (optional)
LLM_REASONING_ENABLED=true
OPENAI_API_KEY=
LOCAL_MODEL_BASE_URL=
LOCAL_MODEL_NAME=
FINBERT_ENABLED=false

# Safety
MAX_DAILY_LOSS_PCT=3.0
EXIT_MONITOR_INTERVAL_SECONDS=10
WEIGHT_UPDATE_INTERVAL_TRADES=20
MIN_TRADES_FOR_WEIGHT_UPDATE=10
DEFAULT_INSTRUMENT_TOKEN=256265
```

---

## Phase 1: Backend Foundation

### Step 1 — Project structure
```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── core/
│   │   └── config.py          ← Build this FIRST
│   ├── db/
│   │   ├── postgres.py        ← Build second
│   │   ├── redis.py           ← Build third
│   │   ├── models/
│   │   │   ├── user.py
│   │   │   ├── refresh_token.py
│   │   │   ├── trade.py
│   │   │   └── workflow_run.py
│   │   └── repos/
│   │       ├── user_repo.py
│   │       └── trade_repo.py
│   ├── auth/
│   │   ├── jwt_utils.py
│   │   └── dependencies.py
│   ├── graph/
│   │   ├── state.py           ← TypedDict schema
│   │   ├── builder.py         ← Graph topology
│   │   └── runtime.py        ← Lifespan
│   ├── agents/
│   │   ├── regime_agent.py
│   │   ├── signal_agent.py
│   │   ├── sentiment_agent.py
│   │   ├── risk_agent.py
│   │   ├── portfolio_agent.py
│   │   ├── orchestration_agent.py
│   │   ├── execution_agent.py
│   │   └── human_agent.py
│   ├── brokers/
│   │   ├── base.py
│   │   ├── mock.py
│   │   └── zerodha.py
│   ├── data/
│   │   ├── feed.py
│   │   └── indicator_cache.py
│   ├── memory/
│   │   ├── embedder.py
│   │   └── qdrant_store.py
│   ├── models/
│   │   └── llm_reasoner.py
│   ├── jobs/
│   │   ├── exit_monitor.py
│   │   ├── weight_updater.py
│   │   └── position_reconciler.py
│   ├── services/
│   │   └── kill_switch_service.py
│   └── api/
│       ├── dependencies/
│       │   └── rate_limiter.py
│       └── routes/
│           ├── auth_router.py
│           ├── workflow_router.py
│           ├── kill_switch_router.py
│           ├── market_router.py
│           ├── trades_router.py
│           ├── zerodha_router.py
│           └── backtest_router.py
requirements.txt
alembic.ini
alembic/
```

### Step 2 — `app/core/config.py` (build first)
```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    POSTGRES_HOST: str
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB:   int = 0

    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION: str = "trade_memories"

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    ACTIVE_BROKER: str = "mock"
    ACTIVE_FEED:   str = "mock"

    LLM_REASONING_ENABLED: bool = True
    OPENAI_API_KEY: str | None = None
    LOCAL_MODEL_BASE_URL: str | None = None
    LOCAL_MODEL_NAME: str | None = None
    FINBERT_ENABLED: bool = False

    MAX_DAILY_LOSS_PCT: float = 3.0
    EXIT_MONITOR_INTERVAL_SECONDS: int = 10
    WEIGHT_UPDATE_INTERVAL_TRADES: int = 20
    MIN_TRADES_FOR_WEIGHT_UPDATE: int = 10
    DEFAULT_INSTRUMENT_TOKEN: int = 256265

    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()
```

### Step 3 — `app/db/postgres.py`
```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.config import settings

DATABASE_URL = (
    f"postgresql+asyncpg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
    f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
)

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)

AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
```

### Step 4 — `app/db/redis.py`
```python
import redis.asyncio as redis
from app.core.config import settings

redis_client = redis.Redis(
    host=settings.REDIS_HOST, port=settings.REDIS_PORT,
    db=settings.REDIS_DB, decode_responses=True,
    socket_connect_timeout=10, retry_on_timeout=True, health_check_interval=30,
)

KEY_TRADING_HALT         = "TRADING_HALT"
KEY_INDICATORS           = "futureedge:indicators:{symbol}"
KEY_ZERODHA_ACCESS_TOKEN = "futureedge:zerodha:access_token"
STREAM_TICKS             = "futureedge:ticks"
CHANNEL_AGENT_RESULTS    = "futureedge:agent_results"
CHANNEL_TRADE_EXECUTED   = "futureedge:trade_executed"
CHANNEL_HITL_PENDING     = "futureedge:hitl_pending"
CHANNEL_KILL_SWITCH      = "futureedge:kill_switch"
```

---

## Phase 2: Data Models

### Step 5 — `app/graph/state.py` (define before writing any agent)
```python
from typing import TypedDict, Annotated, Optional
import operator
from pydantic import BaseModel, Field

class MarketContext(BaseModel):
    symbol:        str
    current_price: float
    ohlcv_1m:      list[dict]
    regime:        str = "UNKNOWN"
    volatility_24h: float = 0.02
    recent_news:   list[dict] = []

class PortfolioSnapshot(BaseModel):
    total_equity:      float
    margin_used:       float
    margin_available:  float
    unrealized_pnl:    float
    open_positions:    list[dict] = []

class AgentVote(BaseModel):
    agent:      str
    decision:   str           # BUY | SELL | HOLD | VETO
    confidence: float = 0.5
    reasoning:  str   = ""
    metadata:   dict  = Field(default_factory=dict)

class TradeProposal(BaseModel):
    symbol:          str
    direction:       str      # LONG | SHORT | NONE
    size:            float
    entry_price:     float
    stop_loss:       float | None = None
    take_profit:     float | None = None
    risk_score:      float = 0.5
    agent_consensus: list[AgentVote] = []
    human_approved:  bool | None = None
    human_notes:     str  | None = None
    llm_rationale:   str  | None = None

class AgentState(TypedDict):
    user_id:        str
    market_context: MarketContext
    portfolio:      PortfolioSnapshot
    signal_vote:    Optional[AgentVote]
    sentiment_vote: Optional[AgentVote]
    risk_vote:      Optional[AgentVote]
    portfolio_vote: Optional[AgentVote]
    consensus:      Optional[TradeProposal]
    hitl_required:  bool
    hitl_status:    str
    executed_trade: Optional[dict]
    execution_error: Optional[str]
    run_id:          str
    timestamp:       str
    episodic_memory: list[dict]
    market_vector:   Optional[list[float]]
    logs:            Annotated[list[str], operator.add]
    completed_nodes: Annotated[list[str], operator.add]
```

### Step 6 — SQLAlchemy Models

**`app/db/models/user.py`**
```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Boolean, DateTime
import uuid

class Base(DeclarativeBase): pass

class User(Base):
    __tablename__ = "users"
    id:              Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email:           Mapped[str]       = mapped_column(String, unique=True, nullable=False)
    hashed_password: Mapped[str]       = mapped_column(String, nullable=False)
    role:            Mapped[str]       = mapped_column(String, default="viewer")
    is_active:       Mapped[bool]      = mapped_column(Boolean, default=True)
    created_at:      Mapped[datetime]  = mapped_column(DateTime(timezone=True), default=datetime.now(UTC))
```

**`app/db/models/trade.py`**
```python
class Trade(Base):
    __tablename__ = "trades"
    id:               Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id:          Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    run_id:           Mapped[str | None] = mapped_column(String)
    symbol:           Mapped[str]        = mapped_column(String, nullable=False)
    direction:        Mapped[str]        = mapped_column(String, nullable=False)
    size:             Mapped[float | None]
    quantity:         Mapped[int | None]
    entry_price:      Mapped[float | None]
    stop_loss:        Mapped[float | None]
    take_profit:      Mapped[float | None]
    exit_price:       Mapped[float | None]
    realized_pnl:     Mapped[float | None]
    pnl_pct:          Mapped[float | None]
    status:           Mapped[str]        = mapped_column(String, default="OPEN")
    risk_score:       Mapped[float | None]
    hitl_required:    Mapped[bool]       = mapped_column(Boolean, default=False)
    human_approved:   Mapped[bool | None]
    agent_consensus:  Mapped[list | None] = mapped_column(JSON)
    broker:           Mapped[str | None]
    broker_order_id:  Mapped[str | None]
    actual_fill_price: Mapped[float | None]
    slippage:         Mapped[float | None]
    opened_at:        Mapped[datetime]   = mapped_column(DateTime(timezone=True), default=datetime.now(UTC))
    closed_at:        Mapped[datetime | None]
```

---

## Phase 3: LangGraph Graph

### Step 7 — `app/graph/builder.py`
```python
from langgraph.graph import StateGraph, START, END
from app.graph.state import AgentState
from app.agents import *   # import all agent functions

def create_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("regime_agent",   regime_agent_node)
    builder.add_node("signal_agent",   signal_agent_node)
    builder.add_node("sentiment_agent", sentiment_agent_node)
    builder.add_node("risk_agent",     risk_agent_node)
    builder.add_node("portfolio_agent", portfolio_agent_node)
    builder.add_node("orchestrator",   orchestrator_node)
    builder.add_node("human_review",   human_review_node)
    builder.add_node("execution",      execution_node)

    builder.add_edge(START, "regime_agent")
    for agent in ["signal_agent", "sentiment_agent", "risk_agent", "portfolio_agent"]:
        builder.add_edge("regime_agent", agent)
        builder.add_edge(agent, "orchestrator")
    builder.add_conditional_edges("orchestrator", should_human_review,
        {"human_review": "human_review", "execute": "execution"})
    builder.add_edge("human_review", "execution")
    builder.add_edge("execution", END)

    return builder

workflow_graph = None   # set in runtime.lifespan

async def run_agent_cycle(market_context, portfolio, user_id) -> dict:
    import uuid
    run_id = str(uuid.uuid4())[:8]
    initial_state: AgentState = {
        "user_id": user_id,
        "market_context": market_context,
        "portfolio": portfolio,
        "signal_vote": None,
        "sentiment_vote": None,
        "risk_vote": None,
        "portfolio_vote": None,
        "consensus": None,
        "hitl_required": False,
        "hitl_status": "NOT_REQUIRED",
        "executed_trade": None,
        "execution_error": None,
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "episodic_memory": [],
        "market_vector": None,
        "logs": [],
        "completed_nodes": [],
    }
    result = await workflow_graph.ainvoke(initial_state, config={"configurable": {"thread_id": run_id}})
    return {"thread_id": run_id, "state": result}
```

### Step 8 — `app/graph/runtime.py`
```python
from contextlib import asynccontextmanager
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.graph.builder import create_graph, workflow_graph as _wg

@asynccontextmanager
async def lifespan(app):
    global _wg
    db_uri = f"postgresql://{settings.POSTGRES_USER}:..."
    async with AsyncPostgresSaver.from_conn_string(db_uri) as checkpointer:
        await checkpointer.setup()
        import app.graph.builder as builder_module
        builder_module.workflow_graph = create_graph().compile(checkpointer=checkpointer)

        # Start broker, tick publisher, exit monitor...
        broker = get_broker(); await broker.connect()
        await exit_monitor.start()
        init_collection()   # Qdrant
        yield
        await exit_monitor.stop()
        await broker.disconnect()
```

---

## Phase 4: Agents (build in this order)

1. **`regime_agent.py`** — pure math, pandas/numpy. No external calls. Test first.
2. **`signal_agent.py`** — depends on indicator_cache (Redis + OHLCV). Test with mock candles.
3. **`sentiment_agent.py`** — keyword fallback first, FinBERT optional. Test with news strings.
4. **`risk_agent.py`** — depends on TradeRepo (Postgres). Build after models/repos.
5. **`portfolio_agent.py`** — reads from PortfolioSnapshot. Test with mock portfolio.
6. **`orchestration_agent.py`** — depends on all 4 agents' votes, qdrant_store, llm_reasoner. Build last.
7. **`human_agent.py`** — only LangGraph interrupt. Simple but fragile (GraphInterrupt must re-raise).
8. **`execution_agent.py`** — depends on broker, TradeRepo, qdrant_store, Redis.

---

## Phase 5: Auth

### Step 9 — JWT utilities
```python
# app/auth/jwt_utils.py

def create_access_token(user_id: str, role: str) -> str:
    payload = {
        "sub":  user_id,
        "role": role,
        "type": "access",
        "exp":  datetime.now(UTC) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")

def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()
```

### Step 10 — Dependencies
```python
# app/auth/dependencies.py

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

async def get_current_user(token=Depends(oauth2_scheme), db=Depends(get_db)):
    payload = decode_token(token)
    user = await UserRepo.get_by_id(db, payload["sub"])
    return user

async def require_trader(user=Depends(get_current_user)):
    if user.role not in ("trader", "risk_manager", "admin"):
        raise HTTPException(403)
    return user

async def require_risk_manager(user=Depends(get_current_user)):
    if user.role not in ("risk_manager", "admin"):
        raise HTTPException(403)
    return user
```

---

## Phase 6: Routes

### Step 11 — `app/main.py` (assemble last)
```python
from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.graph.runtime import lifespan
from app.api.routes import auth_router, workflow_router, kill_switch_router, market_router, trades_router

app = FastAPI(title="FutureEdge", lifespan=lifespan)

app.include_router(auth_router.router,         prefix="/auth",       tags=["auth"])
app.include_router(workflow_router.router,     prefix="/api/v1",     tags=["workflow"])
app.include_router(kill_switch_router.router,  prefix="/api/v1",     tags=["safety"])
app.include_router(market_router.router,       prefix="/api/v1",     tags=["market"])
app.include_router(trades_router.router,       prefix="/api/v1",     tags=["trades"])

@app.get("/health")
async def health():
    return {"status": "ok"}
```

---

## Phase 7: Frontend

```
frontend/
├── src/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx                   ← landing / login redirect
│   │   ├── dashboard/
│   │   │   └── page.tsx              ← main trading dashboard
│   │   ├── login/
│   │   │   └── page.tsx
│   │   └── ...
│   ├── components/
│   │   ├── AgentPanel.tsx            ← live agent votes
│   │   ├── TradeProposal.tsx         ← current proposal card
│   │   ├── ApprovalModal.tsx         ← HITL approve/reject
│   │   ├── TradeHistoryTable.tsx     ← closed trades
│   │   └── KillSwitchButton.tsx
│   ├── lib/
│   │   ├── api.ts                    ← fetch wrapper with JWT
│   │   └── websocket.ts             ← Redis pub/sub bridge client
│   └── store/
│       └── auth.ts                   ← Zustand auth state
```

### WebSocket client setup
```typescript
// lib/websocket.ts
export function createWebSocket(token: string, onMessage: (data: any) => void) {
  const ws = new WebSocket(`ws://localhost:8000/api/v1/market/stream?token=${token}`);
  ws.onmessage = (event) => onMessage(JSON.parse(event.data));
  ws.onerror   = (err) => console.error("WS error", err);
  return ws;
}
```

---

## Critical Build Order Summary

```
1. docker-compose up (Postgres, Redis, Qdrant)
2. .env file
3. app/core/config.py
4. app/db/postgres.py + app/db/redis.py
5. app/db/models/ (User, Trade, WorkflowRun, RefreshToken)
6. alembic init + migrations (creates tables)
7. app/db/repos/ (UserRepo, TradeRepo)
8. app/graph/state.py (TypedDicts — no imports from anything else)
9. app/memory/embedder.py (pure math — no imports from app)
10. app/memory/qdrant_store.py
11. app/brokers/base.py + mock.py
12. app/data/feed.py + indicator_cache.py
13. app/models/llm_reasoner.py
14. All agents (regime → signal → sentiment → risk → portfolio → orchestrator → human → execution)
15. app/jobs/ (weight_updater → exit_monitor → position_reconciler)
16. app/graph/builder.py
17. app/graph/runtime.py
18. app/auth/ (jwt_utils, dependencies)
19. app/api/routes/ (auth → workflow → trades → kill_switch → market)
20. app/main.py
21. Frontend (Next.js)
```
