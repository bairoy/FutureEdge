# README 1 — COMPLETE SYSTEM FLOW

## What is FutureEdge?

A **multi-agent AI trading system** for Indian equity markets (NSE/BSE) via Zerodha Kite. A LangGraph workflow orchestrates 6 AI agents that each vote on a trade. The votes are aggregated by a weighted consensus algorithm. If confidence is high, the trade is placed automatically. If risk is high, a human risk manager must approve it first (HITL).

---

## Architecture at a Glance

```
Browser (Next.js)
    ↕  REST (JWT-protected)
FastAPI (Python)
    ↕  LangGraph graph
    ↕  PostgreSQL (trades, users, checkpoints)
    ↕  Redis     (kill switch, pub/sub, tick stream, indicator cache)
    ↕  Qdrant    (vector DB — episodic memory)
    ↕  Broker    (Zerodha Kite or MockBroker)
    ↕  yfinance  (historical candles, free)
```

---

## Full Request Lifecycle — User Triggers a Trade Cycle

### Step 1 — Frontend: User clicks "Run Agent Cycle"

**File:** `frontend/src/app/*` (Next.js page)

```typescript
// The button calls the API
const res = await fetch("/api/v1/workflow/run", {
  method: "POST",
  headers: { Authorization: `Bearer ${accessToken}` },
  body: JSON.stringify({ symbol: "NIFTY 50", use_live_data: true }),
});
```

**Data sent:**
```json
{ "symbol": "NIFTY 50", "use_live_data": true }
```

---

### Step 2 — FastAPI: Request hits the route

**File:** `backend/app/api/routes/workflow_router.py`
**Function:** `run_workflow()`
**Line:** 61

```python
@router.post("/workflow/run",
    dependencies=[Depends(rate_limit(limit=1, window_seconds=30))])
async def run_workflow(request, current_user, db):
```

**What happens here:**
1. `require_trader` dependency checks JWT → extracts `current_user`
2. Rate limiter blocks if called more than once per 30 seconds
3. Fetches live candles via `load_historical_candles()` from yfinance
4. Fetches current price via `get_current_price_yfinance()`
5. Tries to get live portfolio from broker — falls back to mock portfolio
6. Calls `run_agent_cycle(market_context, portfolio, user_id=current_user.id)`

**Data entering agent cycle:**
```python
MarketContext(
    symbol="NIFTY 50",
    current_price=22300.0,
    ohlcv_1m=[...],   # 2 days of 1-minute candles
    regime="UNKNOWN",
    volatility_24h=0.02,
)
PortfolioSnapshot(
    total_equity=100000.0,
    margin_used=10000.0,
    margin_available=90000.0,
    unrealized_pnl=3020.0,
    open_positions=[...],
)
```

---

### Step 3 — Graph Builder: Initialise LangGraph State

**File:** `backend/app/graph/builder.py`
**Function:** `run_agent_cycle()`
**Line:** 73

```python
initial_state: AgentState = {
    "market_context": market_context,
    "portfolio":      portfolio,
    "signal_vote":    None,
    "sentiment_vote": None,
    "risk_vote":      None,
    "portfolio_vote": None,
    "consensus":      None,
    "hitl_required":  False,
    "hitl_status":    "NOT_REQUIRED",
    "run_id":         "a1b2c3d4",    # uuid4[:8]
    "user_id":        "uuid-of-user",
    "episodic_memory": [],
    "logs":           [],
    "completed_nodes": [],
}

result = await graph.ainvoke(initial_state, config={"configurable": {"thread_id": run_id}})
```

The `config["thread_id"]` is how LangGraph knows which PostgreSQL checkpoint to save/resume from. This is critical for HITL pause/resume.

---

### Step 4 — Graph Execution: Node Order

**File:** `backend/app/graph/builder.py` — `create_graph()`

```
START
  ↓
regime_agent          ← classifies market (TRENDING_UP / RANGEBOUND / HIGH_VOLATILITY)
  ↓ (fan-out — all 4 run simultaneously)
signal_agent   sentiment_agent   risk_agent   portfolio_agent
  ↓ (fan-in — orchestrator waits for all 4)
orchestrator
  ↓ (conditional edge)
  ├─ human_review  (if hitl_required=True)
  │     ↓
  └─ execution      ← places actual trade
        ↓
       END
```

LangGraph's fan-out/fan-in is implemented using `add_edge()`. When multiple edges point to the same node, LangGraph waits for all upstream nodes to finish before running it.

---

### Step 5 — Regime Agent

**File:** `backend/app/agents/regime_agent.py`
**Function:** `regime_agent_node()`

**Receives:** `state["market_context"].ohlcv_1m` (raw candles)

**Computes:**
- ATR (Average True Range) — price volatility measure
- ADX (Average Directional Index) — trend strength (>25 = strong trend)
- EMA-20 slope — direction of trend
- Rolling volatility from log returns

