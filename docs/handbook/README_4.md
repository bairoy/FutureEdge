# README 4 — AI PIPELINE DEEP DIVE

## Overview of AI Components

FutureEdge uses four distinct AI mechanisms:

| Component | Type | File | When Active |
|---|---|---|---|
| Regime Agent | Classical statistics | `agents/regime_agent.py` | Every cycle |
| Signal Agent | Technical indicators | `agents/signal_agent.py` | Every cycle |
| Sentiment Agent | Keyword/FinBERT NLP | `agents/sentiment_agent.py` | Every cycle |
| LLM Reasoner | GPT-4o-mini / local LLM | `models/llm_reasoner.py` | When direction ≠ HOLD |
| Episodic Memory | Vector similarity (Qdrant) | `memory/qdrant_store.py` | Every cycle |
| Adaptive Weights | Accuracy-based recalibration | `jobs/weight_updater.py` | After every N trades |

---

## AI Pipeline 1: Market Regime Detection

### Trigger point
`backend/app/graph/builder.py` — `create_graph()` adds `regime_agent` as the first node after `START`.

### Data received
```python
state["market_context"].ohlcv_1m = [
    {"open": 22280.0, "high": 22350.0, "low": 22260.0, "close": 22300.0, "volume": 12345, "timestamp": "..."},
    # ... 287 more candles
]
```
Minimum 30 candles required. Falls back to `RANGEBOUND` if insufficient.

### Processing stages

**Stage 1 — ATR (Average True Range)**
```python
tr1 = df["high"] - df["low"]                           # high-low range
tr2 = (df["high"] - df["close"].shift(1)).abs()         # gap up
tr3 = (df["low"]  - df["close"].shift(1)).abs()         # gap down
tr  = concat([tr1, tr2, tr3]).max(axis=1)               # max of all three
atr = tr.rolling(window=14).mean()                      # 14-period average
```
ATR measures how much price moves on average. High ATR = volatile market.

**Stage 2 — Directional Movement**
```python
up_move   = df["high"].diff()           # did high increase?
down_move = df["low"].diff().abs()      # did low decrease?

# +DM: up move is dominant and positive
plus_dm  = where((up_move > down_move) & (up_move > 0),   up_move, 0.0)
minus_dm = where((down_move > up_move) & (down_move > 0), down_move, 0.0)

plus_di  = 100 * (rolling_mean(plus_dm)  / atr)   # normalised +DI
minus_di = 100 * (rolling_mean(minus_dm) / atr)   # normalised -DI
```

**Stage 3 — ADX (Average Directional Index)**
```python
dx  = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
adx = dx.rolling(window=14).mean()   # ADX > 25 = strong trend
```

**Stage 4 — EMA slope**
```python
ema20      = df["close"].ewm(span=20).mean()
ema_slope  = float(ema20.diff(3).iloc[-1])   # 3-candle change in EMA
# Positive slope = price trending up
# Negative slope = price trending down
```

**Stage 5 — Volatility**
```python
returns = log(close / close.shift(1))
vol     = returns.tail(30).std()   # std of log returns over 30 candles
```

**Classification logic:**
```python
if latest_adx > 25:
    slope_threshold = close_price * 0.0001   # 0.01% of price
    if   ema_slope > +slope_threshold: regime = "TRENDING_UP"
    elif ema_slope < -slope_threshold: regime = "TRENDING_DOWN"
    else:                               regime = "RANGEBOUND"
else:
    if vol > 0.03:   regime = "HIGH_VOLATILITY"   # >3% std on 1m returns
    else:            regime = "RANGEBOUND"
```

### Output
```python
ctx.regime         = "RANGEBOUND"   # or TRENDING_UP / TRENDING_DOWN / HIGH_VOLATILITY
ctx.volatility_24h = 0.018          # 1.8% rolling volatility
```

