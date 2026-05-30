# README 3 — REQUEST LIFECYCLE MASTERY

## Trace 1: User Login

### Frontend
```typescript
// User submits login form
const res = await fetch("/auth/login", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email: "trader@firm.com", password: "secret" }),
});
const { access_token, refresh_token, expires_in } = await res.json();
// Store access_token in memory (React state / Zustand)
// Store refresh_token in httpOnly cookie
```

### Route — `auth_router.py` → `login()`
```python
# File: backend/app/api/routes/auth_router.py line 99

# Rate limited: 5 calls per 60 seconds (brute-force protection)
@router.post("/login", dependencies=[Depends(rate_limit(limit=5, window_seconds=60))])
async def login(request, body: LoginRequest, db):
```

### Data transformation at each step

**Step 1 — Look up user**
```python
user = await UserRepo.get_by_email(db, body.email)
# SQL: SELECT * FROM users WHERE email = 'trader@firm.com' LIMIT 1
# Returns: User ORM object or None
```

**Step 2 — Timing-safe password check (prevents email enumeration)**
```python
# Even if user doesn't exist, we still run bcrypt.checkpw()
# This makes "wrong email" and "wrong password" take the same time
dummy_hash = "$2b$12$invalidhashfortimingnormalization..."
stored_hash = user.hashed_password if user else dummy_hash
password_correct = verify_password(body.password, stored_hash)
```

**Step 3 — Create tokens**
```python
access_token = create_access_token(user.id, user.role)
# JWT payload: {"sub": user.id, "role": "trader", "type": "access", "exp": now+30min}

refresh_token_str, refresh_expires = create_refresh_token(user.id)
# JWT payload: {"sub": user.id, "type": "refresh", "exp": now+7days}
```

**Step 4 — Store refresh token in DB**
```python
db_token = RefreshToken(
    user_id=user.id,
    token=refresh_token_str,     # full JWT string
    device_info=user_agent[:255],
    ip_address=client_ip,
    expires_at=refresh_expires,
)
db.add(db_token)
await db.commit()
```

**Step 5 — Response**
```json
{
  "access_token": "eyJhbG...",
  "refresh_token": "eyJhbG...",
  "token_type": "bearer",
  "expires_in": 1800
}
```

**Data changes at each stage:**
- Raw password string → bcrypt verification → True/False
- user.id + role → JWT signed with `JWT_SECRET_KEY` → opaque string
- Refresh token string → PostgreSQL `refresh_tokens` row → permanent until revoked

---

## Trace 2: Running a Workflow Cycle

### Frontend call
```typescript
// POST /api/v1/workflow/run
const res = await fetch("/api/v1/workflow/run", {
  method: "POST",
  headers: { Authorization: `Bearer ${accessToken}` },
  body: JSON.stringify({ symbol: "NIFTY 50", use_live_data: true }),
});
```

### Route: `workflow_router.py` → `run_workflow()`
```python
# File: backend/app/api/routes/workflow_router.py line 61

# 1. JWT authentication — require_trader checks role >= "trader"
# 2. Rate limit: 1 call per 30 seconds per user
# 3. Fetch candles via yfinance (blocking I/O → thread pool)
candles = await loop.run_in_executor(None,
    lambda: load_historical_candles("NIFTY 50", period="2d", interval="1m"))

# 4. Fetch current price
price = await loop.run_in_executor(None, get_current_price_yfinance, "NIFTY 50")

# 5. Build market context
market_context = MarketContext(
    symbol="NIFTY 50",
    current_price=22300.0,
    ohlcv_1m=[...287 candles...],
    regime="UNKNOWN",         # will be set by regime_agent
    volatility_24h=0.02,
)

# 6. Fetch live portfolio from broker
broker_acc = await broker.get_account()
broker_pos = await broker.get_positions()
portfolio = PortfolioSnapshot(total_equity=..., open_positions=...)

# 7. Run agent cycle
result = await run_agent_cycle(market_context, portfolio, user_id=current_user.id)
```

