# README 2 — BACKEND EXECUTION FLOW

## File-by-File Breakdown

---

## `backend/app/main.py`

### Why it exists
This is the FastAPI **entry point** — the file uvicorn loads. Without it nothing starts.

### What breaks without it
The entire application doesn't exist. No HTTP server, no routes.

### Execution trigger
`uvicorn app.main:app` — uvicorn imports this module and reads the `app` object.

### Key sections

**`setup_logging()`** — must run before anything else or log lines are lost.

**`lifespan=lifespan`** — connects the startup/shutdown lifecycle (Postgres, KiteTicker, broker, Qdrant, exit monitor) to the FastAPI app object. This is the modern replacement for `@app.on_event("startup")`.

**`app.include_router(...)`** — registers each router's URL prefixes and handlers. Order doesn't matter except that routers must be added before the first request arrives.

### Imports with purpose
```python
from app.graph.runtime import lifespan          # startup: compile graph, connect broker
from app.api.routes.auth_router import router   # /auth/* endpoints
from app.api.routes.workflow_router import router  # /api/v1/workflow/*
from app.api.routes.kill_switch_router import router  # /api/v1/kill-switch/*
from app.api.routes.market_router import router    # /api/v1/market/* + WebSocket
from app.api.routes.zerodha_router import router   # /api/v1/zerodha/*
from app.api.routes.trades_router import router    # /api/v1/trades
from app.api.routes.backtest_router import router  # /api/v1/backtest
```

### Health check functions
```python
async def check_postgres() -> bool:
    # Runs SELECT 1 to verify DB is alive
    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))

async def check_redis() -> bool:
    await redis_client.ping()

async def check_qdrant() -> bool:
    # QdrantClient is sync → run in thread pool
    loop.run_in_executor(None, lambda: client.get_collections())
```

---

## `backend/app/core/config.py`

### Why it exists
Central configuration loaded from `.env` at startup. All settings are typed Python objects. Any missing required variable raises a `ValidationError` before the app starts.

### What breaks without it
Every other file that imports `settings` would fail. Database connection strings, API keys, feature flags all live here.

### Key pattern: `pydantic_settings.BaseSettings`
```python
class Settings(BaseSettings):
    POSTGRES_HOST: str     # required — no default
    ACTIVE_BROKER: str = "mock"    # optional — has default

    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()   # reads .env on import
```

Pydantic validates every field's type. `POSTGRES_PORT: int` will fail if the env var is `"abc"`.

### Feature flags of note
```python
FINBERT_ENABLED: bool = False     # True = use ProsusAI/finbert for sentiment. False = keyword matching.
LLM_REASONING_ENABLED: bool = True  # True = call GPT for trade rationale
ACTIVE_BROKER: str = "mock"       # "mock" | "zerodha"
ACTIVE_FEED: str = "mock"         # "mock" | "zerodha"
```

---

## `backend/app/graph/state.py`

### Why it exists
Defines the shared memory structure (`AgentState`) passed between all LangGraph nodes. Every node reads from and writes to this TypedDict.

### What breaks without it
LangGraph cannot type-check the state. Agents would have no contract to write their outputs to.

### Key types

**`AgentVote`** — what one agent decides
```python
class AgentVote(BaseModel):
    agent:      str      # "SignalAgent"
    decision:   str      # "BUY" | "SELL" | "HOLD" | "VETO"
    confidence: float    # 0.0 to 1.0
    reasoning:  str      # human-readable explanation
    metadata:   dict     # agent-specific data (RSI, kelly_fraction, etc.)
```

**`TradeProposal`** — the orchestrator's final decision
```python
class TradeProposal(BaseModel):
    symbol:          str
    direction:       str        # "LONG" | "SHORT" | "NONE"
    size:            float      # position size in Rupees
    entry_price:     float
    stop_loss:       float | None
    take_profit:     float | None
    risk_score:      float      # 0=safe, 1=extremely risky
    agent_consensus: list[AgentVote]
    human_approved:  bool | None  # None=not required, True=approved, False=rejected
    llm_rationale:   str | None   # GPT explanation
```

