# README 9 — MEMORY BUILDING: PATTERNS TO REMEMBER

## Module: LangGraph (State + Graph)

### Core mental model
```
StateGraph = a directed graph where each node is an async function
              that receives the full shared state and returns a partial update.

The partial update is MERGED (not replaced) into state.
Annotated[list, operator.add] = special merge that appends.
```

### The pattern you will use everywhere
```python
# Node function signature is always:
async def my_node(state: MyState) -> dict:
    # read from state
    data = state["some_field"]

    # do work
    result = process(data)

    # return partial state update — only the fields you changed
    return {
        "output_field": result,
        "completed_nodes": ["my_node"],   # appends due to Annotated
        "logs": ["MyNode completed"],     # appends due to Annotated
    }
```

### The fan-out pattern
```python
# This creates parallelism:
builder.add_edge("node_a", "node_b")
builder.add_edge("node_a", "node_c")
builder.add_edge("node_b", "collector")
builder.add_edge("node_c", "collector")
# LangGraph runs node_b and node_c simultaneously, then waits for both before collector
```

### HITL pattern (always needs all 3 pieces)
```python
# 1. Node that pauses
async def hitl_node(state):
    try:
        response = interrupt({"data": "for human"})   # PAUSES HERE
    except GraphInterrupt:
        raise   # MUST re-raise

# 2. Resume
result = await graph.ainvoke(Command(resume={"decision": "APPROVE"}), config={"thread_id": "..."})

# 3. Checkpointer in compile()
graph = create_graph().compile(checkpointer=checkpointer)
# Without checkpointer, interrupt() has nowhere to save state
```

---

## Module: FastAPI Patterns

### Dependency injection chain to remember
```
Depends(get_db) → yields AsyncSession
Depends(get_current_user) calls Depends(get_db) + Depends(oauth2_scheme)
Depends(require_trader) calls Depends(get_current_user) + role check
```

Always inject at the route level:
```python
@router.post("/my-endpoint")
async def my_route(
    body:         MyRequest,
    current_user: User         = Depends(require_trader),
    db:           AsyncSession = Depends(get_db),
):
```

### Lifespan pattern
```python
@asynccontextmanager
async def lifespan(app):
    # startup
    await resource.connect()
    yield
    # shutdown
    await resource.disconnect()

app = FastAPI(lifespan=lifespan)
```

### Blocking call in async route
```python
# Always use run_in_executor for synchronous library calls
loop = asyncio.get_event_loop()
result = await loop.run_in_executor(None, sync_function, arg1, arg2)
# Or with lambda:
result = await loop.run_in_executor(None, lambda: sync_function(arg1))
```

---

## Module: SQLAlchemy Async Patterns

### Session usage (always as context manager)
```python
async with AsyncSessionLocal() as session:
    result = await session.execute(select(Model).where(Model.field == value))
    items  = result.scalars().all()   # returns list
    item   = result.scalar_one_or_none()   # returns one or None
    await session.commit()
    await session.refresh(item)   # reload after commit to get DB-generated fields
```

### The expire_on_commit=False rule
Without this, accessing any ORM field after `commit()` causes a `DetachedInstanceError` in async code. Always set `expire_on_commit=False` in `async_sessionmaker`.

### Add and commit pattern
```python
new_obj = MyModel(field1=value1, field2=value2)
session.add(new_obj)
await session.commit()
await session.refresh(new_obj)   # new_obj.id is now set
return new_obj
```

### Filtering patterns
```python
# Basic where
select(Trade).where(Trade.user_id == user_id)

# Multiple conditions (AND)
select(Trade).where(Trade.user_id == user_id, Trade.status == "OPEN")

# IS NOT NULL
select(Trade).where(Trade.stop_loss.is_not(None))

# Order + limit
select(Trade).order_by(Trade.created_at.desc()).limit(50)

# Pagination
select(Trade).offset(page * page_size).limit(page_size)
```

---

## Module: Redis Usage Patterns