### Inside `run_agent_cycle()` — `builder.py`
```python
# Creates run_id = "a1b2c3d4" (uuid4[:8])
# Builds initial_state dict
# Calls: result = await graph.ainvoke(initial_state, config={"configurable": {"thread_id": "a1b2c3d4"}})

# LangGraph executes nodes in order:
# regime_agent → [signal, sentiment, risk, portfolio] → orchestrator → (human_review?) → execution
```

### What data looks like at each node boundary

**After regime_agent:**
```python
state["market_context"].regime = "RANGEBOUND"
state["market_context"].volatility_24h = 0.018
```

**After signal_agent:**
```python
state["signal_vote"] = AgentVote(
    agent="SignalAgent", decision="BUY", confidence=0.73,
    reasoning="RSI trigger (27.3) | Bollinger Band boundary trigger",
    metadata={"rsi": 27.3, "macd_hist": 0.12, "bollinger_lower": 22150.0}
)
```

**After risk_agent:**
```python
state["risk_vote"] = AgentVote(
    agent="RiskAgent", decision="HOLD", confidence=0.85,
    reasoning="All risk checks passed",
    metadata={"kelly_fraction": 0.04, "margin_utilisation": 0.10}
)
```

**After orchestrator:**
```python
state["consensus"] = TradeProposal(
    symbol="NIFTY 50", direction="LONG",
    size=4000.0,         # 4% Kelly × ₹100,000 equity
    entry_price=22300.0,
    stop_loss=21633.1,   # entry - 1.5×ATR
    take_profit=23634.0, # entry + 3.0×ATR
    risk_score=0.42,
    llm_rationale="3 of 4 agents voted BUY. RSI shows oversold conditions at 27...",
)
state["hitl_required"] = False
```

**After execution_agent:**
```python
state["executed_trade"] = {
    "run_id": "a1b2c3d4",
    "user_id": "uuid-of-user",
    "symbol": "NIFTY 50",
    "direction": "LONG",
    "shares": 0,           # ₹4000 / ₹22300 = 0.17 → int(0.17) = 0 shares
    "status": "FAILED",
    "error": "POSITION_TOO_SMALL",
}
# ← This is why position size matters. Low Kelly fraction on small equity = 0 shares.
```

### Route response
```json
{
    "thread_id": "a1b2c3d4",
    "hitl_status": "NOT_REQUIRED",
    "direction": "LONG",
    "risk_score": 0.42,
    "proposal": { "symbol": "NIFTY 50", "direction": "LONG", "size": 4000.0, ... },
    "votes": [...],
    "execution_error": "POSITION_TOO_SMALL",
    "completed_nodes": ["regime_agent", "signal_agent", "sentiment_agent", "risk_agent", "portfolio_agent", "orchestrator", "execution"]
}
```

---

## Trace 3: HITL — Workflow Paused, Risk Manager Approves

### Scenario: orchestrator sets `hitl_required=True`

**Trigger conditions:**
- `risk_score > 0.70`, OR
- `disagreement > 0.40`, OR
- `position_rupees > total_equity * 0.05` (>5% of equity)

### Step 1 — Orchestrator sets flag
```python
hitl_required = True
hitl_status = "PENDING"
# Also publishes to CHANNEL_HITL_PENDING via Redis pub/sub
# Frontend WebSocket receives this and shows approval modal
```

### Step 2 — LangGraph routes to human_review
```python
# Conditional edge: should_human_review(state) returns "human_review"
```

### Step 3 — human_review_node calls interrupt()
```python
# File: backend/app/agents/human_agent.py line 132
human_response = interrupt({
    "type": "human_review",
    "run_id": "a1b2c3d4",
    "symbol": "NIFTY 50",
    "direction": "LONG",
    "size": 15000.0,
    "risk_score": 0.78,
    "agent_votes": [...]
})
# ← EXECUTION STOPS HERE
# LangGraph raises GraphInterrupt internally
# The entire AgentState is serialised to PostgreSQL checkpoint
# ainvoke() returns early → workflow_router.run_workflow() gets a result
```

