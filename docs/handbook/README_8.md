# README 8 — DEBUGGING GUIDE

## How to Debug Any Failure in FutureEdge

---

## Debugging Infrastructure

### Log format
FutureEdge uses `loguru`. Every important event is logged with structured fields:
```
2026-05-29 10:42:15 | INFO     | app.agents.orchestration_agent:orchestrator_node:87 — 
🎯 Orchestrator | NIFTY 50 | LONG | buy=0.68 sell=0.12 | risk=0.42 | hitl=False
```

### Log levels used
- `DEBUG` — cache hits, minor events, indicator values
- `INFO` — successful state transitions, trades saved, weights updated
- `WARNING` — non-fatal failures, missing keys, Redis failures (system continues)
- `ERROR` — important failures with context (broker errors, DB errors)
- `CRITICAL` — kill switch activations, daily loss cap breached

### Key log search patterns
```bash
# All logs for a specific run
grep "run_id=a1b2c3" app.log

# All HITL events
grep "HITL\|interrupt\|GraphInterrupt\|human_review" app.log

# Execution failures only
grep "execution_error\|KILL_SWITCH\|POSITION_TOO_SMALL\|VETO" app.log

# Agent votes for a cycle
grep "RegimeAgent\|SignalAgent\|SentimentAgent\|RiskAgent\|PortfolioAgent\|Orchestrator" app.log | grep "2026-05-29 10:42"
```

---

## Failure Type 1: "No Trade Was Placed" (direction=NONE or execution_error)

### Symptom
API returns `"direction": "NONE"` or `"execution_error": "POSITION_TOO_SMALL"`.

### Debug flow

**Step 1 — Find which agent vetoed or voted HOLD**
```bash
grep "signal_vote\|risk_vote\|sentiment_vote\|VETO\|HOLD" app.log | tail -50
```

**Step 2 — Check if kill switch is active**
```bash
# In Redis CLI
redis-cli get TRADING_HALT
# Returns: "1" if halted, nil if not
```

**Step 3 — Check if Kelly fraction is too small**
```python
# risk_agent.py output
# Look for: "kelly_fraction=0.001" — this means no closed trades yet
# Small equity × small kelly = position_rupees too small to buy even 1 share

# Fix: need at least 10 closed trades for proper Kelly
# Interim fix: increase DEFAULT_POSITION_PCT in risk_agent
```

**Step 4 — Check VETO from risk_agent**
Look for:
```
WARNING | RiskAgent | VETO | reason=HIGH_VOLATILITY | vol=0.09 (threshold=0.08)
WARNING | RiskAgent | VETO | reason=MARGIN_CRITICAL | margin_used=82% (threshold=80%)
WARNING | RiskAgent | VETO | reason=HIGH_SYMBOL_EXPOSURE | position=31% (threshold=30%)
```

**Step 5 — Check score thresholds**
```python
# orchestrator_node:
# if buy_score > 0.55 → LONG
# If buy_score = 0.48 → NONE (just below threshold)

# Solutions:
# - Lower threshold in orchestrator (riskier)
# - Check why agent confidence is low
```

**Step 6 — Check direction == NONE before execution**
```python
# execution_node line ~67:
if proposal.direction == "NONE":
    return {"executed_trade": {"status": "NONE"}, ...}
# This is normal — the system decided not to trade
```

---

## Failure Type 2: HITL Workflow Stuck in PENDING

### Symptom
Workflow status stays `"HITL_PENDING"` permanently. Resume API returns error.

### Debug flow

**Step 1 — Verify the checkpoint exists in PostgreSQL**
```sql
-- Check if the checkpoint was saved
SELECT thread_id, created_at FROM checkpoints WHERE thread_id = 'a1b2c3d4';
-- If no rows: interrupt() never fired, or checkpointer connection failed
```

**Step 2 — Verify the GraphInterrupt was properly re-raised**
```python
# human_agent.py — THIS IS THE CRITICAL BUG
try:
    human_response = interrupt(payload)
except GraphInterrupt:
    raise   # ← MUST be here. If missing, interrupt() is swallowed and state not saved.
except Exception as e:
    return await _reject(state, ...)   # only for real errors
```