### String keys (get/set)
```python
# Store
await redis_client.set("my_key", "value")
await redis_client.setex("my_key", 60, "value")   # 60-second TTL

# Read
value = await redis_client.get("my_key")   # returns str (decode_responses=True)
# value is None if key doesn't exist

# Delete
await redis_client.delete("my_key")
```

### Streams (append-only log)
```python
# Write
await redis_client.xadd("stream_name", {"field1": "value1"}, maxlen=1000)

# Read newest
entries = await redis_client.xrevrange("stream_name", count=1)
if entries:
    stream_id, fields = entries[0]
    value = fields["field1"]
```

### Pub/Sub
```python
# Publish
await redis_client.publish("channel_name", json.dumps({"data": "..."}))

# Subscribe (in async loop)
pubsub = redis_client.pubsub()
await pubsub.subscribe("channel_name")
async for message in pubsub.listen():
    if message["type"] == "message":
        data = json.loads(message["data"])
```

---

## Module: Qdrant Vector DB Patterns

### Write
```python
from qdrant_client.models import PointStruct

def _store():
    client = get_qdrant_client()
    point  = PointStruct(id=str(uuid4()), vector=[12 floats], payload={...})
    client.upsert(collection_name="my_collection", points=[point])

await loop.run_in_executor(None, _store)
```

### Read (similarity search)
```python
from qdrant_client.models import Filter, FieldCondition, MatchValue

def _search():
    results = client.search(
        collection_name="my_collection",
        query_vector=[12 floats],
        query_filter=Filter(must=[FieldCondition(key="symbol", match=MatchValue(value="NIFTY 50"))]),
        limit=5,
        with_payload=True,
    )
    return [{**hit.payload, "score": hit.score} for hit in results]

memories = await loop.run_in_executor(None, _search)
```

### Update payload
```python
def _update():
    results, _ = client.scroll(
        collection_name="my_collection",
        scroll_filter=Filter(must=[FieldCondition(key="run_id", match=MatchValue(value="a1b2c3"))]),
        limit=1,
    )
    if results:
        client.set_payload(
            collection_name="my_collection",
            payload={"outcome": "WIN"},
            points=[results[0].id],
        )
```

---

## Module: Agent Voting Patterns

### Always-implement checklist for a new agent
```python
async def my_agent_node(state: AgentState) -> dict:
    try:
        ctx = state["market_context"]

        # 1. Guard: insufficient data
        if not ctx.ohlcv_1m or len(ctx.ohlcv_1m) < MIN_CANDLES:
            return _default_vote(state, reason="insufficient data")

        # 2. Core computation
        score = compute_my_signal(ctx)

        # 3. Score to vote conversion
        if   score >  THRESHOLD: decision = "BUY";  confidence = 0.5 + score
        elif score < -THRESHOLD: decision = "SELL"; confidence = 0.5 + abs(score)
        else:                    decision = "HOLD"; confidence = 0.5

        # 4. Return vote
        return {
            "my_vote": AgentVote(
                agent="MyAgent",
                decision=decision,
                confidence=min(confidence, 0.95),
                reasoning=f"Score={score:.3f}",
                metadata={},
            ),
            "completed_nodes": ["my_agent"],
            "logs": [f"MyAgent: {decision} (confidence={confidence:.2f})"],
        }

    except Exception as e:
        logger.exception(f"MyAgent failed: {e}")
        return {
            "my_vote": AgentVote(agent="MyAgent", decision="HOLD", confidence=0.5, reasoning=f"Error: {e}"),
            "completed_nodes": ["my_agent"],
        }
```

---

## Module: Background Job Pattern

```python
class MyJob:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running: return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._do_work()
            except asyncio.CancelledError: break
            except Exception as e:
                logger.error(f"MyJob error (non-fatal): {e}")
            await asyncio.sleep(INTERVAL_SECONDS)

    async def _do_work(self) -> None:
        pass   # implement here

my_job = MyJob()   # singleton at module level
```

---

## Key Numbers to Remember