### Step 4 — Workflow router saves DB record
```python
workflow_run = WorkflowRun(
    user_id=current_user.id,
    run_id="a1b2c3d4",
    symbol="NIFTY 50",
    status="HITL_PENDING",     # ← this status
    hitl_required=True,
)
db.add(workflow_run)
await db.commit()
```

### Step 5 — Route returns early response to frontend
```json
{
    "thread_id": "a1b2c3d4",
    "hitl_status": "PENDING",
    "direction": "LONG",
    "risk_score": 0.78
}
```

### Step 6 — Risk manager approves via UI

**Frontend call:**
```typescript
await fetch("/api/v1/workflow/resume", {
  method: "POST",
  headers: { Authorization: `Bearer ${riskManagerToken}` },
  body: JSON.stringify({
    thread_id: "a1b2c3d4",
    decision: "APPROVE",
    notes: "Reviewed - RSI oversold confirmed, position size acceptable"
  })
});
```

### Step 7 — Resume route: `workflow_router.py` → `resume_workflow()`
```python
# File: backend/app/api/routes/workflow_router.py line 219

# Security check: require_risk_manager (role must be "risk_manager" or "admin")
# Verify workflow exists and is "HITL_PENDING"

config = {"configurable": {"thread_id": "a1b2c3d4"}}

# LangGraph reloads checkpoint from PostgreSQL, resumes after interrupt()
state = await graph.ainvoke(
    Command(resume={"decision": "APPROVE", "notes": "..."}),
    config=config,
)
```

### Step 8 — human_review_node resumes
```python
# interrupt() returns the resume dict
human_response = {"decision": "APPROVE", "notes": "Reviewed..."}

# _approve() sets:
proposal.human_approved = True
proposal.human_notes    = "Reviewed..."
return {"consensus": proposal, "hitl_status": "APPROVED"}
```

### Step 9 — Execution node runs (same as before, but now human_approved=True)
```python
# Layer 3 check passes: proposal.human_approved == True
# Trade is placed via broker
```

### Step 10 — Resume route updates DB and returns
```python
workflow_run.status = "COMPLETED"
workflow_run.hitl_reviewed_by = current_user.id
workflow_run.hitl_decided_at = datetime.now(timezone.utc)
await db.commit()
```

Response:
```json
{
    "thread_id": "a1b2c3d4",
    "decision": "APPROVE",
    "hitl_status": "APPROVED",
    "executed_trade": { ... },
    "reviewed_by": "risk@firm.com"
}
```

---

## Trace 4: Exit Monitor Closes a Trade

### Scenario: NIFTY 50 LONG trade, stop-loss hit

**Background task running every 10 seconds:**
```python
# File: backend/app/jobs/exit_monitor.py

async def _check_open_trades(self):
    # SQL: SELECT * FROM trades WHERE status='OPEN' AND stop_loss IS NOT NULL
    open_trades = await session.execute(select(Trade).where(Trade.status == "OPEN"))

    for trade in open_trades:
        await self._evaluate_trade(trade, broker)

async def _evaluate_trade(self, trade, broker):
    # Get current price from broker
    current_price = await broker.get_ltp(trade.symbol)  # e.g. 21400.0

    # Check SL: LONG trade, current_price <= stop_loss
    if trade.direction == "LONG" and current_price <= trade.stop_loss:
        # 21400.0 <= 21633.1  → STOP LOSS HIT
        await self._execute_exit(trade, current_price=21400.0, reason="STOP_LOSS")
```