**Step 3 — Check if thread_id matches between run and resume**
```python
# run_workflow response: {"thread_id": "a1b2c3d4"}
# resume_workflow request body: {"thread_id": "a1b2c3d4"}   ← must match exactly
```

**Step 4 — Check PostgreSQL checkpointer connection**
```python
# runtime.lifespan() — the checkpointer must stay alive
async with AsyncPostgresSaver.from_conn_string(db_uri) as checkpointer:
    workflow_graph = create_graph().compile(checkpointer=checkpointer)
    yield   # ← if this context exits before resume, checkpointer is gone
```

**Step 5 — Check WorkflowRun DB record**
```sql
SELECT status, hitl_required FROM workflow_runs WHERE run_id = 'a1b2c3d4';
-- Should be: status='HITL_PENDING', hitl_required=true
-- If status='COMPLETED': workflow already resumed (check if trade was placed)
-- If not found: run_workflow() failed to save the record
```

**Step 6 — Try resuming manually via curl**
```bash
curl -X POST http://localhost:8000/api/v1/workflow/resume \
  -H "Authorization: Bearer $RISK_MANAGER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"thread_id": "a1b2c3d4", "decision": "APPROVE", "notes": "manual test"}'
```

---

## Failure Type 3: Indicators Not Computing (signal_agent returns HOLD constantly)

### Symptom
SignalAgent always returns HOLD with confidence 0.5.

### Debug flow

**Step 1 — Check candles are loading**
```python
# workflow_router.run_workflow():
candles = load_historical_candles("NIFTY 50", period="2d", interval="1m")
# Add: logger.info(f"Candles loaded: {len(candles)}")
# If 0: yfinance failed, market closed, or symbol format wrong
```

**Step 2 — Check yfinance symbol mapping**
```python
# data/feed.py _to_yfinance_symbol()
# "NIFTY 50"  → "^NSEI"   (correct)
# "NIFTY50"   → "^NSEI"   (correct)
# "NIFTY 50 " → "NIFTY 50 .NS"   (WRONG — trailing space!)
```

**Step 3 — Check if RSI needs enough data**
```python
# RSI 14-period needs at least 15 candles
# MACD (12,26,9) needs at least 35 candles
# If len(candles) < 30: signal_agent returns HOLD immediately
```

**Step 4 — Check Redis indicator cache**
```bash
redis-cli get "futureedge:indicators:NIFTY 50"
# If returns data: indicator is being cached correctly
# If empty: either not computed yet or TTL expired
# TTL check:
redis-cli ttl "futureedge:indicators:NIFTY 50"
# Returns: seconds remaining, or -2 if not set
```

**Step 5 — Run indicator computation directly**
```python
# scratch test
from app.data.feed import load_historical_candles
from app.data.indicator_cache import get_indicators

candles = load_historical_candles("NIFTY 50", period="5d", interval="1m")
import asyncio
indicators = asyncio.run(get_indicators("NIFTY 50", candles))
print(indicators)
# Should show: {rsi: ..., macd: ..., macd_signal: ..., bollinger_upper: ...}
```

---

## Failure Type 4: Qdrant Not Storing or Retrieving Memories

### Symptom
`episodic_memory: []` on every cycle even after many trades.

### Debug flow

**Step 1 — Check if Qdrant is running**
```bash
curl http://localhost:6333/collections
# Should return: {"result": {"collections": [{"name": "trade_memories"}]}}
# If connection refused: Qdrant not running → docker-compose up qdrant
```

**Step 2 — Check if collection was created**
```bash
curl http://localhost:6333/collections/trade_memories
# Should return vectors config: {size: 12, distance: "Cosine"}
# If 404: init_collection() never ran → check runtime.lifespan() logs
```

**Step 3 — Check if points are being stored**
```bash
curl -X POST http://localhost:6333/collections/trade_memories/points/scroll \
  -H "Content-Type: application/json" \
  -d '{"limit": 5, "with_payload": true}'
# Should return points if execution_agent stored them
# If empty: execution_agent either failed or trades have direction=NONE
```