| Parameter | Value | Where |
|---|---|---|
| RSI oversold threshold | 30 | signal_agent.py |
| RSI overbought threshold | 70 | signal_agent.py |
| Bollinger std_dev | 2 | signal_agent.py |
| MACD params | fast=12, slow=26, signal=9 | indicator_cache.py |
| ADX trend threshold | 25 | regime_agent.py |
| Vote score threshold | 0.55 | orchestration_agent.py |
| HITL risk threshold | 0.70 | orchestration_agent.py |
| HITL disagreement threshold | 0.40 | orchestration_agent.py |
| ATR multiplier for SL | 1.5x | orchestration_agent.py |
| ATR multiplier for TP | 3.0x | orchestration_agent.py |
| SL price buffer (exit order) | 0.05% | exit_monitor.py |
| Kelly half-fraction cap | 25% | risk_agent.py |
| Min trades for Kelly | 10 | trade_repo.py |
| Weight update interval | 20 trades | weight_updater.py |
| Min weight per agent | 0.10 | weight_updater.py |
| Max weight per agent | 0.50 | weight_updater.py |
| Indicator cache TTL | 60 seconds | indicator_cache.py |
| Redis tick stream maxlen | 1000 | feed.py |
| Exit monitor interval | 10 seconds | exit_monitor.py |
| Rate limit (workflow) | 1/30s | workflow_router.py |
| Rate limit (login) | 5/60s | auth_router.py |
| Default pool size | 10 conns | postgres.py |
| Max pool overflow | 20 conns | postgres.py |
| Qdrant vector dimension | 12 | embedder.py, qdrant_store.py |
| Memory retrieval limit | 5 | orchestration_agent.py |
| LLM max_tokens | 300 | llm_reasoner.py |
| LLM temperature | 0.3 | llm_reasoner.py |

---

## Key Data Flows to Memorise

### Trade lifecycle data flow
```
Market data (yfinance/KiteTicker)
    → ohlcv_1m in MarketContext
    → regime_agent: ohlcv_1m → regime + volatility_24h
    → indicator_cache: ohlcv_1m → RSI, MACD, Bollinger (cached 60s in Redis)
    → signal_agent: indicators + regime → AgentVote
    → orchestrator: 4 AgentVotes + weights → TradeProposal
    → embedder: proposal + indicators → 12-dim market vector
    → Qdrant: vector → similar past memories
    → LLM: votes + memories → text rationale
    → execution_agent: TradeProposal → broker order + PostgreSQL trade row + Qdrant point
    → exit_monitor (10s loop): SL/TP hit → exit order + close_trade() + update Qdrant outcome
    → weight_updater: closed trade → recalculate agent accuracy → store weights in Redis
    → orchestrator (next cycle): reads new weights from Redis
```

### Auth data flow
```
POST /auth/login
    → bcrypt.checkpw(password, stored_hash)
    → jwt.encode(user_id, role) → access_token (30min)
    → jwt.encode(user_id) → refresh_token (7 days)
    → PostgreSQL: insert RefreshToken row
    → Response: {access_token, refresh_token}
    ↓
POST /auth/refresh
    → jwt.decode(refresh_token) → user_id
    → PostgreSQL: find RefreshToken where token=... AND is_revoked=False
    → Set old token is_revoked=True
    → Create new access_token + new refresh_token
    → PostgreSQL: insert new RefreshToken row
    → Response: {new_access_token, new_refresh_token}
    ↓
Any protected route
    → Authorization: Bearer {access_token}
    → jwt.decode → {"sub": user_id, "role": "trader"}
    → UserRepo.get_by_id(db, user_id) → User object
    → role check
    → route handler receives current_user
```

### WebSocket real-time flow
```
orchestrator_node completes
    → redis.publish("futureedge:agent_results", json_payload)
        ↓
market_router WebSocket handler (subscribed via pubsub.subscribe)
    → websocket.send_text(json_payload)
        ↓
Browser ws.onmessage
    → parse JSON
    → update React state
    → component re-renders
```