**Returns state update:**
```python
{
    "market_context": ctx,  # ctx.regime = "TRENDING_UP" | "TRENDING_DOWN" | "RANGEBOUND" | "HIGH_VOLATILITY"
}
```

This single field (`regime`) changes how every downstream agent weights its signals.

---

### Step 6 — Signal Agent (parallel with others)

**File:** `backend/app/agents/signal_agent.py`
**Function:** `signal_agent_node()`

**Receives:** `state["market_context"]` (with regime already set)

**Computes via indicator cache:**
- RSI (14-period) — overbought/oversold
- MACD (12,26,9) — momentum crossovers
- Bollinger Bands (20,2) — price outside bands

**Regime-adaptive scoring (critical logic):**
```python
if regime == "RANGEBOUND":
    score = (rsi_score * 1.5) + (bb_score * 1.5) + (macd_score * 0.2)
    # Mean reversion amplified — RSI and Bollinger matter more

elif regime in ("TRENDING_UP", "TRENDING_DOWN"):
    score = macd_score * 1.8
    # Trend following amplified — MACD matters most

elif regime == "HIGH_VOLATILITY":
    score = ((rsi_score + macd_score + bb_score) * 0.5) * 0.5
    # Everything dampened — be conservative
```

**Returns:**
```python
{
    "signal_vote": AgentVote(
        agent="SignalAgent",
        decision="BUY",       # BUY | SELL | HOLD
        confidence=0.73,
        reasoning="Regime: Rangebound | RSI trigger (27.3) | Bollinger Band boundary trigger",
        metadata={...indicators...}
    )
}
```

---

### Step 7 — Risk Agent (parallel)

**File:** `backend/app/agents/risk_agent.py`
**Function:** `risk_agent_node()`

**Receives:** `state["portfolio"]` and `state["market_context"]`

**Runs 4 checks:**
1. Margin utilisation > 80% → **VETO**
2. Single symbol exposure > 30% → **VETO**
3. 24h volatility > 8% → **VETO**
4. Unrealized PnL stress check → **reduces confidence**

**Kelly Criterion (per user from real DB data):**
```python
# Queries PostgreSQL for this user's last 50 closed trades on this symbol
trades = await TradeRepo.get_recent_closed_trades(session, symbol, user_id, limit=50)
stats  = TradeRepo.calculate_win_stats(trades)
# f = (p*b - q) / b, then halved for safety
kelly_fraction = half_kelly   # e.g. 0.04 = risk 4% of capital
```

**Returns:**
```python
{
    "risk_vote": AgentVote(
        agent="RiskAgent",
        decision="HOLD",   # HOLD = no risk objection (or VETO = stop trade)
        confidence=0.85,
        reasoning="All risk checks passed",
        metadata={"kelly_fraction": 0.04, "margin_utilisation": 0.10}
    )
}
```

---

### Step 8 — Orchestrator (fan-in)

**File:** `backend/app/agents/orchestration_agent.py`
**Function:** `orchestrator_node()`

**Receives:** all 4 votes + market_context + portfolio

**Step-by-step logic:**

```python
# 1. Load adaptive weights from Redis (updated by weight_updater.py)
agent_weights = await get_agent_weights()
# e.g. {"SignalAgent": 0.32, "SentimentAgent": 0.28, ...}

# 2. Check for VETO (any VETO = no trade, immediately)
vetoes = [v for v in votes if v.decision == "VETO"]

# 3. Weighted scoring
for vote in votes:
    wt = agent_weights.get(vote.agent, 0.2)
    if vote.decision == "BUY":  buy_score  += wt * vote.confidence
    if vote.decision == "SELL": sell_score += wt * vote.confidence

# Normalise
buy_score  /= total_wt
sell_score /= total_wt

# 4. Decision (threshold 0.55)
if buy_score > 0.55:   direction = "LONG"
elif sell_score > 0.55: direction = "SHORT"
else:                   direction = "NONE"  (HOLD)

# 5. Position size using Kelly from risk_vote metadata
kelly_fraction = risk_vote.metadata["kelly_fraction"]  # e.g. 0.04
position_rupees = total_equity * kelly_fraction        # e.g. ₹4,000

# 6. ATR-based SL/TP
atr_proxy = volatility_24h * price          # e.g. 0.02 * 22300 = ₹446
stop_loss   = price - (1.5 * atr_proxy)    # 1.5x ATR below entry
take_profit = price + (3.0 * atr_proxy)    # 3.0x ATR above entry (2:1 R:R)

# 7. HITL evaluation
if risk_score > 0.70: hitl_required = True
if disagreement > 0.40: hitl_required = True
if position_rupees > total_equity * 0.05: hitl_required = True

# 8. Episodic memory retrieval from Qdrant
market_vector = build_market_vector(rsi, macd_hist, ..., regime, buy_score, sell_score, risk_score)
episodic_memories = await retrieve_similar_memories(market_vector, symbol, limit=5)

# 9. LLM rationale (GPT/local model)
llm_rationale = await generate_trade_rationale(symbol, direction, votes, ...)

# 10. Publish to Redis pub/sub (frontend WebSocket picks this up instantly)
await redis_client.publish(CHANNEL_AGENT_RESULTS, json.dumps(payload))
```