**Step 4 — Check the vector dimension**
```python
# embedder.py produces exactly 12 floats
vector = build_market_vector(...)
assert len(vector) == 12   # must match VECTOR_DIMENSION = 12
```

**Step 5 — Check symbol filter in retrieval**
```python
# qdrant_store.retrieve_similar_memories() filters by symbol
# If you retrieve for "NIFTY 50" but stored as "NIFTY50", you get 0 results
# Check: how is symbol set in market_context vs how trades are stored
```

---

## Failure Type 5: WebSocket Not Receiving Updates

### Symptom
Frontend connects to WebSocket but no messages arrive after running a cycle.

### Debug flow

**Step 1 — Verify orchestrator publishes to Redis**
```bash
# Open Redis CLI and subscribe to the channel
redis-cli subscribe "futureedge:agent_results"
# Then run a workflow cycle in another tab
# Should see messages arrive in CLI
```

**Step 2 — Check WebSocket route authentication**
```python
# market_router.py WebSocket endpoint receives token as query param
# ws://localhost:8000/api/v1/market/stream?token=eyJ...
# If token is missing or expired, WebSocket closes immediately
```

**Step 3 — Check WebSocket connection status**
```javascript
// Frontend browser console
ws.readyState
// 0=CONNECTING, 1=OPEN, 2=CLOSING, 3=CLOSED
// If 3 immediately after connect: token invalid or backend rejected
```

**Step 4 — Check pub/sub subscription**
```python
# market_router.py
pubsub = redis_client.pubsub()
await pubsub.subscribe(CHANNEL_AGENT_RESULTS, CHANNEL_TRADE_EXECUTED, ...)
# If subscribe fails silently (exception caught), messages are never delivered
```

**Step 5 — Check CORS allows WebSocket origin**
```python
# main.py CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,  # e.g. ["http://localhost:3000"]
    allow_credentials=True,
)
# WebSocket connections must also pass CORS check
```

---

## Failure Type 6: Exit Monitor Not Closing Trades

### Symptom
Trades stay OPEN even after price crosses stop_loss.

### Debug flow

**Step 1 — Check if exit monitor is running**
```bash
grep "ExitMonitor started\|ExitMonitor stopped" app.log
# Should see "🟢 ExitMonitor started" at startup
# If missing: exit_monitor.start() not called in lifespan
```

**Step 2 — Verify trades have stop_loss set**
```sql
SELECT id, symbol, direction, entry_price, stop_loss, status
FROM trades
WHERE status = 'OPEN';
-- If stop_loss is NULL: exit_monitor skips these trades (by design)
-- WHERE clause: Trade.stop_loss.is_not(None)
```

**Step 3 — Check broker get_ltp returns price**
```python
# exit_monitor._evaluate_trade():
current_price = await broker.get_ltp(trade.symbol)
# In mock mode: MockBroker.get_ltp() returns a default price
# If default price doesn't cross stop_loss threshold → no exit
```

**Step 4 — Check SL direction logic**
```python
# LONG trade: SL hit if current_price <= stop_loss
# SHORT trade: SL hit if current_price >= stop_loss
# If direction is wrong in DB: wrong condition is checked
```

**Step 5 — Check daily PnL reset**
```python
# ExitMonitor._check_open_trades():
today = datetime.now(UTC).strftime("%Y-%m-%d")
if today != self._daily_pnl_reset_date:
    self._daily_pnl = 0.0
# If date comparison fails due to timezone: daily PnL never resets
# Fix: ensure all datetime comparisons use UTC consistently
```

---

## Failure Type 7: Kill Switch Not Blocking Trades

### Symptom
Trades are placed even though kill switch is active.

### Debug flow

**Step 1 — Verify kill switch is set in Redis**
```bash
redis-cli get TRADING_HALT
# Must return: "1" (as string)
# If returns: nil → kill switch was never activated or Redis cleared
# If returns: "0" → was previously halted but resumed
```