### What breaks if removed
- Signal agent applies same weights in all market conditions (directionally wrong 50% of the time)
- Risk agent's volatility check still works (reads `volatility_24h`)
- Sentiment agent is unaffected (doesn't use regime)

### How to modify
Add a new regime category:
```python
elif vol > 0.01 and latest_adx < 15:
    regime = "TIGHT_RANGE"
# Then handle it in signal_agent's scoring logic
```

---

## AI Pipeline 2: Technical Signal Generation

### Trigger point
`builder.py`: fan-out from `regime_agent` → `signal_agent` runs in parallel with 3 others.

### Data received
```python
state["market_context"].ohlcv_1m   # raw OHLCV candles
state["market_context"].regime     # set by regime_agent
state["market_context"].volatility_24h
```

### Retrieval flow — Indicator cache

**File:** `backend/app/data/indicator_cache.py`
```python
async def get_indicators(symbol: str, candles: list[dict]) -> dict:
    # 1. Check Redis cache
    cache_key = f"futureedge:indicators:{symbol}"
    cached = await redis_client.get(cache_key)
    if cached:
        data = json.loads(cached)
        if time.time() - data["computed_at"] < 60:   # cache for 60 seconds
            return {**data, "from_cache": True}

    # 2. Compute indicators from candles
    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]

    rsi    = compute_rsi(closes, period=14)
    macd, signal, hist = compute_macd(closes, fast=12, slow=26, signal=9)
    upper, middle, lower = compute_bollinger(closes, period=20, std_dev=2)

    result = {"rsi": rsi, "macd": macd, "macd_signal": signal, "macd_hist": hist,
              "bollinger_upper": upper, "bollinger_middle": middle, "bollinger_lower": lower,
              "computed_at": time.time(), "from_cache": False}

    # 3. Cache for 60 seconds
    await redis_client.setex(cache_key, 60, json.dumps(result))
    return result
```

### Scoring logic (regime-adaptive)

```python
# Raw scores for each indicator
rsi_score  = +0.3 if rsi < 30 else (-0.3 if rsi > 70 else 0.0)
macd_score = +0.25 if hist > 0 and macd > signal else (-0.25 if hist < 0 and macd < signal else 0.0)
bb_score   = +0.2 if price < bb_lower else (-0.2 if price > bb_upper else 0.0)

# Apply regime weights
if regime == "RANGEBOUND":
    # RSI and Bollinger are mean-reversion indicators — weight them more
    score = (rsi_score * 1.5) + (bb_score * 1.5) + (macd_score * 0.2)

elif regime in ("TRENDING_UP", "TRENDING_DOWN"):
    # MACD is the trend-following indicator — weight it heavily
    score = macd_score * 1.8
    # But still allow RSI to catch pullbacks (half weight)
    if regime == "TRENDING_UP"   and rsi_score > 0: score += rsi_score * 0.5
    if regime == "TRENDING_DOWN" and rsi_score < 0: score += rsi_score * 0.5

elif regime == "HIGH_VOLATILITY":
    # Everything halved — be conservative
    score = (rsi_score + macd_score + bb_score) * 0.5 * 0.5

# Volatility damping (cross-regime)
if volatility_24h > 0.05:
    score *= 0.7   # dampen further if vol is very high

# Convert score to vote
if   score >  0.2: decision = "BUY",  confidence = min(0.5 + score, 0.95)
elif score < -0.2: decision = "SELL", confidence = min(0.5 + abs(score), 0.95)
else:              decision = "HOLD", confidence = 0.5 + abs(score)
```

### Why the 0.2 threshold exists
Below 0.2, the signal is weak and noisy. Trading on weak signals produces too many marginal trades that eat into returns through transaction costs.

### How to reuse in another project
```python
# 1. Compute your own indicators (RSI, MACD, Bollinger)
# 2. Apply regime-adaptive weights to each indicator score
# 3. Sum scores → threshold → BUY / SELL / HOLD
# 4. Cache indicator results with a short TTL to avoid recomputation on rapid cycles
```

---

## AI Pipeline 3: Sentiment Analysis

### Trigger point
Parallel fan-out from `regime_agent`.

### Data received
```python
state["market_context"].recent_news = [
    {"title": "RBI holds rates steady", "source": "Economic Times", "timestamp": "..."},
    {"title": "FII inflows surge", "source": "Mint", ...},
    ...
]
```

### Two modes

**Mode 1 — Keyword matching (default, `FINBERT_ENABLED=False`)**
```python
POSITIVE_KEYWORDS = {"bullish", "surge", "rally", "strong", "buy", "outperform", "growth"}
NEGATIVE_KEYWORDS = {"bearish", "crash", "sell", "weak", "loss", "downgrade", "recession"}

for news_item in recent_news:
    text = news_item["title"].lower()
    pos_hits = sum(1 for kw in POSITIVE_KEYWORDS if kw in text)
    neg_hits = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text)
    sentiment_score = (pos_hits - neg_hits) / max(len(recent_news), 1)
# Returns: -1.0 to +1.0
```

**Mode 2 — FinBERT (if `FINBERT_ENABLED=True`)**
```python
# File: backend/app/models/finbert.py
from transformers import pipeline
sentiment_pipeline = pipeline("text-classification", model="ProsusAI/finbert")

for news_item in recent_news:
    result = sentiment_pipeline(news_item["title"])
    # Returns: {"label": "positive/negative/neutral", "score": 0.92}
    if label == "positive": score += result["score"]
    elif label == "negative": score -= result["score"]
```

FinBERT is a BERT model fine-tuned on financial text. More accurate than keywords but requires downloading ~440MB model on first run.

### Output
```python
state["sentiment_vote"] = AgentVote(
    agent="SentimentAgent",
    decision="BUY",        # BUY if score > 0.15, SELL if < -0.15, else HOLD
    confidence=0.65,
    reasoning="Positive news sentiment detected (FII inflows, RBI stability)",
    metadata={"sentiment_score": 0.35, "news_count": 5, "finbert_used": False}
)
```

---

## AI Pipeline 4: LLM Trade Rationale

### Trigger point
Inside `orchestrator_node()` after the mathematical consensus is computed. Only runs if:
1. `LLM_REASONING_ENABLED = True`
2. `OPENAI_API_KEY` or `LOCAL_MODEL_BASE_URL` is set
3. direction ≠ "HOLD" (no point explaining why we did nothing)

### Data received
```python
generate_trade_rationale(
    symbol="NIFTY 50",
    direction="LONG",
    votes=[...4 AgentVotes...],
    buy_score=0.68,
    sell_score=0.12,
    risk_score=0.42,
    disagreement=0.15,
    episodic_memories=[...5 past similar trades...],
    regime="RANGEBOUND",
)
```

### Prompt construction
```python
votes_summary = "\n".join([
    f"  {v.agent}: {v.decision} (confidence={v.confidence:.2f}) — {v.reasoning}"
    for v in votes
])

memory_summary = "\n".join([
    f"  - {m['symbol']} {m['direction']} in {m['regime']}: {m['outcome']} ({m['pnl_pct']:+.1f}%) | similarity={m['similarity']:.2f}"
    for m in episodic_memories[:3]   # top 3 most similar
])

prompt = f"""You are the reasoning module of an algorithmic trading system for Indian equities.

The system has just decided to go LONG on NIFTY 50.

Agent votes:
  SignalAgent: BUY (confidence=0.73) — RSI trigger (27.3) | Bollinger Band boundary
  SentimentAgent: BUY (confidence=0.65) — Positive news sentiment
  RiskAgent: HOLD (confidence=0.85) — All risk checks passed
  PortfolioAgent: HOLD (confidence=0.80) — Portfolio exposure acceptable

Consensus scores: buy=0.68, sell=0.12
Risk score: 0.42 (higher = more risky)
Agent disagreement: 0.15 (higher = less consensus)
Market regime: RANGEBOUND

Similar past situations:
  - NIFTY 50 LONG in RANGEBOUND: WIN (+1.2%) | similarity=0.91
  - NIFTY 50 LONG in RANGEBOUND: LOSS (-0.8%) | similarity=0.87
  - NIFTY 50 LONG in RANGEBOUND: WIN (+0.5%) | similarity=0.83

Write a 2-3 sentence explanation for the risk manager who will review this trade.
...
"""
```

### LLM call
```python
def _call_api():
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a professional financial AI reasoning engine..."},
            {"role": "user",   "content": prompt}
        ],
        max_tokens=300,
        temperature=0.3,    # low temperature = consistent, factual explanations
    )
    return response.choices[0].message.content.strip()

# Run in thread pool (OpenAI client is synchronous)
rationale = await loop.run_in_executor(None, _call_api)
```

### Output
```
"Signal agent detected RSI oversold conditions at 27.3 and price touching the lower Bollinger Band, 
suggesting a mean-reversion opportunity in this rangebound regime. Sentiment is mildly positive 
based on recent news (FII inflows). Historical data shows 2 of 3 similar past situations resulted 
in wins — consider the 0.8% loss case was during elevated volatility."
```

### Failure mode
```python
except Exception as e:
    # API key missing, network error, rate limit, etc.
    return _template_rationale(direction, votes, risk_score, disagreement)
    # Fallback: "System decided LONG based on: SignalAgent=BUY, ... Risk=moderate"
```

**Critical design principle:** The LLM never makes the decision. It only explains after the decision is made. If the LLM call fails, the trade proceeds normally with a template explanation. The trading system never depends on GPT for correctness.

---

## AI Pipeline 5: Episodic Memory (Qdrant)

### Trigger point
Inside `orchestrator_node()`, after computing scores but before the final proposal.

### Stage 1 — Build market vector (embedder.py)
```python
market_vector = build_market_vector(
    rsi=27.3,
    macd_hist=0.12,
    bollinger_upper=22650.0,
    bollinger_lower=22150.0,
    current_price=22300.0,
    volatility_24h=0.018,
    sentiment_score=0.35,
    regime="RANGEBOUND",
    buy_score=0.68,
    sell_score=0.12,
    risk_score=0.42,
)
# Returns 12 floats:
# [0.273, 0.012, 0.3, 0.09, 0.35, 0.0, 0.0, 0.0, 0.0, 0.68, 0.12, 0.42]
# [rsi/100, tanh(macd/10), bb_pos, vol/0.20, sentiment, trending_up, trending_down, sideways, high_vol, buy, sell, risk]
```

### Stage 2 — Retrieve similar memories (qdrant_store.py)
```python
results = client.search(
    collection_name="trade_memories",
    query_vector=market_vector,   # 12 floats
    query_filter=Filter(must=[FieldCondition(key="symbol", match=MatchValue(value="NIFTY 50"))]),
    limit=5,
    with_payload=True,
)
# Returns top-5 by cosine similarity
```

### Stage 3 — What the orchestrator sees
```python
episodic_memories = [
    {"symbol": "NIFTY 50", "direction": "LONG", "regime": "RANGEBOUND",
     "outcome": "WIN", "pnl_pct": 1.2, "similarity": 0.91, "agent_votes": {...}},
    {"symbol": "NIFTY 50", "direction": "LONG", "regime": "RANGEBOUND",
     "outcome": "LOSS", "pnl_pct": -0.8, "similarity": 0.87, ...},
    # ... 3 more
]
```

This is injected into the LLM prompt and returned in `state["episodic_memory"]` for the frontend to display.

### Stage 4 — Store after execution
```python
# execution_agent.py, after broker order placed
await store_trade_memory(
    run_id="a1b2c3d4",
    user_id="uuid-user",
    symbol="NIFTY 50",
    direction="LONG",
    vector=market_vector,   # same vector computed in orchestrator
    outcome="NEUTRAL",       # updated to WIN/LOSS when exit monitor closes trade
    pnl_pct=0.0,
    regime="RANGEBOUND",
    agent_votes={"SignalAgent": "BUY", "SentimentAgent": "BUY", ...},
    risk_score=0.42,
)
```

### Stage 5 — Outcome update (exit_monitor.py)
```python
# When exit monitor closes the trade with PnL=-900:
await update_trade_outcome(
    run_id="a1b2c3d4",
    outcome="LOSS",
    pnl_pct=-4.04,
)
# Qdrant client.set_payload() updates the point's payload
# This makes future retrievals show the real outcome
```

---

## AI Pipeline 6: Adaptive Agent Weights

### The feedback loop
```
Trade closes (exit_monitor)
    ↓ calls maybe_update_weights()
Check: is total closed trades % WEIGHT_UPDATE_INTERVAL == 0?
    ↓ yes (every 20 trades)
_calculate_new_weights(last 100 closed trades)
    ↓ accuracy per agent = correct_votes / active_votes
Redis.set("futureedge:agent_weights", json.dumps(new_weights))
    ↓
Next orchestrator cycle reads updated weights
    ↓ get_agent_weights() → returns new weights from Redis
Weighted consensus uses accurate agents more
```

### Accuracy calculation
```python
for trade in closed_trades:
    trade_won  = trade.realized_pnl > 0
    trade_long = trade.direction == "LONG"

    for vote in trade.agent_consensus:
        if vote["decision"] not in ("BUY", "SELL"):
            continue   # HOLD votes don't count

        # Was the vote aligned with the profitable outcome?
        correct = (
            vote["decision"] == "BUY"  and trade_long  and trade_won or    # BUY → LONG → WIN
            vote["decision"] == "SELL" and not trade_long and trade_won or  # SELL → SHORT → WIN
            vote["decision"] == "BUY"  and not trade_long and not trade_won or  # contrarian BUY on SHORT loss
            vote["decision"] == "SELL" and trade_long  and not trade_won    # contrarian SELL on LONG loss
        )
```

### Why this matters
An agent that's right 70% of the time should have more influence than one that's right 50% of the time. Fixed weights ignore performance history entirely. This adaptive system means the consensus gets better as the system trades more.

### How to reuse
```python
# Pattern: after each outcome, store agent votes + outcome in DB
# Periodically: query last N outcomes, compute accuracy per agent
# Normalise accuracies to sum=1, apply min/max bounds
# Store in Redis/cache, read in scoring code
```