**`AgentState`** — the LangGraph TypedDict (the "shared brain")
```python
class AgentState(TypedDict):
    # inputs
    user_id:        str
    symbol:         str
    market_context: MarketContext
    portfolio:      PortfolioSnapshot
    # agent outputs (None until each agent runs)
    signal_vote:    Optional[AgentVote]
    sentiment_vote: Optional[AgentVote]
    risk_vote:      Optional[AgentVote]
    portfolio_vote: Optional[AgentVote]
    # orchestrator outputs
    consensus:      Optional[TradeProposal]
    llm_rationale:  Optional[str]
    # HITL
    hitl_required:  bool
    hitl_status:    str   # "NOT_REQUIRED" | "PENDING" | "APPROVED" | "REJECTED"
    # execution
    executed_trade:  Optional[dict]
    execution_error: Optional[str]
    # observability
    run_id:          str
    timestamp:       str
    episodic_memory: list[dict]
    market_vector:   Optional[list[float]]
    logs:            Annotated[list[str], operator.add]       # append-only
    completed_nodes: Annotated[list[str], operator.add]       # append-only
```

The `Annotated[list[str], operator.add]` means LangGraph automatically **appends** new values rather than overwriting. Every node can add to `logs` without seeing each other's additions.

---

## `backend/app/graph/builder.py`

### Why it exists
Defines the graph **topology** — which nodes exist, what order they run in, and what edges connect them.

### What breaks without it
No workflow exists to run.

### Key function: `create_graph()`
```python
builder = StateGraph(AgentState)

# Register nodes (name → function)
builder.add_node("regime_agent",  regime_agent_node)
builder.add_node("signal_agent",  signal_agent_node)
builder.add_node("orchestrator",  orchestrator_node)
builder.add_node("human_review",  human_review_node)
builder.add_node("execution",     execution_node)

# Edges
builder.add_edge(START, "regime_agent")
builder.add_edge("regime_agent", "signal_agent")   # fan-out
builder.add_edge("regime_agent", "sentiment_agent")
builder.add_edge("regime_agent", "risk_agent")
builder.add_edge("regime_agent", "portfolio_agent")
builder.add_edge("signal_agent",    "orchestrator")  # fan-in
builder.add_edge("sentiment_agent", "orchestrator")
builder.add_edge("risk_agent",      "orchestrator")
builder.add_edge("portfolio_agent", "orchestrator")
builder.add_conditional_edges(
    "orchestrator",
    should_human_review,
    {"human_review": "human_review", "execute": "execution"}
)
```

### Key function: `run_agent_cycle()`
- Creates `run_id` (short UUID)
- Assembles `initial_state`
- Sets `config = {"configurable": {"thread_id": run_id}}`
- Calls `graph.ainvoke(initial_state, config=config)`
- **Returns** `{"thread_id": run_id, "state": result}`

The `thread_id` = `run_id` is the PostgreSQL checkpoint key. HITL resume must pass the same thread_id in its config.

---

## `backend/app/graph/runtime.py`

### Why it exists
Manages the **lifecycle** of the application — everything that must happen before serving requests and after shutdown.

