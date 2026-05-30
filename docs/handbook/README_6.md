# README 6 — ENGINEERING PATTERNS USED

## Pattern 1: Multi-Agent Voting / Consensus Pattern

### Where it exists
`backend/app/agents/` — all agent files
`backend/app/agents/orchestration_agent.py` — consensus logic

### Why it was used
No single algorithm is reliably right in all market conditions. Technical indicators miss sentiment. Sentiment analysis misses technical overbought signals. Risk checks are independent of signals. The voting pattern lets each agent specialise without any single one being a single point of failure.

### How to identify it in future projects
- Multiple independent "voters" each producing a `{decision, confidence, reasoning}` output
- A "conductor" that collects all votes, applies weights, and produces one final decision
- Each voter is isolated — cannot see other voters' results
- VETO mechanism: any voter can override the consensus

### Structure in code
```python
# Each agent returns a partial state update
return {"signal_vote": AgentVote(decision="BUY", confidence=0.73, ...)}

# Orchestrator collects all votes
votes = [state.get("signal_vote"), state.get("sentiment_vote"), ...]
votes = [v for v in votes if v is not None]

# Weighted scoring
for vote in votes:
    wt = agent_weights.get(vote.agent, 0.2)
    if vote.decision == "BUY":  buy_score  += wt * vote.confidence
    if vote.decision == "SELL": sell_score += wt * vote.confidence

# Final decision by threshold
if buy_score > 0.55: direction = "LONG"
```

### Alternative implementation
You could use a simple majority vote (3 of 4 say BUY → trade). But weighted confidence is better because a 95%-confidence BUY from signal_agent should outweigh a 55%-confidence BUY from sentiment_agent.

### How to use in another project
```python
# Any system where multiple data sources must be aggregated:
# - Credit scoring (multiple credit bureaus)
# - Fraud detection (multiple signal sources)
# - Medical diagnosis (multiple diagnostic tests)
# - Content moderation (multiple safety classifiers)
```

---

## Pattern 2: LangGraph State Machine (Graph Workflow)

### Where it exists
`backend/app/graph/builder.py` — graph definition
`backend/app/graph/state.py` — shared state
`backend/app/graph/runtime.py` — lifecycle management

### Why it was used
The trading workflow has a complex conditional flow:
- Fan-out (4 agents run in parallel)
- Fan-in (orchestrator waits for all 4)
- Conditional edge (HITL or direct execution)
- Pause/resume (interrupt + checkpoint)

Implementing this manually with asyncio would require semaphores, condition variables, and custom checkpoint serialisation. LangGraph handles all of this.

### How to identify it
- `StateGraph(TypedDict)` — defines the shared state schema
- `add_node()` — registers a function as a graph node
- `add_edge()` — defines sequential or fan-out dependencies
- `add_conditional_edges()` — routing based on state values
- `compile(checkpointer=checkpointer)` — makes the graph pausable
- `graph.ainvoke(state, config)` — executes the workflow

### The fan-out/fan-in pattern
```python
# Fan-out: one source, multiple destinations
builder.add_edge("regime_agent", "signal_agent")
builder.add_edge("regime_agent", "sentiment_agent")
builder.add_edge("regime_agent", "risk_agent")
builder.add_edge("regime_agent", "portfolio_agent")

# Fan-in: multiple sources, one destination
# LangGraph automatically waits for ALL sources to complete before running the destination
builder.add_edge("signal_agent",    "orchestrator")
builder.add_edge("sentiment_agent", "orchestrator")
builder.add_edge("risk_agent",      "orchestrator")
builder.add_edge("portfolio_agent", "orchestrator")
```

### The `Annotated[list, operator.add]` pattern
```python
# In AgentState
logs:            Annotated[list[str], operator.add]
completed_nodes: Annotated[list[str], operator.add]
```
This tells LangGraph to **append** new values rather than overwrite. Multiple parallel agents can safely add to `logs` without a race condition.

### Alternative implementation
Without LangGraph: raw `asyncio.gather()` for parallel nodes, `asyncio.Event` for fan-in, `pickle` for checkpoint serialisation, and custom resume logic. LangGraph replaces ~500 lines of infrastructure code.

---

## Pattern 3: Repository Pattern

### Where it exists
`backend/app/db/repos/trade_repo.py`
`backend/app/db/repos/user_repo.py`

### Why it was used
All SQL for a given table lives in one class. Routes and agents never write raw SQL — they call repo methods. This means:
- If you change the schema, only one file changes
- Routes stay thin — no business logic mixed with HTTP handling
- Repos are testable in isolation

