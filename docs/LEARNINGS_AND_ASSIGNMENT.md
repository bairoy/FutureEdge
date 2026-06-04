# FutureEdge: Real Practical Skills Curriculum

> **Mission**: Code FutureEdge yourself from scratch — no AI, no copy-paste.
> **Schedule**: 2 hours every day. 4 phases. 20 projects.
> **Rule**: Each project folder lives in `/Users/baijuyadav/Desktop/futureedge/practice/phaseN/dayN/`.
> After completing every project, compare with the real FutureEdge file listed at the end of each task.

---

## Why Agents Also Need Real Tools (Answer to Your Question)

Your agents currently have limited "eyes":
- `SignalAgent` sees only OHLCV candles → price patterns only.
- `SentimentAgent` reads RSS feeds → delayed, no real FII/DII data.
- `RiskAgent` checks only portfolio math → no market-wide context.

**Real production agents need live tools**:
| Tool | What It Gives | Real FutureEdge Gap |
|------|--------------|---------------------|
| Real-time news API (NewsAPI, GDELT) | Breaking corporate events in seconds | RSS feeds are 15–30 min delayed |
| NSE official data API | Real FII/DII institutional flows | Currently **mocked with `random.seed()`** |
| India VIX live feed | Market fear index | Not connected |
| Earnings calendar (NSE/BSE) | Know before results day | Not built |
| Options chain (Zerodha) | Implied volatility, put/call ratio | Not connected |

You will build real tool integrations in Phase 4 of this curriculum.

---

# PHASE 1: INFRASTRUCTURE — Wire Everything Together
> **Goal**: Write Docker, Docker Compose, and CI/CD from memory. Understand what every line does.

---

## Day 1 — Dockerfile: The Container Blueprint

**What you build**: Write Dockerfiles for a Python backend and a Next.js frontend from scratch.

**The mental model**:
```
Dockerfile = a recipe. Each line is an instruction.
Layer caching = Docker re-uses unchanged lines, so order matters.
COPY requirements.txt BEFORE copying code → dependencies layer is cached.
```

### Backend Dockerfile Task
Write `/practice/phase1/day1/backend/Dockerfile`:
```
1. Start FROM python:3.12-slim
2. Set WORKDIR /app
3. COPY requirements.txt .
4. RUN pip install --no-cache-dir -r requirements.txt   ← separate layer for caching
5. COPY . .
6. EXPOSE 8000
7. CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Write `/practice/phase1/day1/frontend/Dockerfile`:
```
1. FROM node:20-alpine AS deps
2. WORKDIR /app
3. COPY package*.json ./
4. RUN npm ci                         ← ci = clean install, reproducible
5. FROM node:20-alpine AS runner
6. WORKDIR /app
7. COPY --from=deps /app/node_modules ./node_modules
8. COPY . .
9. EXPOSE 3000
10. CMD ["npm", "run", "dev"]
```

**Questions to answer yourself before moving on:**
- Why do we `COPY requirements.txt` before `COPY . .`?
- What does `--no-cache-dir` do and why does it matter in containers?
- What is the difference between `CMD` and `ENTRYPOINT`?

**Compare with**: [backend/Dockerfile](file:///Users/baijuyadav/Desktop/futureedge/backend/Dockerfile)

---

## Day 2 — Docker Compose: Orchestrating Services

**What you build**: Write a complete `docker-compose.yml` for 5 services: backend, frontend, PostgreSQL, Redis, Qdrant.

**The mental model**:
```
docker-compose = a conductor. It starts services in the right order.
depends_on + healthcheck = service A will not start until service B is truly ready.
volumes = persistent data that survives container restarts.
networks = containers can talk to each other by service name ("postgres", not "localhost").
```

### Task
Write `practice/phase1/day2/docker-compose.yml`:

1. `postgres` service:
   - Image: `postgres:16`
   - Environment: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
   - Healthcheck: `pg_isready -U myuser` every 5 seconds
   - Volume: `postgres_data:/var/lib/postgresql/data`

2. `redis` service:
   - Image: `redis:7`
   - Healthcheck: `redis-cli ping`

3. `qdrant` service:
   - Image: `qdrant/qdrant:latest`
   - Volume: `qdrant_data:/qdrant/storage`

4. `backend` service:
   - `build: ./backend`
   - `depends_on: postgres (condition: service_healthy), redis (condition: service_healthy)`
   - `env_file: .env`

5. `frontend` service:
   - `depends_on: backend`
   - `environment: BACKEND_URL=http://backend:8000`

