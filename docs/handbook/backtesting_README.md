# BACKTESTING — FROM FIRST PRINCIPLES TO PRODUCTION AI TRADING

> *"The best predictor of future performance is a well-designed backtest."*

This guide teaches you **everything** about backtesting — from the fundamental concept to every line of code in this project — so you can replicate it in any future project and build a career as a professional AI trading engineer.

---

## TABLE OF CONTENTS

1. [What is Backtesting? (First Principles)](#1-what-is-backtesting-first-principles)
2. [Why Every Trading System Needs a Backtester](#2-why-every-trading-system-needs-a-backtester)
3. [The Architecture of Our Backtester](#3-the-architecture-of-our-backtester)
4. [Data Layer — Fetching Historical Candles](#4-data-layer--fetching-historical-candles)
5. [Indicators — The Brain of the Strategy](#5-indicators--the-brain-of-the-strategy)
6. [Regime Detection — Reading Market Conditions](#6-regime-detection--reading-market-conditions)
7. [The Simulation Loop — Event-Driven Backtesting](#7-the-simulation-loop--event-driven-backtesting)
8. [Transaction Costs — Making It Real](#8-transaction-costs--making-it-real)
9. [Position Management — Entries, Exits, and Reversals](#9-position-management--entries-exits-and-reversals)
10. [Performance Metrics — Judging Strategy Quality](#10-performance-metrics--judging-strategy-quality)
11. [The API Layer — Exposing the Backtester](#11-the-api-layer--exposing-the-backtester)
12. [The Complete Data Flow — From Request to Result](#12-the-complete-data-flow--from-request-to-result)
13. [Critical Biases to Avoid](#13-critical-biases-to-avoid)
14. [What to Learn Next — Professional AI Trading Roadmap](#14-what-to-learn-next--professional-ai-trading-roadmap)

---

## 1. What is Backtesting? (First Principles)

### The Core Idea

Imagine you invent a new trading rule: *"Buy whenever RSI drops below 30, sell when it rises above 70."*

Before risking real money, you want to know: **Would this rule have made money in the past?**

Backtesting is the act of **replaying your strategy on historical price data** to measure its profitability, risk, and consistency — as if you had been trading it in the past, using only the information that was available at each moment.

```
Historical Prices (Past OHLCV Data)
           │
           ▼
 ┌─────────────────────┐
 │   Strategy Logic    │  ← Your rules: when to buy, sell, how much
 │   (same code as     │
 │    live trading)    │
 └─────────┬───────────┘
           │
           ▼
    Simulated Trades
           │
           ▼
 ┌─────────────────────┐
 │  Performance Report │  ← Win rate, Sharpe, drawdown, PnL
 └─────────────────────┘
```

### The Fundamental Contract of Backtesting

> **At every moment in time T, you can ONLY use information that was available BEFORE T.**

This is the cardinal rule. Violating it = **lookahead bias** = fake profits that disappear in live trading.

### What a Candle Represents (OHLCV)

Before we go further, understand what data we work with:

```
A single 1-hour candle for RELIANCE on June 1st at 10:00 AM:

 Open  = ₹2,870.00   ← price at 10:00 AM (start of candle)
 High  = ₹2,895.50   ← highest price during 10:00–11:00 AM
 Low   = ₹2,855.00   ← lowest price during 10:00–11:00 AM
 Close = ₹2,880.20   ← price at 11:00 AM (end of candle)
 Vol   = 1,234,567   ← shares traded during this hour

         ┃  High ₹2,895
   ┌─────┸─────┐
   │           │  ← Candle body (Open to Close)
   └─────┰─────┘
         ┃  Low ₹2,855
```

---

## 2. Why Every Trading System Needs a Backtester

### The Problem Without Backtesting

If you deploy a strategy live without testing:
- You discover bugs **with real money on the line**
- You have **no baseline** for "is the strategy working?"
- You cannot **optimize parameters** (SL/TP %, position size)
- You cannot measure **regime sensitivity** (does it work in trending markets but not sideways?)

### What Backtesting Gives You

| Question                          | Metric                     |
|-----------------------------------|----------------------------|
| Did it make money?                | Total PnL, Return %        |
| How consistently profitable?      | Win Rate, Profit Factor    |
| How much risk did it take?        | Max Drawdown               |
| Is return worth the risk?         | Sharpe Ratio, Calmar Ratio |
| Does it work in all conditions?   | Regime Breakdown           |
| How long does a losing streak last?| Consecutive losses        |

### In Our System: Why the Backtester Mirrors the Live Agent

The most critical design decision we made:

> **The backtester uses EXACTLY THE SAME indicator math as the live SignalAgent.**

This means:
- The backtest is a **faithful simulation** of what would happen live
- If you change a parameter in the live agent, you change it in the backtest too
- Results are **reproducible** and **trustworthy**

---

## 3. The Architecture of Our Backtester

```
┌─────────────────────────────────────────────────────────────────────┐
│                     BACKTEST SYSTEM                                  │
│                                                                      │
│  HTTP Request (POST /api/v1/backtest)                                │
│       │                                                              │
│       ▼                                                              │
│  ┌──────────────────────┐                                            │
│  │  backtest_router.py  │  ← FastAPI route, validates input          │
│  └──────────┬───────────┘                                            │
│             │                                                        │
│             ▼                                                        │
│  ┌──────────────────────┐                                            │
│  │  backtester.py       │  ← Core engine (629 lines)                 │
│  │  run_backtest()      │                                            │
│  └──────────┬───────────┘                                            │
│             │                                                        │
│    ┌────────┼────────────────┐                                       │
│    │        │                │                                       │
│    ▼        ▼                ▼                                       │
│  feed.py  precompute_    simulate                                    │
│  (OHLCV   indicators()  loop over                                    │
│   data)   (RSI, MACD,   candles                                      │
│           BB, ADX,       t=30..N                                     │
│           Regime)        │                                           │
│                          ▼                                           │
│                    calculate metrics                                  │
│                    (Sharpe, MDD,                                     │
│                     PF, Win Rate)                                    │
│                          │                                           │
│                          ▼                                           │
│                    Return JSON result                                 │
└─────────────────────────────────────────────────────────────────────┘
```

**Files involved:**
- [`backtester.py`](file:///Users/baijuyadav/Desktop/futureedge/backend/app/graph/backtester.py) — Core engine (629 lines)
- [`backtest_router.py`](file:///Users/baijuyadav/Desktop/futureedge/backend/app/api/routes/backtest_router.py) — API endpoint
- [`feed.py`](file:///Users/baijuyadav/Desktop/futureedge/backend/app/data/feed.py) — Historical data fetching
- [`indicator_cache.py`](file:///Users/baijuyadav/Desktop/futureedge/backend/app/data/indicator_cache.py) — Live indicator computation (reference)
- [`regime_detector.py`](file:///Users/baijuyadav/Desktop/futureedge/backend/app/data/regime_detector.py) — Regime classification

---

## 4. Data Layer — Fetching Historical Candles

### File: `app/data/feed.py` → `load_historical_candles()`

The first step of any backtest: **get the historical price data**.

```python
# From: app/graph/backtester.py, line 174
candles = load_historical_candles(symbol, period=period, interval=interval)
```

### How `load_historical_candles` Works

```
load_historical_candles("RELIANCE", period="1mo", interval="1h")
          │
          ▼
    Is ACTIVE_BROKER = "zerodha"?
          │
     ─────┴──────
     │           │
    YES          NO
     │           │
     ▼           ▼
  Try Zerodha   yfinance
  historical   (free, 5-15
  API (paid    min delay)
  ₹2000/mo)
     │
     │ (if empty/fails)
     ▼
  yfinance fallback
```

### Why Two Sources?

| Source   | Cost        | Latency    | Reliability | Use Case           |
|----------|-------------|------------|-------------|--------------------|
| Zerodha  | ₹2000/month | Real-time  | High        | Production backtest|
| yfinance | Free        | 5-15 min   | Medium      | Development/testing|

### The `_to_yfinance_symbol()` Function

NSE symbols need to be converted for Yahoo Finance:

```python
# From: feed.py, line 347-371
def _to_yfinance_symbol(symbol: str) -> str:
    index_map = {
        "NIFTY 50":   "^NSEI",     # NSE index → Yahoo Finance ticker
        "NIFTY BANK": "^NSEBANK",
        "SENSEX":     "^BSESN",
    }
    # Indian equities get .NS suffix
    equity_symbol = clean_symbol.replace(" ", "").upper()
    return f"{equity_symbol}.NS"    # RELIANCE → RELIANCE.NS
```

**Why `.NS`?** Yahoo Finance uses stock exchange suffixes. `.NS` = NSE (National Stock Exchange), `.BO` = BSE.

### What a Candle Dict Looks Like

```python
{
    "open":      2870.00,   # Opening price
    "high":      2895.50,   # Session high
    "low":       2855.00,   # Session low
    "close":     2880.20,   # Closing price
    "volume":    1234567,   # Shares traded
    "timestamp": "2024-06-01T10:00:00+05:30"  # ISO 8601 with IST timezone
}
```

### Minimum Candle Requirement

```python
# From: backtester.py, line 175
if not candles or len(candles) < 35:
    logger.warning(f"Insufficient historical candles for backtesting {symbol}")
    return { ... empty metrics ... }
```

**Why 35?** Most indicators need warmup period:
- RSI needs 14 candles
- MACD needs 26 candles
- Bollinger Bands need 20 candles
- We start simulation at `t=30` (line 211), so we need at least 35 total

---

## 5. Indicators — The Brain of the Strategy

### File: `app/graph/backtester.py` → `precompute_indicators_and_regimes()`

**Critical Design Decision:** We precompute all indicators for ALL candles upfront, before the simulation loop starts.

```
Why precompute everything first?

Option A (WRONG - naive approach):
  for each candle:
      compute RSI on candles[0..t]   ← SLOW: O(N²) complexity
      compute MACD on candles[0..t]  ← also recomputing same data

Option B (OUR APPROACH - vectorized):
  df = precompute_indicators_and_regimes(df)  ← compute ONCE
  for each candle:
      read df.iloc[t-1]              ← O(1) lookup: FAST
```

This is why the function is called `precompute_` — it runs once and attaches all indicator columns to the DataFrame.

### RSI — Relative Strength Index

**What it measures:** Momentum — is the market overbought or oversold?

```python
# From: backtester.py, lines 47-59
delta = df["close"].diff()        # price change each bar
gain  = delta.clip(lower=0)       # keep only positive changes
loss  = -delta.clip(upper=0)      # keep only negative changes (flip sign)

period = 14
# Welles Wilder's Smoothing (same as TradingView)
avg_gain = gain.ewm(alpha=1.0/period, adjust=False).mean()
avg_loss = loss.ewm(alpha=1.0/period, adjust=False).mean().replace(0, 1e-10)

rs = avg_gain / avg_loss          # Relative Strength
df["rsi"] = 100 - (100 / (1 + rs))
```

**Visual interpretation:**
```
RSI = 100 ───────────────── Extreme Overbought (everyone is buying)
RSI = 70  ─ ─ ─ ─ ─ ─ ─ ─ Overbought threshold (we SELL signal)
RSI = 50  ──────────────────Neutral
RSI = 30  ─ ─ ─ ─ ─ ─ ─ ─ Oversold threshold (we BUY signal)
RSI = 0   ───────────────── Extreme Oversold (everyone is selling)
```

**Why `ewm(alpha=1/period, adjust=False)`?**
- Welles Wilder (RSI inventor) used a specific smoothing formula
- `alpha = 1/period` = same as Wilder's Smoothing (RMA)
- `adjust=False` = each new value builds on the previous (recursive, not window-based)
- This matches **TradingView's RSI exactly** ← critical for strategy validation

**Why `replace(0, 1e-10)`?**
- If there are ZERO losses in a period (pure up-move), avg_loss = 0
- Division by zero would give `NaN` or infinity
- We replace with a tiny number so RSI correctly returns ~100 (fully overbought)

### MACD — Moving Average Convergence Divergence

**What it measures:** Trend direction and momentum shifts

```python
# From: backtester.py, lines 61-66
ema_fast       = df["close"].ewm(span=12, adjust=False).mean()  # 12-period EMA
ema_slow       = df["close"].ewm(span=26, adjust=False).mean()  # 26-period EMA
df["macd"]     = ema_fast - ema_slow      # MACD Line
df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean() # Signal Line
df["macd_hist"]   = df["macd"] - df["macd_signal"]             # Histogram
```

**Visual interpretation:**
```
     MACD Line ────────────────────────────────
     Signal Line ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

     Histogram (MACD - Signal):

     Bullish:  │███│
               ────────── zero line ──────────
     Bearish:           │███│

When histogram crosses above zero → BUY signal
When histogram crosses below zero → SELL signal
```

**Why span=12, 26, 9?**
These are the original Gerald Appel parameters from 1979. They represent:
- `12` = ~2.5 trading weeks of price data
- `26` = ~1.3 months of price data
- `9` = signal smoothing (reduces noise)

### Bollinger Bands

**What they measure:** Volatility and price extremes

```python
# From: backtester.py, lines 68-78
sma   = df["close"].rolling(window=20).mean()      # 20-period Simple MA
stdev = df["close"].rolling(window=20).std()       # Standard deviation

df["bollinger_upper"]  = sma + (stdev * 2)  # Upper band: SMA + 2σ
df["bollinger_lower"]  = sma - (stdev * 2)  # Lower band: SMA - 2σ
df["bollinger_middle"] = sma                # Middle: just the SMA
```

**Visual interpretation:**
```
bollinger_upper ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ (sell zone: price near upper)
                  ┌──────────────────
price ────────────┘        ┐
                           └─────────
bollinger_lower ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ (buy zone: price near lower)

Bands narrow = low volatility (breakout coming)
Bands widen  = high volatility
```

**Why 2 standard deviations?**
- Statistically, ~95% of price data falls within ±2σ of the mean
- When price breaks outside the bands, it is statistically "extreme"
- John Bollinger (inventor) found 2σ gives the best signal-to-noise ratio

### EMA 9/21 Crossover

**What it measures:** Medium-term trend direction changes

```python
# From: backtester.py, lines 140-146
ema9  = df["close"].ewm(span=9,  adjust=False).mean()
ema21 = df["close"].ewm(span=21, adjust=False).mean()
df["ema_9"]       = ema9
df["ema_21"]      = ema21
df["ema_9_prev"]  = ema9.shift(1)    # previous bar's EMA9
df["ema_21_prev"] = ema21.shift(1)   # previous bar's EMA21
```

**Why do we store `_prev`?**
To detect a **fresh crossover** — the moment EMA9 crosses EMA21 is a stronger signal than when EMA9 is already above EMA21:

```python
# From: backtester.py, lines 258-262
# Is EMA9 above EMA21 right now?
if ema9_curr > ema21_curr:
    crossover_score = 0.2
    # Is this a FRESH cross? (previous bar had EMA9 below EMA21)
    if ema9_prev <= ema21_prev:
        crossover_score += 0.1   # +0.1 bonus for fresh crossover signal
```

### ATR — Average True Range

**What it measures:** Volatility in price terms (₹ per candle)

```python
# From: backtester.py, lines 81-88 and indicator_cache.py, lines 200-230
tr1 = df["high"] - df["low"]                       # High-Low range
tr2 = (df["high"] - df["close"].shift(1)).abs()    # Gap up
tr3 = (df["low"]  - df["close"].shift(1)).abs()    # Gap down
tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)  # True Range = max of 3

# Welles Wilder's smoothing
atr = tr.ewm(alpha=1.0/period, adjust=False).mean()
```

**Why True Range is max of 3?**
```
Case: Stock closes at ₹100, gaps up, opens at ₹110, reaches ₹115, low ₹108.
  tr1 = 115 - 108 = ₹7    (intraday range)
  tr2 = 115 - 100 = ₹15   (gap up + high)
  tr3 = 108 - 100 = ₹8    (gap up + low)
  True Range = max(7, 15, 8) = ₹15  ← captures the full price gap
```

**How ATR is used for Stop-Loss and Take-Profit:**

```python
# From: backtester.py, lines 291-297
atr_val = float(row_prev["atr"])
if atr_val > 0:
    dynamic_sl_pct = (1.5 * atr_val / open_price) * 100.0  # 1.5× ATR as %
    dynamic_tp_pct = (3.0 * atr_val / open_price) * 100.0  # 3.0× ATR as %
else:
    dynamic_sl_pct = stop_loss_pct     # fallback to fixed %
    dynamic_tp_pct = take_profit_pct   # fallback to fixed %
```

**Why ATR-based SL/TP is superior to fixed %?**
- Fixed 1.5% SL on RELIANCE (₹2880) = ₹43 below entry
- In a high-volatility session, ATR might be ₹60 → your SL gets hit by normal noise!
- ATR-based SL adapts: if ATR = ₹30, SL = 1.5 × ₹30 = ₹45 → tighter, appropriate
- The ratio `1.5× ATR for SL, 3.0× ATR for TP` gives you a **2:1 reward/risk ratio**

---

## 6. Regime Detection — Reading Market Conditions

### File: `app/data/regime_detector.py` (concept), implemented inline in `backtester.py`

**The fundamental insight:** Different indicators work in different market conditions.

```
┌─────────────────────────────────────────────────────────────────┐
│  REGIME         │  BEST INDICATOR    │  WORST INDICATOR         │
├─────────────────┼────────────────────┼──────────────────────────┤
│  TRENDING_UP    │  MACD, EMA cross   │  RSI (trend persists)    │
│  TRENDING_DOWN  │  MACD, EMA cross   │  RSI (stays oversold)    │
│  RANGEBOUND     │  RSI, BB           │  MACD (whipsaws)         │
│  HIGH_VOLATILITY│  None reliable     │  All (reduce size!)      │
└─────────────────────────────────────────────────────────────────┘
```

### How Regime is Detected (from backtester.py, lines 118-152)

```python
for idx in range(len(df)):
    latest_adx = df["adx"].iloc[idx]     # trend strength (0-100)
    slope      = df["ema_slope"].iloc[idx] # EMA direction (+ or -)
    price      = df["close"].iloc[idx]
    v          = df["vol"].iloc[idx]       # annualized volatility

    if latest_adx > 25:                   # ADX > 25 = trending market
        slope_threshold = price * 0.0001
        if slope > slope_threshold:
            regimes.append("TRENDING_UP")
        elif slope < -slope_threshold:
            regimes.append("TRENDING_DOWN")
        else:
            regimes.append("RANGEBOUND")
    else:
        if v > 0.03:                      # vol > 3% annualized
            regimes.append("HIGH_VOLATILITY")
        else:
            regimes.append("RANGEBOUND")
```

### ADX — Average Directional Index

```python
# From: backtester.py, lines 90-107
up_move   = df["high"].diff()
down_move = df["low"].diff().abs()

# +DM: upward movement (only when up > down AND positive)
plus_dm   = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
# -DM: downward movement (only when down > up AND positive)
minus_dm  = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

plus_di  = 100 * (plus_dm_smoothed / atr)   # upward directional force
minus_di = 100 * (minus_dm_smoothed / atr)  # downward directional force

dx  = 100 * |plus_di - minus_di| / (plus_di + minus_di)  # directional movement
adx = smooth(dx)   # ADX = smoothed DX
```

**ADX interpretation:**
```
ADX < 20:  No trend     (RANGEBOUND)
ADX 20-25: Weak trend   (transitioning)
ADX > 25:  Strong trend (TRENDING_UP or TRENDING_DOWN)
ADX > 50:  Very strong  (rare, extreme momentum)
```

### EMA Slope

```python
# From: backtester.py, line 111
ema20 = df["close"].ewm(span=20, adjust=False).mean()
df["ema_slope"] = ema20.diff(3)  # change in EMA over last 3 bars
```

`ema_slope > 0` = price trending upward, `< 0` = downward. This tells us the **direction** once ADX confirms a trend exists.

---

## 7. The Simulation Loop — Event-Driven Backtesting

### File: `app/graph/backtester.py` lines 210–500

This is the heart of the backtester. It processes each candle one at a time, exactly as a live trading system would.

### State Variables

```python
cash          = initial_capital   # Available cash (starts at ₹1,00,000)
position      = 0                 # Shares held (positive=LONG, negative=SHORT)
entry_price   = 0.0               # Price at which we entered
entry_time    = None              # Timestamp of entry
entry_regime  = "RANGEBOUND"      # Market regime when we entered
direction     = "NONE"            # "LONG" | "SHORT" | "NONE"
trades        = []                # List of completed trades
equity_curve  = []                # Portfolio value over time
```

### The Loop Structure

```
for t in range(30, len(candles)):       ← start at 30 (warmup period)
    current_candle = candles[t]
    row_prev       = df.iloc[t - 1]     ← USE PREVIOUS BAR'S INDICATORS

    1. Read indicators from t-1          ← NO LOOKAHEAD BIAS
    2. Compute signal score              ← same math as live SignalAgent
    3. Check exit conditions (SL / TP)   ← does this candle hit our stop?
    4. Execute new entries if flat       ← enter on current bar's OPEN
    5. Handle reversals if position open ← close + reverse direction
    6. Track equity curve                ← record portfolio value
```

### Why `t-1` for Indicators?

```
TIME LINE:
                    t-1 (PREVIOUS candle)      t (CURRENT candle)
                    ┌──────────────────┐        ┌──────────────────┐
                    │  RSI = 28        │        │  ??? (unknown)   │
                    │  MACD hist > 0   │        │  (unfolding now) │
                    └──────────────────┘        └──────────────────┘
                           │                           │
                    WE READ THIS                  WE TRADE AT
                    at night, after                open of THIS
                    session closes                 candle

# In code (backtester.py, line 218):
row_prev = df.iloc[t - 1]   ← yesterday's closed indicator values
open_price = current_candle["open"]  ← today's first price
```

This replicates **real-world trading**: at night, you analyze the previous day's indicators, and execute at the next day's open.

### The Scoring System

**Signal score is the weighted sum of all indicator signals:**

```python
# Regime-Adaptive Weighting (backtester.py, lines 264-277)

if regime == "RANGEBOUND":
    score = (rsi_score * 1.5) + (bb_score * 1.5) + (macd_score * 0.2)
    # RSI and BB are primary → mean reversion strategy

elif regime in ("TRENDING_UP", "TRENDING_DOWN"):
    score = (macd_score * 1.2) + (crossover_score * 1.5)
    # MACD and EMA crossover are primary → trend-following strategy
    if regime == "TRENDING_UP" and rsi_score > 0:
        score += rsi_score * 0.5    # RSI oversold confirms trend entry

elif regime == "HIGH_VOLATILITY":
    score = ((rsi_score * 0.4) + (macd_score * 0.4) + (bb_score * 0.4)) * 0.5
    # Everything halved → be very cautious in high-vol conditions
```

**Decision thresholds:**

```python
decision = "HOLD"
if score > 0.2:     decision = "BUY"    # significant bullish consensus
elif score < -0.2:  decision = "SELL"   # significant bearish consensus
```

**Volatility filter:**

```python
if vol > 0.05:          # annualized vol > 5%
    score *= 0.7        # reduce confidence by 30% in high-vol markets
```

---

## 8. Transaction Costs — Making It Real

### File: `app/graph/backtester.py` → `calculate_transaction_cost()`

This is one of the most important functions for realistic backtesting. **Without transaction costs, a strategy looks profitable but loses money live.**

```python
def calculate_transaction_cost(value: float, is_buy: bool) -> float:
    """
    Models exact Zerodha/NSE MIS equity taxes.
    value   = trade value in rupees (shares × price)
    is_buy  = True for buy orders, False for sell orders
    """
    brokerage    = min(20.0, value * 0.0003)   # ₹20 cap OR 0.03%, whichever is lower
    exchange_txn = value * 0.0000345            # NSE transaction charge: 0.00345%
    sebi_fee     = value * 0.0000001            # SEBI: ₹10 per crore = 0.0001%
    gst          = (brokerage + exchange_txn + sebi_fee) * 0.18  # 18% GST
    stamp_duty   = value * 0.00003 if is_buy else 0.0   # 0.003% on BUY only
    stt          = value * 0.00025 if not is_buy else 0.0  # 0.025% on SELL only

    return brokerage + exchange_txn + sebi_fee + gst + stamp_duty + stt
```

### Real Example

On a ₹10,000 buy order for RELIANCE:

| Component         | Calculation           | Amount  |
|-------------------|-----------------------|---------|
| Brokerage         | min(₹20, ₹10K×0.03%)  | ₹3.00   |
| Exchange Txn      | ₹10K × 0.00345%       | ₹0.345  |
| SEBI Fee          | ₹10K × 0.0001%        | ₹0.01   |
| GST (18%)         | (₹3+₹0.345+₹0.01)×18% | ₹0.613  |
| Stamp Duty (buy)  | ₹10K × 0.003%         | ₹0.30   |
| **Total Cost**    |                       | **₹4.27** |

**Total round-trip cost (buy + sell) ≈ ₹10-12 on a ₹10,000 trade = 0.1%.**

For a high-frequency strategy making 100 trades/month, that's 10% drag on performance annually!

### Slippage

Slippage is the difference between the expected price and the actual fill price. In liquid markets like NSE:

```python
# From: backtester.py, lines 371-372, 388-389
# BUY: you always pay slightly MORE than the current price
entry_price_with_slippage = open_price * (1.0 + slippage_pct / 100.0)

# SELL: you always receive slightly LESS than the current price
exit_price_with_slippage = exit_price * (1.0 - slippage_pct / 100.0)
```

We use `slippage_pct=0.05%` (5 basis points) which is realistic for NSE liquid stocks.

---

## 9. Position Management — Entries, Exits, and Reversals

### Entry Logic

```python
# From: backtester.py, lines 367-400
if position == 0 and not exited_this_candle:
    if decision == "BUY":
        # Position sizing: use only `size_pct` % of available cash
        trade_size_val = cash * (size_pct / 100.0)   # e.g., 10% of ₹1,00,000 = ₹10,000

        # Calculate shares (integer — can't buy fractions of a stock)
        entry_price_with_slippage = open_price * (1.0 + slippage_pct / 100.0)
        shares = int(trade_size_val / entry_price_with_slippage)

        if shares > 0:
            gross_value  = shares * entry_price_with_slippage
            entry_cost   = calculate_transaction_cost(gross_value, is_buy=True)
            cash        -= (gross_value + entry_cost)    # deduct from cash
            position     = shares                        # hold these many shares
```

**Why `int()` for shares?** Stock markets don't allow fractional shares. If ₹10,000 / ₹2,880 per share = 3.47 shares → we can only buy 3 shares.

**Why only `size_pct = 10%`?** Position sizing principle: never bet your entire capital on one trade. 10% means you can survive 10 consecutive losing trades before being wiped out.

### Stop-Loss and Take-Profit Logic

```python
# From: backtester.py, lines 304-329
if position > 0:  # LONG POSITION
    sl_price = entry_price * (1.0 - dynamic_sl_pct / 100.0)  # below entry
    tp_price = entry_price * (1.0 + dynamic_tp_pct / 100.0)  # above entry

    # Did price touch stop-loss this candle?
    if current_candle["low"] <= sl_price:
        exit_triggered = True
        exit_price     = sl_price
        exit_reason    = "STOP_LOSS"

    # Did price reach take-profit this candle?
    elif current_candle["high"] >= tp_price:
        exit_triggered = True
        exit_price     = tp_price
        exit_reason    = "TAKE_PROFIT"
```

**Critical detail:** We check SL FIRST. This is the **conservative, realistic** approach. In real markets, if both SL and TP were hit in the same candle (extreme volatility), assume the worst: you got stopped out first.

**Using `low` for SL check:** We can't know the exact sequence of ticks within a candle. Using `low` for SL check means "if the candle's low touched our stop, we assume we got stopped out." This is a standard backtest assumption.

### Signal Reversal

```python
# From: backtester.py, lines 402-443
# If we're LONG and signal says SELL → close LONG, immediately open SHORT
elif direction == "LONG" and decision == "SELL":
    # Close the long
    exit_price_with_slippage = open_price * (1.0 - slippage_pct / 100.0)
    gross_value = position * exit_price_with_slippage
    cash += gross_value - exit_cost

    # Record the completed trade
    trades.append({ ... "exit_reason": "SIGNAL_REVERSAL" })

    # Open the short
    position = -shares
    direction = "SHORT"
```

Reversals are powerful in trending markets but can be costly in choppy markets (frequent whipsaws).

### Equity Curve Tracking

```python
# From: backtester.py, lines 487-500
current_candle_close = current_candle["close"]
position_value = 0.0

if position > 0:    # LONG
    position_value = position * current_candle_close

elif position < 0:  # SHORT
    # Short position value = initial margin + unrealized P&L
    position_value = abs(position) * entry_price + (entry_price - current_candle_close) * abs(position)

current_equity = cash + position_value
equity_curve.append({ "time": timestamp, "equity": round(current_equity, 2) })
```

**Why track short differently?**
- When long: equity = cash + (shares × current_price)
- When short: you sold borrowed shares. Your "value" = margin held + gain from price falling
- If entry was ₹100 and price drops to ₹90: gain = (100-90) × shares → you profited!

---

## 10. Performance Metrics — Judging Strategy Quality

### File: `app/graph/backtester.py` lines 532–628

### Win Rate

```python
winning_trades = [t for t in trades if t["pnl"] > 0]
win_rate = len(winning_trades) / total_trades * 100.0
```

**What it tells you:** % of trades that were profitable.
**Caution:** A 40% win rate can be excellent if avg winner >> avg loser (see Profit Factor).

### Profit Factor

```python
gross_profits = sum([t["pnl"] for t in winning_trades])
gross_losses  = sum([abs(t["pnl"]) for t in losing_trades])
profit_factor = gross_profits / gross_losses
```

**What it tells you:** For every ₹1 lost, how many ₹ were won?
```
Profit Factor < 1.0 → Losing strategy
Profit Factor = 1.0 → Break-even
Profit Factor 1.2–1.5 → Good
Profit Factor > 2.0 → Excellent (rare, treat with skepticism)
```

### Win/Loss Ratio

```python
avg_win_pct  = mean(winning_trade pnl %)
avg_loss_pct = mean(losing_trade pnl %)
win_loss_ratio = avg_win_pct / avg_loss_pct   # also called "payoff ratio"
```

**The Kelly Criterion connection:**
```
Optimal position size = (Win_Rate × Win_Loss_Ratio - Loss_Rate) / Win_Loss_Ratio

Example: Win Rate=50%, Win/Loss=2:1
  Kelly = (0.5 × 2 - 0.5) / 2 = 0.25 → bet 25% of capital (we use 10% for safety)
```

### Maximum Drawdown

```python
# From: backtester.py, lines 551-559
max_dd = 0.0
peak   = initial_capital

for pt in equity_curve:
    equity = pt["equity"]
    if equity > peak:
        peak = equity         # new all-time high
    dd = (peak - equity) / peak * 100.0   # drawdown from peak
    if dd > max_dd:
        max_dd = dd
```

**Visual:**
```
Portfolio value:
₹1,30,000 ─────────────── Peak (new high)
₹1,20,000             /
₹1,10,000        /───/
₹1,00,000 ──────/
₹  90,000                   ← Trough (worst point)
                 │         │
               Peak     Trough
               Max Drawdown = (130K - 90K) / 130K = 30.7%
```

**Why Max Drawdown matters more than return:**
A strategy that gains 30% but drops 50% at some point is psychologically impossible to follow — most traders quit during the drawdown and miss the recovery.

### Sharpe Ratio

```python
# From: backtester.py, lines 562-583
# Group equity curve by day
daily_equities = { date: equity for each daily snapshot }

# Calculate daily returns
daily_returns = [(curr - prev) / prev for consecutive days]

# Annualized Sharpe = (mean_return / std_return) × √252
mean_ret = sum(daily_returns) / len(daily_returns)
std_ret  = math.sqrt(variance)
sharpe   = (mean_ret / std_ret) * math.sqrt(252)   # 252 = trading days/year
```

**What Sharpe means:**
```
Sharpe < 0:    Worse than cash (losing money)
Sharpe 0–0.5:  Mediocre (not worth the risk)
Sharpe 0.5–1:  Acceptable
Sharpe 1–2:    Good
Sharpe > 2:    Excellent (top hedge funds)
```

**Why √252?** To annualize daily Sharpe: daily std × √252 = annual std (because variance scales linearly with time, std scales with √time).

### Calmar Ratio

```python
# From: backtester.py, line 587
calmar = round(return_pct / max_dd, 2)
```

**What it means:** How much return do you get per unit of maximum pain (drawdown)?

```
Calmar < 1:  Return doesn't justify the drawdown risk
Calmar > 3:  Excellent (₹3 return for every ₹1 of max loss)
```

### Per-Regime Breakdown (Research Metric)

```python
# From: backtester.py, lines 589-600
for reg in regimes_seen:
    reg_trades = [t for t in trades if t.get("regime") == reg]
    regime_breakdown[reg] = {
        "total_trades": len(reg_trades),
        "wins":         len(wins),
        "win_rate_pct": ...,
        "total_pnl":    ...,
    }
```

**Why this is powerful:** It tells you exactly WHICH regime your strategy works in:

```json
{
  "TRENDING_UP":    { "win_rate_pct": 65.2, "total_pnl": 8200 },
  "RANGEBOUND":     { "win_rate_pct": 48.1, "total_pnl": -1200 },
  "HIGH_VOLATILITY":{ "win_rate_pct": 38.5, "total_pnl": -3100 }
}
```
→ "Stop trading in HIGH_VOLATILITY regime, only trade TRENDING_UP"

---

## 11. The API Layer — Exposing the Backtester

### File: `app/api/routes/backtest_router.py`

```python
class BacktestRequest(BaseModel):
    symbol:          str              # e.g., "RELIANCE"
    period:          str = "1mo"      # lookback: "5d", "1mo", "3mo", "1y"
    interval:        str = "5m"       # candle size: "1m", "5m", "15m", "1h", "1d"
    initial_capital: float = 100000.0 # starting capital in ₹
    stop_loss_pct:   float = 1.5      # fixed SL % (fallback if no ATR)
    take_profit_pct: float = 3.0      # fixed TP % (fallback if no ATR)
    size_pct:        float = 10.0     # % of capital per trade
    slippage_pct:    float = 0.05     # 5 basis points slippage
```

```python
@router.post("/backtest")
async def execute_backtest(request: BacktestRequest, current_user: User = Depends(require_viewer)):
    result = await run_backtest(
        symbol=request.symbol,
        period=request.period,
        interval=request.interval,
        initial_capital=request.initial_capital,
        stop_loss_pct=request.stop_loss_pct,
        take_profit_pct=request.take_profit_pct,
        size_pct=request.size_pct,
        slippage_pct=request.slippage_pct,
    )
    return result
```

**Why `require_viewer`?** Authentication dependency. Even a read-only viewer can run backtests, so we use the least privileged role. You don't need trader permissions just to analyze historical data.

### Test Script

```python
# backend/test_backtest_scratch.py
import asyncio
from app.graph.backtester import run_backtest

async def run_simulation():
    res = await run_backtest(
        symbol="RELIANCE",
        period="1mo",
        interval="1h",
        initial_capital=100000.0,
        stop_loss_pct=1.5,
        take_profit_pct=3.0,
        size_pct=10.0,
    )
    print("METRICS:", res["metrics"])
    print("TOTAL TRADES:", len(res["trades"]))
    print("EQUITY CURVE POINTS:", len(res["equity_curve"]))

asyncio.run(run_simulation())
```

**To run:** From `backend/` directory: `python test_backtest_scratch.py`

---

## 12. The Complete Data Flow — From Request to Result

```
POST /api/v1/backtest
{ symbol: "RELIANCE", period: "1mo", interval: "1h" }
          │
          ▼
  backtest_router.py
  Validate input with Pydantic
  Authenticate user (require_viewer)
          │
          ▼
  run_backtest() in backtester.py
          │
          ├── Step 1: Fetch Data
          │   load_historical_candles("RELIANCE", "1mo", "1h")
          │   → tries Zerodha API → falls back to yfinance
          │   → returns ~720 candles (1mo × 8h/day × ~1h candles)
          │
          ├── Step 2: Build DataFrame
          │   df = pd.DataFrame(candles)
          │   df[col] = df[col].astype(float)  # ensure numeric types
          │
          ├── Step 3: Precompute Indicators (VECTORIZED)
          │   df = precompute_indicators_and_regimes(df)
          │   ← adds columns: rsi, macd, macd_signal, macd_hist,
          │                    bollinger_upper/middle/lower,
          │                    adx, ema_slope, vol, raw_vol,
          │                    ema_9, ema_21, ema_9_prev, ema_21_prev,
          │                    atr, regime
          │
          ├── Step 4: Initialize State
          │   cash=100000, position=0, direction="NONE"
          │
          ├── Step 5: Simulation Loop (t = 30 to N)
          │   For each candle:
          │   ├── Read indicators from df.iloc[t-1]
          │   ├── Compute weighted signal score (regime-adaptive)
          │   ├── Apply volatility filter
          │   ├── Determine decision (BUY/SELL/HOLD)
          │   ├── Compute ATR-based dynamic SL/TP
          │   ├── Check exit conditions (SL, TP, reversal)
          │   ├── Execute new entries if flat
          │   └── Append equity point to equity_curve
          │
          ├── Step 6: Close any remaining open position
          │
          └── Step 7: Compute Metrics
              win_rate, total_pnl, return_pct, profit_factor,
              max_drawdown, sharpe_ratio, calmar_ratio,
              avg_win_pct, avg_loss_pct, win_loss_ratio,
              regime_breakdown
                    │
                    ▼
              Return JSON:
              {
                "symbol": "RELIANCE",
                "metrics": { ... },
                "regime_breakdown": { ... },
                "trades": [ ... ],
                "equity_curve": [ ... ]
              }
```

---

## 13. Critical Biases to Avoid

These are the ways a backtest can lie to you — memorize them.

### 1. Lookahead Bias ⚠️ (Most Common)

**What it is:** Using future data to make a past decision.

**In our code:** We prevent this by always reading `df.iloc[t - 1]` (previous bar's indicators), never `df.iloc[t]`.

```python
# WRONG (lookahead bias):
row_curr = df.iloc[t]   # uses current bar's closing RSI to enter at its open!

# CORRECT (our approach):
row_prev = df.iloc[t - 1]  # only use data that was available BEFORE this candle
open_price = current_candle["open"]  # enter at this candle's open
```

### 2. Survivorship Bias

**What it is:** Backtesting only on stocks that still exist today. Stocks that went bankrupt or were delisted are excluded from your dataset.

**Fix:** Use a point-in-time database that includes delisted stocks. For NSE, this means getting historical index constituents, not just current ones.

### 3. Overfitting (Curve Fitting)

**What it is:** Optimizing parameters so perfectly on historical data that the strategy "memorizes" the past rather than finding real patterns.

```
Example: If you test 1000 combinations of (RSI_period, MACD_fast, MACD_slow)
and pick the best one, you've likely found a pattern that only exists
in THAT specific historical data.
```

**Fix:** Use Walk-Forward Optimization — train on period 1, test on period 2, then train on period 2, test on period 3. If performance holds across multiple test windows, the edge is real.

### 4. Ignoring Transaction Costs

**What it is:** Backtest without brokerage, taxes, slippage → strategy appears profitable. Live trading → strategy loses.

**Our fix:** `calculate_transaction_cost()` models all 6 components of NSE equity trading costs.

### 5. Using Closing Prices for Entry

**What it is:** A strategy says "buy when RSI closes below 30" and backtests the entry at that exact closing price. But in reality, by the time you see the close, the next candle has already opened.

**Our fix:** Entry is always at the **next candle's open**, after confirming previous candle's signal.

### 6. Not Accounting for Liquidity

**What it is:** Assuming you can buy 10,000 shares of a thinly traded stock at the exact price shown. In reality, you'd move the market trying to fill that order.

**Partial fix:** Our slippage model accounts for some of this, but a proper fix requires order book data.

---

## 14. What to Learn Next — Professional AI Trading Roadmap

Below is the complete learning path to become a professional AI trading engineer. Topics are organized by priority and depth.

### LEVEL 1 — SOLIDIFY THE FOUNDATION (Next 1–3 Months)

#### A. Walk-Forward Optimization
Test your parameter search honestly:
```
Train Window 1 → Test Window 1
             Train Window 2 → Test Window 2
                          Train Window 3 → Test Window 3
```
**Tool:** `pyalgotrade`, `backtrader`, or build your own.

#### B. Monte Carlo Simulation
Instead of one backtest path, run 1000 random permutations of trade order:
```python
import random
results = []
for _ in range(1000):
    shuffled = random.sample(trades, len(trades))
    simulated_equity = simulate_equity_curve(shuffled)
    results.append(max_drawdown(simulated_equity))
# Distribution of possible drawdowns
```
**Why?** The sequence of wins/losses matters. Monte Carlo shows you the distribution of possible outcomes, not just the historical one.

#### C. Parameter Sensitivity Analysis
Run backtest across a grid of parameters:
```python
for sl_pct in [0.5, 1.0, 1.5, 2.0, 2.5]:
    for tp_pct in [1.0, 2.0, 3.0, 4.0]:
        result = run_backtest(..., stop_loss_pct=sl_pct, take_profit_pct=tp_pct)
        print(f"SL={sl_pct}%, TP={tp_pct}% → Sharpe={result['metrics']['sharpe_ratio']}")
```
A robust strategy should have **smooth, gradual** performance curves, not sharp peaks.

### LEVEL 2 — ADVANCED STRATEGY DESIGN (Months 3–6)

#### A. Kelly Criterion for Position Sizing
Replace fixed 10% with mathematically optimal sizing:
```python
def kelly_fraction(win_rate, win_loss_ratio):
    """
    win_rate      = P(win) e.g. 0.55 = 55%
    win_loss_ratio = avg_win / avg_loss e.g. 2.0
    """
    b = win_loss_ratio
    p = win_rate
    q = 1 - win_rate
    kelly = (b * p - q) / b
    return max(0, min(kelly, 0.25))  # cap at 25% (half-kelly)
```

#### B. Multi-Timeframe Analysis
The power of combining signals from different timeframes:
```
Daily chart  → Trend direction (primary bias)
4H chart     → Entry confirmation
15M chart    → Precise entry timing
```

#### C. Portfolio Backtesting
Backtest a **portfolio** of stocks simultaneously, not just one. Manage correlation risk: if you hold 10 tech stocks, they all fall together. A portfolio backtester must track:
- Total portfolio equity
- Correlation between positions
- Maximum total position size

#### D. Options Strategy Backtesting
Options are fundamentally different — price, Greeks, time decay, and volatility must all be modeled. Learn:
- Black-Scholes model
- Delta hedging in backtests
- IV rank for entry timing (buy options when IV is low)

### LEVEL 3 — MACHINE LEARNING FOR TRADING (Months 6–12)

#### A. Feature Engineering for Price Data
Turn raw OHLCV into ML-ready features:
```python
features = {
    "rsi_14":           ...,
    "rsi_pct_rank":     rsi vs 252-day history (percentile rank),
    "bb_width":         (upper - lower) / middle,
    "bb_position":      (close - lower) / (upper - lower),
    "vol_regime":       current_vol / hist_vol_252d,
    "volume_surge":     current_volume / avg_volume_20d,
    "trend_strength":   adx / 100,
    "returns_1":        1-bar return,
    "returns_5":        5-bar return,
    "returns_21":       21-bar return,
}
```

#### B. Supervised Learning for Signal Generation
Replace the hard-coded scoring formula with ML:
```python
from sklearn.ensemble import GradientBoostingClassifier

# Label: did price go up >1% in next 20 bars?
y = (future_return_20bars > 0.01).astype(int)
X = features_df

# Train on first 70% of data
model = GradientBoostingClassifier(n_estimators=200)
model.fit(X_train, y_train)

# Backtest on remaining 30% (out-of-sample!)
predictions = model.predict_proba(X_test)[:, 1]  # probability of up move
```

**Critical:** Never train and test on the same data. Always use a **time-based split**, never random split (random split would leak future info into training).

#### C. Reinforcement Learning for Trade Execution
Our system already has RL concepts. The next step is a proper RL agent:
```
State:  [RSI, MACD, regime, ATR, position, unrealized_PnL, time_in_trade]
Action: [HOLD, BUY_10%, SELL_ALL]
Reward: PnL - transaction_costs - drawdown_penalty
```
**Libraries:** Stable Baselines3, RLlib, Ray

#### D. LSTM/Transformer for Time Series
Deep learning models that capture temporal patterns:
```python
from tensorflow.keras.layers import LSTM, Dense

model = Sequential([
    LSTM(128, return_sequences=True, input_shape=(lookback, n_features)),
    LSTM(64),
    Dense(32, activation='relu'),
    Dense(1, activation='sigmoid')   # P(price goes up next bar)
])
```
**State of the art (2025):** Temporal Fusion Transformer (TFT), PatchTST, N-BEATS.

### LEVEL 4 — PROFESSIONAL INFRASTRUCTURE (Year 1+)

#### A. Real-Time Tick Data Processing
Move from 1-minute candles to sub-second tick data:
```
Zerodha tick: { price: 2880.5, qty: 100, timestamp: "10:23:45.238" }
                                                                 ↑ millisecond
```
**Tech stack:** Apache Kafka (tick streaming) → Apache Flink (stream processing) → InfluxDB (time-series DB)

#### B. Market Microstructure
Understanding the order book:
- **Level 1 data:** just best bid/ask
- **Level 2 data:** full order book depth
- **VWAP/TWAP execution:** slice large orders to minimize market impact
- **Order flow imbalance:** predict short-term price direction from buy/sell pressure

#### C. Execution Algorithms
How professional traders minimize market impact:
- **TWAP:** Time-Weighted Average Price — spread orders evenly over time
- **VWAP:** Volume-Weighted Average Price — execute proportionally to market volume
- **Participation rate:** "Be 10% of market volume" — follow the market

#### D. Risk Management Systems
Professional risk infrastructure:
- **VaR (Value at Risk):** "With 95% confidence, we won't lose more than X in a day"
- **CVaR (Conditional VaR):** Expected loss given you're already in the worst 5%
- **Position limits:** Max loss per trade, per day, per strategy
- **Kill switch:** Auto-stop all trading if daily loss exceeds threshold (we built this!)
- **Correlation monitoring:** Real-time correlation between open positions

#### E. Alternative Data
What separates top quant funds from retail traders:
- **Satellite imagery:** Counting cars in parking lots to predict retailer sales
- **Credit card data:** Real-time consumer spending trends
- **NLP on earnings calls:** Extract sentiment from CEO tone during calls
- **Web scraping:** Job postings, patent filings, app download rankings

#### F. High-Frequency Trading Concepts
Not for retail, but important to understand:
- **Co-location:** Placing servers physically next to exchange servers (microsecond edges)
- **Statistical arbitrage:** Pairs trading between correlated instruments
- **Market making:** Providing liquidity by quoting both bid and ask

### LEVEL 5 — PROFESSIONAL CERTIFICATIONS & RESEARCH

| Area            | What to Study                        | Resource                          |
|-----------------|--------------------------------------|-----------------------------------|
| Quantitative Finance | Stochastic calculus, options pricing | Hull: *Options, Futures, Derivatives* |
| Algorithmic Trading | System design, execution            | Narang: *Inside the Black Box*    |
| ML for Finance  | Time series, feature engineering    | López de Prado: *Advances in Financial ML* |
| Backtesting     | Avoiding biases, WFO                 | Chan: *Quantitative Trading*      |
| Risk Management | VaR, CVaR, portfolio theory          | Roncalli: *Risk Management*       |
| Python Finance  | pandas, numpy, scipy, statsmodels   | Hilpisch: *Python for Finance*    |
| Research Papers | Academic alpha sources               | SSRN.com, arXiv q-fin section     |

### Key Libraries to Master

```python
# Core Data Science
import pandas as pd        # DataFrames, time series
import numpy as np         # vectorized math
import scipy.stats as stats # statistics

# Trading & Backtesting
import yfinance as yf       # data (you already use this)
import backtrader as bt     # full backtesting framework (alternative)
import pyfolio              # performance tearsheets
import empyrical            # financial metrics (Sharpe, Calmar, etc.)
import zipline              # Quantopian-style backtesting

# Machine Learning
import sklearn              # classical ML
import xgboost, lightgbm    # gradient boosting (excellent for finance)
import tensorflow, pytorch  # deep learning
import stable_baselines3    # reinforcement learning

# Visualization
import plotly               # interactive charts
import matplotlib           # static charts
import seaborn              # statistical plots

# Alternative Data
import fredapi              # Federal Reserve economic data
import pandas_datareader    # various economic databases
import quandl               # premium financial data
```

### The Professional Trading Engineer Stack (2025)

```
Strategy Research:
  Python + Jupyter + backtrader/zipline → validate edges

Data Infrastructure:
  Apache Kafka → real-time tick streams
  InfluxDB     → time-series storage
  PostgreSQL   → trade records (you already have this)
  Redis        → real-time indicator cache (you already have this)

ML Pipeline:
  MLflow       → experiment tracking
  DVC          → data versioning
  Ray          → distributed hyperparameter search

Execution:
  Zerodha KiteConnect → order placement (you have this)
  FIX protocol        → institutional execution

Monitoring:
  Grafana      → real-time P&L dashboards
  Prometheus   → system metrics
  Sentry       → error tracking

Deployment:
  Docker + Kubernetes → scalable deployment (you have Docker)
  GitHub Actions      → CI/CD (you have this)
```

---

## QUICK REFERENCE — The Formula Sheet

### Core Indicator Formulas

```
RSI = 100 - (100 / (1 + avg_gain/avg_loss))     [period=14, Wilder RMA]
MACD = EMA(12) - EMA(26),  Signal = EMA(MACD, 9)
Bollinger = SMA(20) ± 2σ
ATR = smooth(max(H-L, |H-PrevC|, |L-PrevC|))    [Wilder RMA]
ADX = smooth(|+DI - -DI| / (+DI + -DI) × 100)
```

### Performance Metric Formulas

```
Win Rate       = Winning Trades / Total Trades
Profit Factor  = Gross Profit / Gross Loss
Win/Loss Ratio = Avg Win % / Avg Loss %
Max Drawdown   = (Peak - Trough) / Peak × 100
Sharpe Ratio   = (Mean Return / Std Return) × √252
Calmar Ratio   = Return % / Max Drawdown %
Kelly Fraction = (Win_Rate × Win/Loss - Loss_Rate) / Win/Loss
```

### NSE Transaction Cost Summary

```
Brokerage:    min(₹20, 0.03% of value)
STT:          0.025% on SELL side only
Exchange Txn: 0.00345% both sides
SEBI Fee:     ₹10 per crore (≈ 0.0001%)
GST:          18% of (Brokerage + Exchange + SEBI)
Stamp Duty:   0.003% on BUY side only
Round-trip:   ≈ 0.10% total
```

### Signal Score Weights by Regime

```
RANGEBOUND:    RSI×1.5 + BB×1.5 + MACD×0.2
TRENDING:      MACD×1.2 + EMA_Cross×1.5
HIGH_VOL:      (RSI×0.4 + MACD×0.4 + BB×0.4) × 0.5
Decision:      BUY if score > 0.2, SELL if score < -0.2
Vol Filter:    if vol > 5%, score × 0.7
```

---

*Last Updated: June 2026 | FutureEdge Handbook | backtesting_README.md*