### Structure
```python
class TradeRepo:
    @staticmethod
    async def save_trade(session, proposal, run_id, user_id, ...) -> Trade:
        # All the ORM code
        trade = Trade(...)
        session.add(trade)
        await session.commit()
        return trade

    @staticmethod
    async def get_recent_closed_trades(session, symbol, user_id, limit) -> list[Trade]:
        # SELECT with filters
        result = await session.execute(select(Trade).where(...))
        return list(result.scalars().all())
```

### How to identify it
- A class with all static methods
- Methods named `get_by_X`, `save_X`, `close_X`, `list_X`
- Methods always receive a `session` parameter (not creating their own)
- Never imported by each other — only by routes and agents

### Alternative
Active Record pattern: `trade.save()` — the ORM object manages its own persistence. Django's ORM works this way. SQLAlchemy supports it too. The Repository pattern is preferred in async FastAPI apps because you need fine-grained session control.

---

## Pattern 4: Dependency Injection (FastAPI)

### Where it exists
`backend/app/auth/dependencies.py`
`backend/app/api/dependencies/rate_limiter.py`
Every route function in `backend/app/api/routes/`

### Why it was used
Routes need multiple shared resources: database session, current user, rate limiter. FastAPI's `Depends()` injects these without the route needing to know how they're created. This also makes testing easy — you can override any dependency.

### Structure
```python
# Dependency definition (dependencies.py)
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session   # session is available during request, closed after

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db:    AsyncSession = Depends(get_db),
) -> User:
    payload = decode_token(token)
    user = await UserRepo.get_by_id(db, payload["sub"])
    return user

async def require_trader(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("trader", "risk_manager", "admin"):
        raise HTTPException(status_code=403)
    return user

# Usage in route
@router.post("/workflow/run")
async def run_workflow(
    request:      RunWorkflowRequest,
    current_user: User         = Depends(require_trader),   # ← injected
    db:           AsyncSession = Depends(get_db),            # ← injected
):
    # current_user and db are ready to use
```

### Dependency chain
```
GET /workflow/run request
    ↓ FastAPI reads Authorization header
    ↓ Depends(require_trader)
    ↓ Depends(get_current_user)
    ↓ Depends(get_db) → creates AsyncSession
    ↓ decode_token(access_token) → {"sub": user_id, "role": "trader"}
    ↓ UserRepo.get_by_id(db, user_id) → User object
    ↓ role check → passes
    ↓ route function receives (request, current_user=User, db=session)
```

---

## Pattern 5: Adapter Pattern (Broker Abstraction)

### Where it exists
`backend/app/brokers/base.py` — abstract interface
`backend/app/brokers/mock.py` — mock implementation
`backend/app/brokers/zerodha.py` — real implementation

### Why it was used
The execution_agent must place orders regardless of which broker is active. If Zerodha API changes or you switch to Alpaca or IIFL, only the adapter changes — not the execution logic.

### Structure
```python
# base.py — interface (abstract base class)
class BrokerBase(ABC):
    @abstractmethod
    async def connect(self) -> bool: ...
    @abstractmethod
    async def place_order(self, symbol, direction, quantity, order_type, price) -> OrderResult: ...
    @abstractmethod
    async def get_account(self) -> dict: ...
    @abstractmethod
    async def get_positions(self) -> list[dict]: ...
    @abstractmethod
    async def get_ltp(self, symbol) -> float: ...

# Factory function
def get_broker() -> BrokerBase:
    if settings.ACTIVE_BROKER == "zerodha":
        return ZerodhaBroker()
    return MockBroker()   # default
```

### Usage in execution_agent
```python
broker = get_broker()   # never knows if it's mock or real
order_result = await broker.place_order(
    symbol="NIFTY 50",
    direction="LONG",
    quantity=1.0,
    order_type="LIMIT",
    price=22311.0,
)
# OrderResult has: success, fill_price, order_id, status, error_message
```

### How to add a new broker
```python
class IIFLBroker(BrokerBase):
    async def connect(self) -> bool:
        # IIFL connection logic
    async def place_order(self, ...) -> OrderResult:
        # IIFL order API call

# factory function
def get_broker():
    if settings.ACTIVE_BROKER == "iifl": return IIFLBroker()
    if settings.ACTIVE_BROKER == "zerodha": return ZerodhaBroker()
    return MockBroker()
```

---

## Pattern 6: Background Task (asyncio Task)

### Where it exists
`backend/app/jobs/exit_monitor.py` — `ExitMonitor` class
`backend/app/jobs/position_reconciler.py` — `PositionReconciler` class

### Why it was used
Exit monitoring must run continuously, every 10 seconds, independently of HTTP requests. An HTTP request cannot block waiting for trade exits. The background task pattern runs alongside the main server.

### Structure
```python
class ExitMonitor:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())  # fire and forget

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self) -> None:
        while self._running:
            try:
                await self._check_open_trades()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Non-fatal: {e}")   # never crash silently
            await asyncio.sleep(10)   # wait 10 seconds

exit_monitor = ExitMonitor()   # singleton
```