**Questions to answer yourself:**
- Why use `condition: service_healthy` instead of just `depends_on: postgres`?
- What happens if you remove the `volumes` section — where does PostgreSQL store data?
- How does the frontend container reach the backend? (hint: container networking)

**Compare with**: [docker-compose.yml](file:///Users/baijuyadav/Desktop/futureedge/docker-compose.yml)

---

## Day 3 — GitHub Actions CI/CD: Automated Testing

**What you build**: Write a CI pipeline that runs on every push — installs dependencies, lints code, runs tests with real PostgreSQL and Redis.

**The mental model**:
```
GitHub Actions = a robot that runs your code on every git push.
services: = Docker containers that start alongside your tests.
steps: = sequential commands the robot runs.
env: = environment variables the tests can read.
```

### Task
Write `practice/phase1/day3/.github/workflows/ci.yml`:

1. Trigger on push to `main` and `develop` branches.
2. Start a `postgres:16` service container with health checks.
3. Start a `redis:7` service container with health checks.
4. Steps:
   - `actions/checkout@v4` — checkout code
   - `actions/setup-python@v5` with `python-version: "3.12"` and pip caching
   - `pip install -r requirements.txt pytest pytest-asyncio httpx`
   - Set environment variables: `POSTGRES_HOST=localhost`, `REDIS_HOST=localhost`, `APP_ENV=test`
   - Run: `ruff check app/ --select E,F`  (lint)
   - Run: `pytest tests/ --asyncio-mode=auto --cov=app --cov-fail-under=40 -v`

**Questions to answer yourself:**
- Why is `POSTGRES_HOST=localhost` and not `postgres` in CI (unlike Docker Compose)?
- What does `cache-dependency-path` do in `setup-python`?
- What does `--cov-fail-under=40` mean? What happens if coverage drops below 40%?

**Compare with**: [.github/workflows/ci.yml](file:///Users/baijuyadav/Desktop/futureedge/.github/workflows/ci.yml)

---

# PHASE 2: DATA LAYER — Connect Databases and Write Queries
> **Goal**: Connect to PostgreSQL, Redis, and Qdrant. Write every type of query from memory.

---

## Day 4 — PostgreSQL: Models, Sessions, and Repository Pattern

**What you build**: Full async database setup with SQLAlchemy — models, session factory, and a repository class.

**The mental model**:
```
Model  = Python class that maps to a DB table (one class = one table)
Session = a database "conversation" — open, run queries, commit, close
Repository = a class that owns all SQL for one model (TradeRepo owns Trade queries)
```

### Task
Create `practice/phase2/day4/`:

**`db/models.py`** — Write these models:
```python
# User model
class User(Base):
    id: UUID (primary key, default=uuid4)
    email: str (unique, not null)
    hashed_password: str
    role: str  # "viewer" | "trader" | "admin"
    created_at: datetime (server_default=now())

# Trade model
class Trade(Base):
    id: UUID
    user_id: UUID  (ForeignKey → User.id)
    symbol: str
    direction: str  # "LONG" | "SHORT"
    quantity: int
    entry_price: float
    stop_loss: float
    take_profit: float
    status: str  # "OPEN" | "CLOSED"
    realized_pnl: float (nullable)
    opened_at: datetime
    closed_at: datetime (nullable)
```

**`db/session.py`** — Write the async session factory:
```python
engine = create_async_engine(DATABASE_URL, pool_size=10, max_overflow=20, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
```

**`db/repos/trade_repo.py`** — Write these repository methods:
- `save_trade(session, **kwargs) → Trade` — insert and return the new trade
- `get_open_trades(session, user_id) → list[Trade]` — fetch all OPEN trades for user
- `close_trade(session, trade_id, exit_price) → Trade` — update status=CLOSED, set realized_pnl
- `get_recent_closed(session, symbol, user_id, limit=50) → list[Trade]` — for Kelly calculation
- `calculate_win_stats(trades) → dict` — pure Python: return `{win_rate, avg_win, avg_loss}`

**`main.py`** — Test: insert a trade, fetch it, close it, print the result.

**Compare with**: [db/models/trade.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/db/models/trade.py), [db/repos/trade_repo.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/db/repos/trade_repo.py)

---

## Day 5 — Redis: All 4 Usage Patterns

**What you build**: A Python script that demonstrates every Redis pattern used in FutureEdge: key-value cache, TTL expiry, pub/sub messaging, and streams.

### Task
Create `practice/phase2/day5/redis_patterns.py`:

**Pattern 1 — Key-value cache with TTL (indicator cache)**:
```python
# Store
await redis.setex("indicator:RSI:RELIANCE", 60, json.dumps({"rsi": 34.5}))
# Read
raw = await redis.get("indicator:RSI:RELIANCE")
# Check None (expired or never set)
```

**Pattern 2 — Kill switch boolean**:
```python
await redis.set("TRADING_HALT", "1")
halt = await redis.get("TRADING_HALT")
assert halt == "1"
await redis.delete("TRADING_HALT")
```

**Pattern 3 — Pub/Sub (agent results broadcast)**:
Write a publisher coroutine that publishes `{"agent": "SignalAgent", "vote": "BUY"}` to `"futureedge:agent_results"` every 2 seconds.
Write a subscriber coroutine that listens and prints every received message.
Run both with `asyncio.gather(publisher(), subscriber())`.

**Pattern 4 — Stream (tick feed)**:
```python
# Append a tick
await redis.xadd("ticks:RELIANCE", {"price": "2450.5", "volume": "12000"}, maxlen=1000)
# Read last 5 ticks
entries = await redis.xrevrange("ticks:RELIANCE", count=5)
```

**Compare with**: [db/redis.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/db/redis.py), [data/indicator_cache.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/data/indicator_cache.py)

---

## Day 6 — Qdrant: Vector Memory Store and Similarity Search

**What you build**: Store trade memories as vectors in Qdrant and recall the most similar past trades.

**The mental model**:
```
Vector DB = a database that stores lists of numbers (vectors).
Similarity search = find stored vectors mathematically closest to your query vector.
Use case in FutureEdge: "Find me the 5 past trades made in market conditions similar to right now."
```

### Task
Create `practice/phase2/day6/qdrant_memory.py`:

1. Connect to Qdrant and create a collection `"trade_memories"` with 6-dimensional vectors if not exists.

2. Write `build_memory_vector(rsi, macd_hist, volatility, sentiment, buy_score, risk_score) → list[float]`:
   - Normalise: `rsi/100`, `tanh(macd_hist/10)`, `min(volatility/0.20, 1.0)`, `(sentiment+1)/2`, `buy_score`, `1-risk_score`

3. Write `store_memory(run_id, symbol, vector, decision, outcome)`:
   - Use `PointStruct(id=str(uuid4()), vector=vector, payload={run_id, symbol, decision, outcome})`
   - Call `client.upsert()` in a `run_in_executor`

4. Write `recall_similar(vector, symbol, limit=5) → list[dict]`:
   - Use `client.search()` with a `Filter` on `symbol`
   - Return list of `{payload, score}`

5. Test: store 10 fake memories. Then recall with a new vector. Print similarity scores.

**Compare with**: [memory/embedder.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/memory/embedder.py), [memory/qdrant_store.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/memory/qdrant_store.py)

---

# PHASE 3: APIs, AUTH, AND WEBSOCKETS
> **Goal**: Build production-grade HTTP APIs with auth, plus real-time WebSocket streaming.

---

## Day 7 — FastAPI: Full REST API with Dependency Injection

**What you build**: A complete FastAPI app with proper request validation, response schemas, error handling, and dependency injection chains.

### Task
Create `practice/phase3/day7/` — a full FastAPI app:

**Route structure to build**:
- `POST /trades` — create a trade (body: `symbol`, `direction`, `quantity`, `entry_price`, `stop_loss`, `take_profit`)
- `GET /trades` — list user's trades (query params: `limit=100`, `offset=0`, `status="OPEN"`)
- `GET /trades/{trade_id}` — single trade (return 404 if not found)
- `POST /trades/{trade_id}/close` — close an open trade (return 400 if already closed)
- `DELETE /trades/{trade_id}` — soft delete (set `status="DELETED"`)

**Patterns to implement**:
- Pydantic `BaseModel` for request/response schemas with field validation
- `Depends(get_db)` on every route
- `HTTPException(status_code=404)` for missing resources
- `HTTPException(status_code=400)` for invalid operations
- A `@router.get` router with prefix `/api/v1`

**Compare with**: [api/routes/trades_router.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/api/routes/trades_router.py)

---

## Day 8 — Auth: JWT + bcrypt + Role-Based Access Control

**What you build**: Complete auth system — register, login, token refresh, and protected routes.

**The mental model**:
```
bcrypt = a one-way hash. You store the hash, never the password.
JWT = a signed token. Server signs it with a secret. Client sends it on every request.
Access token = short-lived (30 min). Refresh token = long-lived (7 days), stored in DB.
RBAC = Role-Based Access Control. viewer < trader < admin.
```

### Task
Create `practice/phase3/day8/`:

**`auth/jwt.py`** — Write these functions:
```python
def create_access_token(user_id: str, role: str) -> str:
    # jwt.encode({"sub": user_id, "role": role, "exp": now + 30min}, SECRET_KEY)

def create_refresh_token(user_id: str) -> str:
    # jwt.encode({"sub": user_id, "exp": now + 7days}, SECRET_KEY)

def decode_token(token: str) -> dict:
    # jwt.decode(token, SECRET_KEY) — raise 401 if expired or invalid
```

**`auth/dependencies.py`** — Write these dependencies:
```python
async def get_current_user(token = Depends(oauth2_scheme), db = Depends(get_db)) -> User:
    payload = decode_token(token)
    user = await UserRepo.get_by_id(db, payload["sub"])
    if not user: raise HTTPException(401)
    return user

async def require_trader(user = Depends(get_current_user)) -> User:
    if user.role not in ("trader", "admin"): raise HTTPException(403)
    return user
```

**`api/routes/auth_router.py`** — Build these routes:
- `POST /auth/register` — hash password with bcrypt, create user, return `{id, email}`
- `POST /auth/login` — verify bcrypt hash, return `{access_token, refresh_token}`
- `POST /auth/refresh` — validate refresh token from DB, revoke old, issue new pair
- `POST /auth/logout` — mark refresh token as revoked in DB

**Compare with**: [auth/jwt.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/auth/jwt.py), [auth/dependencies.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/auth/dependencies.py)

---

## Day 9 — WebSocket: Real-Time Streaming with Redis Pub/Sub

**What you build**: A WebSocket endpoint that relays Redis pub/sub messages to browser clients in real time. This is the exact live price/agent-vote feed pattern.

### Task
Create `practice/phase3/day9/`:

**Step 1 — Background tick publisher**:
Write a `TickPublisher` background job class (use the background job pattern from README_9):
- Every 1 second, generate a fake price: `price = base_price * random.uniform(0.998, 1.002)`
- Publish to Redis: `await redis.publish("ticks:RELIANCE", json.dumps({"price": price, "ts": timestamp}))`

**Step 2 — WebSocket endpoint**:
```python
@router.websocket("/ws/live/{symbol}")
async def live_feed(websocket: WebSocket, symbol: str):
    await websocket.accept()
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"ticks:{symbol}")
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        await pubsub.unsubscribe(f"ticks:{symbol}")
```

**Step 3 — HTML test client**:
Write `test.html` with a `<script>` that opens `ws://localhost:8000/ws/live/RELIANCE` and appends each price to a `<div>`. Open in browser, verify prices stream.

**Compare with**: [api/routes/market_router.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/api/routes/market_router.py)

---

# PHASE 4: AI — TOOLS, MEMORY, AGENTS, AND TESTS
> **Goal**: Give AI agents real tools and multiple memory types. Write tests for everything.

---

## Day 10 — AI Tools & Alerts: Real-Time News, Corporate Events, and Telegram HITL

**What you build**: Implement 5 real tool functions including live market indicators, real-time news scraping, corporate events checks, and a Telegram Bot HITL notification webhook.

**Why this matters**: Real-time news and upcoming corporate events prevent technical signals from walking into event-risk traps. A mobile Telegram bot allows you to approve or veto trades from your phone on the go without sitting at your terminal all day.

### Task
Create `practice/phase4/day10/tools/`:

**Tool 1 — `get_live_price(symbol) → float`**:
- Try `yfinance.Ticker(f"{symbol}.NS").fast_info.last_price` or live broker LTP
- Return `0.0` on failure, log the error

**Tool 2 — `get_india_vix() → float`**:
- Fetch `yfinance.Ticker("^INDIAVIX").fast_info.last_price`
- Used by agents: if VIX > 20, market is fearful → reduce position sizes

**Tool 3 — `get_realtime_ticker_news(symbol) → list[dict]`**:
- Fetch news for symbol via yfinance or NewsAPI.
- Return `[{title, source, link, published}]`
- Cache in Redis for 5 minutes: key = `"news:{symbol}"`

**Tool 4 — `get_upcoming_corporate_events(symbol) → dict`**:
- Fetch upcoming calendar via `yfinance.Ticker(symbol).calendar`.
- Returns key events like upcoming earnings dates to trigger risk vetoes.

**Tool 5 — `send_telegram_alert(run_id, proposal) → dict`**:
- Send formatted HTML trade proposal messages with Inline Keyboard buttons `Approve` and `Reject` to the user's phone via `https://api.telegram.org/bot<TOKEN>/sendMessage`.

**Tool 6 — `telegram_webhook_receiver`**:
- Build a FastAPI route that listens for Telegram's callback query POST requests, extracts `action` and `run_id`, and resumes the LangGraph agent state.

**Testing your tools**:
```python
import asyncio
asyncio.run(main())   # call each tool, send a mock telegram button message, and print results
```

---

## Day 11 — AI Memory Types: Implement All 3

**What you build**: Three different memory systems used in FutureEdge — short-term (Redis), long-term (PostgreSQL), and episodic/vector (Qdrant). Understand when to use each.

**The mental model**:
```
Short-term memory  = Redis  → "What was RSI 60 seconds ago?"   (TTL, forget automatically)
Long-term memory   = PostgreSQL → "What trades did this user make this year?" (permanent records)
Episodic memory    = Qdrant → "What market conditions led to a WIN before?" (similarity search)
```

### Task
Create `practice/phase4/day11/memory/`:

**`short_term.py` — Redis indicator cache**:
```python
async def cache_indicators(symbol: str, data: dict, ttl: int = 60):
    await redis.setex(f"ind:{symbol}", ttl, json.dumps(data))

async def get_cached_indicators(symbol: str) -> dict | None:
    raw = await redis.get(f"ind:{symbol}")
    return json.loads(raw) if raw else None
```

**`long_term.py` — PostgreSQL trade log**:
```python
async def log_agent_decision(session, symbol, agent_votes: dict, final_decision: str, outcome: str = None):
    # Insert a row into an AgentLog table
    # Fields: symbol, agent_votes (JSONB), final_decision, outcome, logged_at

async def get_agent_accuracy(session, agent_name: str) -> float:
    # SELECT count(*) WHERE agent_votes->>'AgentName' = final_decision / total
    # Return the win rate for a specific agent
```

**`episodic.py` — Qdrant similarity memory**:
- Implement `store(vector, metadata)`, `recall(vector, filter_symbol, limit=5)`
- Write a test that stores 20 memories and verifies the top recalled memory is genuinely similar

---

## Day 12 — One Agent: Signal Agent with Tools + Memory

**What you build**: A complete `SignalAgent` that uses real tools AND all 3 memory types.

### Task
Create `practice/phase4/day12/agents/signal_agent.py`:

```python
async def signal_agent_node(state: dict) -> dict:
    symbol = state["symbol"]
    candles = state["candles"]

    # Step 1: Try short-term memory first
    cached = await get_cached_indicators(symbol)
    if cached:
        ind = cached
    else:
        # Step 2: Compute fresh indicators
        ind = compute_indicators(candles)   # RSI, MACD, Bollinger
        await cache_indicators(symbol, ind, ttl=60)

    # Step 3: Get live market tool data
    live_price = await get_live_price(symbol)
    vix = await get_india_vix()

    # Step 4: Compute score with regime awareness
    score = compute_score(ind, vix)

    # Step 5: Decision
    if score > 0.2:   decision = "BUY"
    elif score < -0.2: decision = "SELL"
    else:             decision = "HOLD"

    # Step 6: Log to long-term memory (PostgreSQL)
    await log_agent_decision(session, symbol, {"SignalAgent": decision}, decision)

    return {"signal_vote": {"agent": "SignalAgent", "decision": decision, "confidence": ...}}
```

---

## Day 13 — Write Tests: Unit Tests and Integration Tests

**What you build**: A complete test suite using `pytest` and `pytest-asyncio`.

**The mental model**:
```
Unit test = test ONE function in isolation, mock its dependencies
Integration test = test multiple components together with a real DB
The "Arrange, Act, Assert" pattern: set up data → call the function → verify the result
```

### Task
Create `practice/phase4/day13/tests/`:

**`test_kelly.py` — Unit test (no DB needed)**:
```python
from trade_repo import calculate_win_stats

def test_kelly_with_good_history():
    trades = [{"realized_pnl": 1000}, {"realized_pnl": -400}, {"realized_pnl": 800}]
    stats = calculate_win_stats(trades)
    assert stats["win_rate"] == pytest.approx(2/3, 0.01)
    assert stats["avg_win"] > stats["avg_loss"]

def test_kelly_with_no_history():
    stats = calculate_win_stats([])
    assert stats["win_rate"] == 0.5   # safe default
```

**`test_auth.py` — Integration test (needs real DB)**:
```python
@pytest.mark.asyncio
async def test_register_and_login(async_client):
    # Register
    r = await async_client.post("/auth/register", json={"email": "test@test.com", "password": "pass123"})
    assert r.status_code == 200

    # Login
    r = await async_client.post("/auth/login", json={"email": "test@test.com", "password": "pass123"})
    assert r.status_code == 200
    assert "access_token" in r.json()

async def test_protected_route_without_token(async_client):
    r = await async_client.get("/trades")
    assert r.status_code == 401
```

**`test_signal_agent.py` — Agent test**:
```python
@pytest.mark.asyncio
async def test_signal_agent_buy_on_oversold():
    # Arrange: create candles that would make RSI go below 30
    candles = generate_downtrending_candles(n=50)
    state = {"symbol": "TEST", "candles": candles}

    # Act
    result = await signal_agent_node(state)

    # Assert
    assert result["signal_vote"]["decision"] == "BUY"
    assert result["signal_vote"]["confidence"] > 0.6
```

**`conftest.py`** — Write the test fixtures:
```python
@pytest.fixture
async def async_client():
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

@pytest.fixture(autouse=True)
async def clean_db():
    # Clear all tables before each test
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE TABLE trades, users CASCADE"))
        await session.commit()
```

---

## Day 14 — Full Mini FutureEdge: Everything Connected

**This is the capstone of Phase 4.**

Build a working mini trading system with all components:

### What to build in `practice/phase4/day14/`:

```
.
├── Dockerfile
├── docker-compose.yml
├── .github/workflows/ci.yml
├── app/
│   ├── main.py              (FastAPI + lifespan)
│   ├── db/
│   │   ├── models.py        (User, Trade)
│   │   ├── session.py       (AsyncSessionLocal)
│   │   └── repos/
│   │       └── trade_repo.py
│   ├── auth/
│   │   ├── jwt.py
│   │   └── dependencies.py
│   ├── memory/
│   │   ├── short_term.py    (Redis)
│   │   ├── long_term.py     (PostgreSQL)
│   │   └── episodic.py      (Qdrant)
│   ├── tools/
│   │   ├── price_tool.py
│   │   ├── news_tool.py
│   │   └── market_status.py
│   ├── agents/
│   │   ├── signal_agent.py
│   │   ├── risk_agent.py
│   │   └── orchestrator.py
│   ├── jobs/
│   │   └── exit_monitor.py
│   └── api/
│       ├── auth_router.py
│       ├── trades_router.py
│       └── ws_router.py      (WebSocket live feed)
└── tests/
    ├── conftest.py
    ├── test_auth.py
    ├── test_trades.py
    └── test_agents.py
```

### The full flow to implement:
```
POST /auth/login → JWT
    ↓
POST /api/run-cycle {symbol} [JWT required]
    ↓
    SignalAgent (Redis cache + live price tool + RSI/MACD)
    RiskAgent (PostgreSQL Kelly + portfolio checks)
    → Orchestrator (weighted consensus → BUY/SELL/HOLD)
    ↓
    If BUY: save Trade to PostgreSQL, store vector in Qdrant
    Publish to Redis: "futureedge:agent_results"
    ↓
Background ExitMonitor (every 10s)
    → Close trades hitting SL/TP
    → Update Qdrant memory outcome
    ↓
WebSocket /ws/live
    → Browser gets agent vote results in real-time
```

---

## Day 15 — The Final Test: Blind Rewrite

Pick ONE real FutureEdge file and rewrite it entirely from memory. No looking at the source file.

**Options (pick what is hardest for you)**:
- **A**: [exit_monitor.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/jobs/exit_monitor.py) — background job, DB queries, Redis events
- **B**: [risk_agent.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/agents/risk_agent.py) — Kelly criterion, VETO logic, 5 safety checks
- **C**: [orchestration_agent.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/agents/orchestration_agent.py) — weighted consensus, HITL, SL/TP, Redis publish

After writing, compare line-by-line. Every difference = a gap in your muscle memory. Fix those gaps. You are ready when the diff is small.

---

## Master Reference Card (Stick This to Your Wall)

### The Pattern Every Day Repeats
```
Infrastructure → Data Layer → Auth → Tools → Agents → Tests
```

### 6 Core Code Patterns (Memorise These)

**1. Async DB session**
```python
async with AsyncSessionLocal() as session:
    result = await session.execute(select(Trade).where(Trade.status == "OPEN"))
    trades = result.scalars().all()
```

**2. Redis cache with TTL**
```python
await redis.setex(f"cache:{key}", 60, json.dumps(data))
raw = await redis.get(f"cache:{key}")
data = json.loads(raw) if raw else None
```

**3. Redis pub/sub**
```python
await redis.publish("channel", json.dumps(payload))        # publish
async for msg in pubsub.listen():                         # subscribe
    if msg["type"] == "message": process(msg["data"])
```

**4. Run blocking code in async context**
```python
result = await asyncio.get_running_loop().run_in_executor(None, sync_function, arg)
```

**5. Background job class**
```python
class MyJob:
    async def start(self): self._task = asyncio.create_task(self._loop())
    async def _loop(self):
        while self._running:
            try: await self._do_work()
            except Exception as e: logger.error(e)
            await asyncio.sleep(10)
```

**6. Agent vote pattern**
```python
async def my_agent(state) -> dict:
    try:
        score = compute_score(state["candles"])
        decision = "BUY" if score > 0.2 else "SELL" if score < -0.2 else "HOLD"
        return {"my_vote": AgentVote(agent="MyAgent", decision=decision, confidence=...)}
    except Exception as e:
        return {"my_vote": AgentVote(agent="MyAgent", decision="HOLD", confidence=0.1)}
```