### What breaks without it
- The PostgreSQL checkpointer never connects (HITL can't save state)
- The graph never gets compiled (no workflow to run)
- The broker never connects
- Exit monitor never starts

### Startup sequence (inside `lifespan()`)
```python
# 1. PostgreSQL checkpointer — MUST stay alive for HITL to work
async with AsyncPostgresSaver.from_conn_string(db_uri) as checkpointer:
    await checkpointer.setup()  # creates checkpoint tables if needed

    # 2. Compile graph once (with checkpointer attached)
    workflow_graph = create_graph().compile(checkpointer=checkpointer)

    # 3. Load 100 historical candles from yfinance → seed Redis Stream
    candles = load_historical_candles("NIFTY 50", period="5d", interval="1m")
    for candle in candles[-100:]:
        await redis_client.xadd(STREAM_TICKS, {...candle...})

    # 4. Start KiteTicker (only if ACTIVE_FEED="zerodha")
    tick_publisher.start()

    # 5. Connect broker
    broker = get_broker()
    await broker.connect()

    # 6. Initialise Qdrant collection
    await loop.run_in_executor(None, init_collection)

    # 7. Start exit monitor (checks SL/TP every 10 seconds)
    await exit_monitor.start()

    # 8. Start position reconciler (syncs DB with broker every 5 minutes)
    await position_reconciler.start()

    yield   # ← APP RUNS HERE

    # SHUTDOWN (reverse order)
    await position_reconciler.stop()
    await exit_monitor.stop()
    tick_publisher.stop()
    await broker.disconnect()
```

### Why the checkpointer must stay alive
When `interrupt()` fires in `human_review_node`, LangGraph saves the entire `AgentState` to PostgreSQL. When the resume API is called (possibly hours later), LangGraph **reloads** that state from PostgreSQL and continues from where it left off. If the PostgreSQL connection closes between pause and resume, the workflow state is lost.

---

## `backend/app/db/postgres.py`

### Why it exists
Creates the SQLAlchemy async engine and session factory. Both are used application-wide.

### What breaks without it
No database access anywhere. Every repo query fails.

### Key objects
```python
engine = create_async_engine(
    "postgresql+asyncpg://user:pass@host:port/db",
    pool_pre_ping=True,   # check connection health before reuse
    pool_size=10,         # keep 10 connections alive
    max_overflow=20,      # allow 20 extra under heavy load
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # ORM objects stay readable after commit (critical for async)
)
```

### Usage pattern everywhere in the codebase
```python
async with AsyncSessionLocal() as session:
    result = await session.execute(select(Trade).where(Trade.user_id == user_id))
    trades = result.scalars().all()
    await session.commit()
```

---

## `backend/app/db/redis.py`

### Why it exists
Single Redis client + all key/channel name constants. If you scattered Redis key names across files, one typo would break pub/sub silently.

### Four uses of Redis in FutureEdge
1. **Kill switch** — `KEY_TRADING_HALT = "TRADING_HALT"` — set to `"1"` to halt all trading
2. **Tick stream** — `STREAM_TICKS = "futureedge:ticks"` — live NSE price data
3. **Pub/Sub** — channels for real-time frontend updates
4. **Indicator cache** — RSI/MACD cached for 60s, format: `futureedge:indicators:{symbol}`

### Client configuration
```python
redis_client = redis.Redis(
    decode_responses=True,        # return str not bytes
    socket_connect_timeout=10,    # fail fast if Redis is unreachable
    retry_on_timeout=True,        # auto-retry on transient failures
    health_check_interval=30,     # background ping every 30s
)
```

---

## `backend/app/agents/regime_agent.py`

### Why it exists
Classifies the current market into one of four regimes so downstream agents can adapt their logic. Without this, signal_agent applies the same weights in a trending market as in a rangebound one — which is statistically wrong.

### Execution trigger
First node after `START` in the LangGraph graph. Every other agent waits for this to finish.

### Algorithm
```
1. Compute ATR (Average True Range) — rolling 14-period average of daily price range
2. Compute Directional Movement (+DM, -DM) — measures up vs down price movement
3. Compute ADX from DX — if ADX > 25, there's a strong trend
4. Compute EMA-20 slope (3-period diff) — direction of trend
5. Compute volatility from log returns std over last 30 candles

Classification:
  ADX > 25 + slope > 0  → TRENDING_UP
  ADX > 25 + slope < 0  → TRENDING_DOWN
  ADX ≤ 25 + vol > 3%   → HIGH_VOLATILITY
  ADX ≤ 25 + vol ≤ 3%   → RANGEBOUND
```

### What it modifies in state
`state["market_context"].regime` and `state["market_context"].volatility_24h`

---

## `backend/app/agents/orchestration_agent.py`

### Why it exists
The brain of the system. Collects all 4 votes, applies weighted consensus, decides the trade, sizes the position, evaluates risk, retrieves episodic memory, generates LLM explanation, and publishes to Redis.

### Functions

**`orchestrator_node(state)`**
- Input: complete `AgentState` with all 4 votes populated
- Output: `{"consensus": TradeProposal, "hitl_required": bool, "episodic_memory": [...], ...}`
- Side effect: publishes to `CHANNEL_AGENT_RESULTS` on Redis

**`_calculate_disagreement(votes)`**
```python
active = [v for v in votes if v.decision in ("BUY", "SELL")]
return min(buys, sells) / (buys + sells)
# 0.0 = full agreement, 1.0 = half say BUY half say SELL
```

**`_publish_results(...)`**
- Publishes agent results to Redis pub/sub
- If `hitl_required`, also publishes to `CHANNEL_HITL_PENDING`

---

## `backend/app/agents/execution_agent.py`

### Why it exists
The final action layer. Converts the abstract trade proposal into a real broker order.

### Safety layers (in execution order)
1. Kill switch → `redis.get("TRADING_HALT") == "1"` → return error
2. No-trade → `proposal.direction == "NONE"` → return cleanly
3. HITL approval → `hitl_required and not human_approved` → return error
4. Shares → `int(size / price)` → if 0, return error (position too small)
5. Broker order → `broker.place_order(LIMIT, price * 1.0005)` → real trade
6. PostgreSQL write → `TradeRepo.save_trade()` → permanent record
7. Qdrant write → `store_trade_memory()` → episodic memory (outcome=NEUTRAL initially)
8. Redis publish → `CHANNEL_TRADE_EXECUTED` → frontend update

---

## `backend/app/agents/human_agent.py`

### Why it exists
Implements LangGraph's interrupt/resume mechanism for HITL.

### Critical bug to avoid
```python
try:
    human_response = interrupt(...)
except GraphInterrupt:
    raise   # ← MUST re-raise. GraphInterrupt is LangGraph's control flow signal.
            #   Catching and suppressing it breaks the pause mechanism entirely.
except Exception as e:
    return await _reject(...)   # only for non-GraphInterrupt exceptions
```

### `should_human_review(state)` — the routing function
```python
def should_human_review(state) -> str:
    if state.get("hitl_required", False):
        return "human_review"
    if state.get("risk_vote") and risk_vote.decision == "MODIFY":
        return "human_review"
    return "execute"
```

Returns a string that LangGraph uses as the edge key in `add_conditional_edges`.

---

## `backend/app/db/repos/trade_repo.py`

### Why it exists
Repository pattern: all SQL for the `trades` table lives here. No raw SQL in routes or agents.

### Functions

**`save_trade(session, proposal, run_id, user_id, ...)`**
- Writes new trade to DB
- Calculates slippage = `fill_price - entry_price`
- Status defaults to "OPEN" for real trades

**`close_trade(session, trade_id, user_id, exit_price)`**
- Security check: `Trade.user_id == user_id` — owner-only close
- Calculates PnL: `LONG = (exit - entry) × shares`, `SHORT = (entry - exit) × shares`
- Calculates pnl_pct as percentage of entry price

**`get_recent_closed_trades(session, symbol, user_id, limit=50)`**
- Used by Kelly criterion calculation in risk_agent
- Filtered by user_id (each user has their own Kelly)

**`calculate_win_stats(trades)`**
- Pure function (no DB call)
- Returns `{win_rate, avg_win, avg_loss, sample_size}`
- Returns safe defaults if `< 10` trades

---

## `backend/app/models/llm_reasoner.py`

### Why it exists
Generates a plain-English explanation of why the system decided on a trade. Risk managers need this to make informed HITL decisions.

### Functions

**`generate_trade_rationale(...)`** — async
- Builds a structured prompt with agent votes, scores, regime, and similar past trades
- Calls OpenAI via `run_in_executor` (sync OpenAI client in thread pool)
- Falls back to `_template_rationale()` if API key missing or call fails

**`_template_rationale(...)`** — sync fallback
- No API key needed
- Produces: `"System decided LONG based on agent votes: SignalAgent=BUY, ... Risk level is moderate (score=0.65) with strong consensus (disagreement=0.15)."`

### Critical design
The LLM **explains** the decision — it does **not make** it. The consensus score calculation is entirely deterministic. This ensures the system is auditable even if the LLM call fails.

---

## `backend/app/memory/embedder.py`

### Why it exists
Converts a market snapshot into a 12-dimensional float vector for similarity search in Qdrant. Language model embedders work on text — they're wrong for numerical market data.

### The 12 dimensions
```
dim 0:  RSI / 100                       → 0.0 to 1.0
dim 1:  tanh(MACD_hist / 10)           → -1.0 to 1.0
dim 2:  (price - BB_lower) / BB_range  → 0.0 to 1.0 (Bollinger position)
dim 3:  volatility / 0.20              → 0.0 to 1.0 (capped at 20%)
dim 4:  sentiment_score                → -1.0 to 1.0
dim 5:  1 if TRENDING_UP else 0
dim 6:  1 if TRENDING_DOWN else 0
dim 7:  1 if SIDEWAYS else 0
dim 8:  1 if HIGH_VOL else 0
dim 9:  buy_score                      → 0.0 to 1.0
dim 10: sell_score                     → 0.0 to 1.0
dim 11: risk_score                     → 0.0 to 1.0
```

Cosine similarity in Qdrant finds past trades with similar market conditions.

---

## `backend/app/memory/qdrant_store.py`

### Why it exists
Episodic memory — stores past trade situations as vectors and retrieves similar ones to inform the orchestrator.

### Functions

**`init_collection()`** — called at startup
- Creates `trade_memories` collection if not exists
- `distance=Cosine`, `size=12`

**`store_trade_memory(run_id, vector, outcome="NEUTRAL", ...)`**
- Called by execution_agent after placing a trade
- Stored with `outcome="NEUTRAL"` — updated to WIN/LOSS when exit_monitor closes the trade

**`retrieve_similar_memories(vector, symbol, limit=5)`**
- Searches for top-5 similar past situations
- Filtered to same symbol (cross-symbol contamination avoided)
- Returns: similarity score + all payload fields

**`update_trade_outcome(run_id, outcome, pnl_pct)`**
- Called by exit_monitor when a trade closes
- Updates the Qdrant point's payload from NEUTRAL to WIN/LOSS

---

## `backend/app/jobs/exit_monitor.py`

### Why it exists
Closes trades automatically when stop-loss or take-profit is hit. Without this, trades accumulate indefinitely, Kelly criterion has no closed-trade data, and real Zerodha positions pile up.

### Architecture
```python
class ExitMonitor:
    async def start(self):   # creates asyncio background task
    async def stop(self):    # cancels background task cleanly
    async def _monitor_loop(self):  # loops forever, calls _check_open_trades()
    async def _check_open_trades(self):  # queries all OPEN trades, evaluates each
    async def _evaluate_trade(self, trade, broker):  # SL/TP check
    async def _execute_exit(self, trade, exit_price, reason):  # places exit order
    async def _check_daily_loss_cap(self):  # auto-halt if loss > MAX_DAILY_LOSS_PCT

exit_monitor = ExitMonitor()  # singleton at module level
```

---

## `backend/app/jobs/weight_updater.py`

### Why it exists
Adaptive agent weights — agents that make correct predictions get more influence in the consensus. Without this, all agents always have fixed weights regardless of their actual performance.

### Functions

**`get_agent_weights()`** — called by orchestrator each cycle
- Returns weights from Redis (`futureedge:agent_weights`)
- Falls back to `DEFAULT_WEIGHTS` if Redis is empty

**`maybe_update_weights(user_id, symbol)`** — called by exit_monitor after each close
- Checks: is total closed trades a multiple of `WEIGHT_UPDATE_INTERVAL_TRADES`?
- If yes, calls `_calculate_new_weights(trades)`
- Stores result in Redis

**`_calculate_new_weights(trades)`**
- For each agent: accuracy = correct_votes / total_active_votes
- Allocates 50% of total weight budget to active agents proportionally
- Remaining 50% split equally among inactive agents
- Clips each weight to `[MIN_WEIGHT=0.10, MAX_WEIGHT=0.50]`
- Renormalises so all weights sum to 1.0