**Data transformations in `_execute_exit()`:**
```python
# 1. Place counter-order (exit LONG = place SHORT)
exit_direction = "SHORT"  # opposite of LONG
limit_price = round(21400.0 - (21400.0 * 0.0005), 2)  = 21389.3  # slight discount

order_result = await broker.place_order(
    symbol="NIFTY 50",
    direction="SHORT",
    quantity=1.0,
    order_type="LIMIT",
    price=21389.3,
)

# 2. Close trade in DB
closed_trade = await TradeRepo.close_trade(
    session, trade_id=trade.id, user_id=trade.user_id, exit_price=21400.0
)
# Calculates: realized_pnl = (21400.0 - 22300.0) * 1 shares = -₹900
# pnl_pct = ((21400 - 22300) / 22300) * 100 = -4.04%

# 3. Update episodic memory in Qdrant
await update_trade_outcome(run_id=trade.run_id, outcome="LOSS", pnl_pct=-4.04)
# Qdrant point payload: outcome "NEUTRAL" → "LOSS", pnl_pct 0.0 → -4.04

# 4. Accumulate daily PnL and check cap
self._daily_pnl += -900.0   # now -₹900 for today

# 5. Publish to Redis
await redis_client.publish(CHANNEL_TRADE_EXECUTED, json.dumps({
    "event": "TRADE_CLOSED",
    "reason": "STOP_LOSS",
    "symbol": "NIFTY 50",
    "entry": 22300.0,
    "exit": 21400.0,
    "pnl": -900.0,
    "pnl_pct": -4.04,
}))

# 6. Trigger weight update (if 20 trades have closed for this user)
await maybe_update_weights(user_id=trade.user_id, symbol="NIFTY 50")
```

---

## Trace 5: Kill Switch Activation

### Risk manager activates kill switch

```typescript
// Frontend POST /api/v1/kill-switch/halt
await fetch("/api/v1/kill-switch/halt", {
  method: "POST",
  headers: { Authorization: `Bearer ${riskManagerToken}` },
  body: JSON.stringify({ reason: "Unexpected market volatility", duration_seconds: 3600 })
});
```

### `kill_switch_router.py` → `kill_switch_service.py`
```python
async def activate_kill_switch(duration_seconds, reason):
    await redis_client.set(KEY_TRADING_HALT, "1")  # "TRADING_HALT" = "1"

    # Publish for frontend notification
    await redis_client.publish(CHANNEL_KILL_SWITCH, json.dumps({
        "action": "HALT", "reason": reason, "duration_seconds": duration_seconds
    }))

    # Store event in DB
    async with AsyncSessionLocal() as session:
        event = KillSwitchEvent(action="HALT", reason=reason, ...)
        session.add(event)
        await session.commit()
```

### Effect on next workflow cycle
```python
# In execution_node:
halt = await redis_client.get("TRADING_HALT")  # returns "1"
if halt == "1":
    return {"execution_error": "KILL_SWITCH_ACTIVE", ...}
# Trade is blocked before any order is placed
```

---

## Trace 6: WebSocket — Frontend Receives Real-Time Updates

### Frontend connects WebSocket
```typescript
const ws = new WebSocket(`ws://localhost:8000/api/v1/market/stream?token=${accessToken}`);
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.type === "agent_results") updateAgentPanel(data);
  if (data.type === "trade_executed") refreshTradeTable(data);
  if (data.type === "hitl_pending") showApprovalModal(data);
};
```

### Backend WebSocket handler (`market_router.py`)
```python
# 1. Validates JWT from query param
# 2. Subscribes to Redis pub/sub channels:
#    CHANNEL_AGENT_RESULTS, CHANNEL_TRADE_EXECUTED, CHANNEL_HITL_PENDING, CHANNEL_KILL_SWITCH
# 3. Forwards any published message to the WebSocket connection

async for message in pubsub.listen():
    if message["type"] == "message":
        await websocket.send_text(message["data"])
```

### End-to-end flow for live agent update
```
orchestrator_node completes
    ↓ calls _publish_results()
redis_client.publish("futureedge:agent_results", json_payload)
    ↓
Redis delivers to all subscribers
    ↓
market_router WebSocket handler receives it
    ↓
websocket.send_text(json_payload)
    ↓
Browser receives message via ws.onmessage
    ↓
React state updates → component re-renders with new agent votes
```
Total latency: typically **< 50ms** from orchestrator decision to browser update.