**Data returned:**
```python
{
    "consensus": TradeProposal(
        symbol="NIFTY 50",
        direction="LONG",
        size=4000.0,       # ₹4,000
        entry_price=22300.0,
        stop_loss=21633.1,
        take_profit=23638.0,
        risk_score=0.42,
        llm_rationale="Signal agent detected RSI oversold at 27.3...",
        agent_consensus=[...votes...]
    ),
    "hitl_required": False,
    "hitl_status": "NOT_REQUIRED",
    "episodic_memory": [...],
}
```

---

### Step 9a — Human Review Node (if HITL required)

**File:** `backend/app/agents/human_agent.py`
**Function:** `human_review_node()`

```python
human_response = interrupt({
    "type": "human_review",
    "run_id": run_id,
    "symbol": proposal.symbol,
    "direction": proposal.direction,
    # ... full proposal details ...
})
# ← EXECUTION STOPS HERE. LangGraph saves checkpoint to PostgreSQL.
# ainvoke() returns early. The workflow is "HITL_PENDING".
```

The risk manager sees the trade in the UI. They click Approve/Reject. Frontend calls:
```
POST /api/v1/workflow/resume
{ "thread_id": "a1b2c3d4", "decision": "APPROVE", "notes": "Looks good" }
```

LangGraph resumes from checkpoint:
```python
graph.ainvoke(Command(resume={"decision": "APPROVE", "notes": "..."}), config=config)
# Execution continues from the line after interrupt()
```

---

### Step 9b — Execution Node

**File:** `backend/app/agents/execution_agent.py`
**Function:** `execution_node()`

**7 safety layers in order:**
```
1. Kill switch check → Redis KEY_TRADING_HALT == "1" → ABORT
2. No-trade check    → direction == "NONE" → SKIP (normal HOLD)
3. HITL check        → hitl_required and not human_approved → ABORT
4. Share conversion  → shares = int(₹4,000 / ₹22,300) = 0 shares → ABORT
5. Broker order      → broker.place_order(LIMIT, price+0.05%)
6. PostgreSQL write  → TradeRepo.save_trade(session, proposal, run_id, user_id)
7. Qdrant write      → store_trade_memory(run_id, vector, outcome="NEUTRAL")
8. Redis publish     → CHANNEL_TRADE_EXECUTED (frontend updates table)
```

---

### Step 10 — Response Back to Frontend

The `run_workflow` route returns:
```json
{
    "thread_id": "a1b2c3d4",
    "hitl_status": "NOT_REQUIRED",
    "direction": "LONG",
    "risk_score": 0.42,
    "proposal": { ... },
    "votes": [
        { "agent": "SignalAgent", "decision": "BUY", "confidence": 0.73, "reasoning": "..." },
        { "agent": "SentimentAgent", "decision": "BUY", "confidence": 0.65, "reasoning": "..." },
        { "agent": "RiskAgent", "decision": "HOLD", "confidence": 0.85, "reasoning": "..." },
        { "agent": "PortfolioAgent", "decision": "HOLD", "confidence": 0.8, "reasoning": "..." }
    ],
    "execution_error": null,
    "completed_nodes": ["regime_agent", "signal_agent", "sentiment_agent", "risk_agent", "portfolio_agent", "orchestrator", "execution"]
}
```

The frontend already has the live orchestrator result via WebSocket. The REST response is for the initial triggering request.

---

## Background Systems Running Continuously

### Exit Monitor (every 10 seconds)
**File:** `backend/app/jobs/exit_monitor.py`
- Queries all `OPEN` trades from PostgreSQL
- Gets current price from broker
- If price hits `stop_loss` or `take_profit` → places exit order → closes trade → updates Qdrant memory with WIN/LOSS
- If daily PnL loss > 3% of equity → auto-activates kill switch

### Position Reconciler (every 5 minutes)
**File:** `backend/app/jobs/position_reconciler.py`
- Syncs DB positions with broker's actual positions
- Detects orphaned orders
- Optionally halts trading on large discrepancies

### Weight Updater (after every 20 closed trades)
**File:** `backend/app/jobs/weight_updater.py`
- Recalculates each agent's prediction accuracy from trade history
- Stores updated weights in Redis
- Orchestrator picks them up on the next cycle

---

## WebSocket Real-Time Flow

```
Orchestrator node completes
    ↓
redis_client.publish(CHANNEL_AGENT_RESULTS, json_payload)
    ↓
Frontend WebSocket server subscribed to Redis pub/sub
    ↓
Browser receives live update — no polling, no page refresh
```

**Channels:**
- `futureedge:agent_results` — agent votes + proposal after each cycle
- `futureedge:trade_executed` — when a trade is placed or closed
- `futureedge:hitl_pending` — when a trade needs human approval
- `futureedge:kill_switch` — when trading is halted or resumed