### Why the singleton matters
`exit_monitor = ExitMonitor()` at module level means `from app.jobs.exit_monitor import exit_monitor` always returns the same object. If you instantiated a new one per request, you'd have thousands of competing monitor loops.

### How to add a new background job
```python
class MyJob:
    def __init__(self): self._task = None; self._running = False

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._running = False
        if self._task: self._task.cancel(); await self._task

    async def _loop(self):
        while self._running:
            try:
                await self._do_work()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Job error: {e}")
            await asyncio.sleep(interval)

my_job = MyJob()

# In runtime.lifespan():
await my_job.start()
yield
await my_job.stop()
```

---

## Pattern 7: HITL Interrupt/Resume (LangGraph)

### Where it exists
`backend/app/agents/human_agent.py`
`backend/app/api/routes/workflow_router.py` — resume endpoint

### Why it was used
High-risk trades must pause for human review. The system cannot just wait synchronously (HTTP request would timeout). LangGraph's `interrupt()` saves state to PostgreSQL and allows the workflow to be resumed later by a completely different HTTP request.

### The two-phase pattern
```
Phase 1 — PAUSE:
  run_workflow() → run_agent_cycle() → graph.ainvoke()
                                            ↓
                              human_review_node calls interrupt()
                                            ↓
                              LangGraph saves checkpoint to PostgreSQL
                              ainvoke() returns early
                                            ↓
                              run_workflow() returns {"hitl_status": "PENDING"}

Phase 2 — RESUME (different HTTP request, possibly minutes later):
  resume_workflow() → graph.ainvoke(Command(resume={...}), config={"thread_id": run_id})
                                            ↓
                              LangGraph loads checkpoint from PostgreSQL
                              Resumes at line after interrupt()
                              human_response = {"decision": "APPROVE"}
                                            ↓
                              execution_node runs → trade placed
```

### How to identify this pattern
- `interrupt(payload)` in a node function
- `except GraphInterrupt: raise` (must re-raise!)
- `graph.ainvoke(Command(resume={...}), config={"thread_id": "..."})` to resume
- A separate `status` table tracking whether workflow is paused

---

## Pattern 8: Feature Flag Pattern

### Where it exists
`backend/app/core/config.py`

### Why it was used
Two expensive AI features (FinBERT, GPT) are optional. In development or demo environments, you want the system to work without API keys or model downloads. Feature flags let you toggle functionality without code changes.

### Structure
```python
class Settings(BaseSettings):
    FINBERT_ENABLED: bool = False       # feature flag
    LLM_REASONING_ENABLED: bool = True  # feature flag
    ACTIVE_BROKER: str = "mock"         # mode flag

# Usage in sentiment_agent.py
if settings.FINBERT_ENABLED:
    sentiment_score = finbert_analyze(news)
else:
    sentiment_score = keyword_analyze(news)   # fast fallback

# Usage in llm_reasoner.py
if not settings.LLM_REASONING_ENABLED:
    return _template_rationale(...)
```

### How to identify this pattern
- Boolean settings with meaningful names: `FEATURE_ENABLED`, `USE_X`, `ENABLE_Y`
- Code branches: `if settings.X_ENABLED: use_real_impl() else: use_fallback()`
- Safe defaults that work without the real system

---

## Pattern 9: Event-Driven Real-Time Updates (Redis Pub/Sub)

### Where it exists
`backend/app/db/redis.py` — channel definitions
`backend/app/agents/orchestration_agent.py` — publisher
`backend/app/api/routes/market_router.py` — subscriber (WebSocket bridge)

### Why it was used
The frontend needs real-time updates without polling. Polling every second would hit the database 86,400 times per day per user. Redis pub/sub pushes updates to WebSocket connections instantly, with zero database load.

### Structure
```python
# Publisher (orchestrator_node, after each cycle)
await redis_client.publish(
    CHANNEL_AGENT_RESULTS,
    json.dumps({"run_id": "a1b2c3", "direction": "LONG", ...})
)

# Bridge (market_router.py WebSocket endpoint)
@router.websocket("/market/stream")
async def market_stream(websocket: WebSocket, token: str, ...):
    await websocket.accept()
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(CHANNEL_AGENT_RESULTS, CHANNEL_TRADE_EXECUTED, ...)
    async for message in pubsub.listen():
        if message["type"] == "message":
            await websocket.send_text(message["data"])
```

### How to identify this pattern
- A publisher writes to a named channel after an event
- A subscriber reads from the channel and forwards to connected clients
- No database involved in the real-time path
- WebSocket acts as the bridge between server events and browser

### How to reuse
Any system where the server needs to push state to the browser without polling:
- Order book updates
- Live logs dashboard
- Notification systems
- Collaborative editing cursor positions