**Step 2 — Check execution_agent reads the right key**
```python
# execution_agent.py first check:
halt = await redis_client.get(KEY_TRADING_HALT)
# KEY_TRADING_HALT = "TRADING_HALT" (from redis.py)
# If you changed the key name anywhere, they must match
```

**Step 3 — Check comparison is exact string**
```python
if halt == "1":   # correct — both are str because decode_responses=True
    return {"execution_error": "KILL_SWITCH_ACTIVE"}
# If decode_responses=False: halt would be bytes b"1", and "1" != b"1"
```

---

## Failure Type 8: Agent Weights Not Updating

### Symptom
Weights always return default values even after many closed trades.

### Debug flow

**Step 1 — Check Redis for stored weights**
```bash
redis-cli get futureedge:agent_weights
# Should return JSON string if weights have been calculated
# nil if never calculated
```

**Step 2 — Check trade count meets threshold**
```python
# weight_updater.maybe_update_weights():
if total < settings.MIN_TRADES_FOR_WEIGHT_UPDATE:
    return   # not enough trades yet
if total % settings.WEIGHT_UPDATE_INTERVAL_TRADES != 0:
    return   # not at interval boundary yet

# If 15 trades: MIN_TRADES=10 passes, but 15 % 20 = 15 ≠ 0 → skip
# First update happens at trade 20
```

**Step 3 — Check agent_consensus field in closed trades**
```sql
SELECT agent_consensus FROM trades WHERE status = 'CLOSED' LIMIT 5;
-- Should be JSON array of vote objects
-- If NULL: execution_agent didn't save agent votes → accuracy calc gets 0 votes
```

**Step 4 — Check for agents with enough active votes**
```python
# _calculate_new_weights():
min_votes_req = 5
for agent, stats in agent_stats.items():
    if stats["total"] >= 5:
        accuracies[agent] = stats["correct"] / stats["total"]
    else:
        accuracies[agent] = None   # keep default weight
```

---

## Common Code Mistakes to Watch For

### Mistake 1: Not re-raising GraphInterrupt
```python
# WRONG — interrupt silently swallowed, HITL never works
try:
    response = interrupt(payload)
except Exception:
    return fallback_state

# CORRECT — re-raise GraphInterrupt specifically
try:
    response = interrupt(payload)
except GraphInterrupt:
    raise   # LangGraph needs this
except Exception:
    return fallback_state
```

### Mistake 2: Using sync Qdrant client in async context
```python
# WRONG — blocks event loop
client = QdrantClient(...)
results = client.search(...)   # synchronous!

# CORRECT — run in thread pool
loop = asyncio.get_event_loop()
results = await loop.run_in_executor(None, lambda: client.search(...))
```

### Mistake 3: expire_on_commit=True (SQLAlchemy async bug)
```python
# WRONG — accessing ORM fields after commit raises DetachedInstanceError
AsyncSessionLocal = async_sessionmaker(expire_on_commit=True)   # default!

# After session.commit(), accessing trade.id raises error
trade_id = trade.id   # DetachedInstanceError!

# CORRECT
AsyncSessionLocal = async_sessionmaker(expire_on_commit=False)
```

### Mistake 4: Importing runtime.lifespan inside a route
```python
# WRONG — creates circular import: main.py → runtime.py → builder.py → main.py
from app.graph.runtime import lifespan   # in a route file

# CORRECT — only main.py imports lifespan
```

### Mistake 5: Creating a new Redis client per request
```python
# WRONG — creates new connection per call, eats connections
async def publish_result(data):
    r = redis.Redis(...)   # new connection every time!
    await r.publish(...)

# CORRECT — use the global singleton
from app.db.redis import redis_client
await redis_client.publish(...)
```

### Mistake 6: Forgetting user_id filter in trade queries
```python
# WRONG — returns ALL users' trades
result = await session.execute(select(Trade).where(Trade.status == "CLOSED"))

# CORRECT — always scope to current user
result = await session.execute(
    select(Trade).where(Trade.status == "CLOSED", Trade.user_id == user_id)
)
```
