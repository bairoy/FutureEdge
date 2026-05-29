"""
app/scripts/bootstrap_dataset.py
==================================
Dataset bootstrapping script for model fine-tuning.

Runs the historical simulation over yfinance candles to identify technical
BUY/SELL setups. Generates professional reasoning rationales for each trade setup.
Saves the training pairs to a JSON Lines (.jsonl) file in standard OpenAI/Unsloth chat format.

FEATURES:
1. Free & Offline by default: Uses a rich rule-based expert system to draft rationales.
2. Teacher Distillation option: Pass `--distill` to use the GPT-4o-mini/GPT-4o model to write racional es.
3. Chat format compatible: Direct format for Llama-3 Supervised Fine-Tuning (SFT).

USAGE:
------
    # Dry run / Offline (creates dataset for free):
    docker compose exec backend python -m app.scripts.bootstrap_dataset --period 6mo --tickers RELIANCE,TCS,INFY,HDFCBANK,SBIN

    # Teacher distillation (requires OPENAI_API_KEY in .env):
    docker compose exec backend python -m app.scripts.bootstrap_dataset --distill --period 6mo
"""

import os
import json
import argparse
import asyncio
import numpy as np
import pandas as pd
from loguru import logger
from openai import OpenAI

from app.core.config import settings
from app.data.feed import load_historical_candles
from app.data.indicator_cache import _compute_indicators


# ============================================================
# CONFIGURABLE TICKERS
# ============================================================
DEFAULT_TICKERS = [
    "RELIANCE",
    "TCS",
    "INFY",
    "HDFCBANK",
    "ICICIBANK",
    "SBIN",
    "BHARTIARTL",
    "LTIM",
    "TATAMOTORS",
]


# ============================================================
# REGIME DETECTION LOGIC (Replicated from RegimeAgent)
# ============================================================
def detect_regime(df: pd.DataFrame) -> tuple[str, float, float]:
    """
    Computes ATR, ADX, EMA Slope, and Volatility to classify regime.
    """
    if len(df) < 30:
        return "RANGEBOUND", 20.0, 0.02

    # 1. Average True Range (ATR)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift(1)).abs()
    tr3 = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    period = 14
    atr = tr.rolling(window=period).mean()
    atr = atr.replace(0, 1e-10)

    # 2. Directional Movement (+DM, -DM)
    up_move = df["high"].diff()
    down_move = df["low"].diff().abs()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_di = 100 * (pd.Series(plus_dm).rolling(window=period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).rolling(window=period).mean() / atr)

    # 3. DX and ADX (Average Directional Index)
    denom = plus_di + minus_di
    denom = denom.replace(0, 1e-10)
    dx = 100 * (plus_di - minus_di).abs() / denom
    adx = dx.rolling(window=period).mean()

    latest_adx = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 20.0

    # 4. EMA 20 Slope
    ema20 = df["close"].ewm(span=20, adjust=False).mean()
    ema_slope = float(ema20.diff(3).iloc[-1]) if len(ema20) >= 3 else 0.0

    # 5. Volatility (std of log returns)
    returns = np.log(df["close"] / df["close"].shift(1))
    vol = float(returns.tail(30).std()) if len(returns) >= 30 else 0.02
    if pd.isna(vol):
        vol = 0.02

    # 6. Classification Logic
    if latest_adx > 25:
        slope_threshold = float(df["close"].iloc[-1]) * 0.0001
        if ema_slope > slope_threshold:
            regime = "TRENDING_UP"
        elif ema_slope < -slope_threshold:
            regime = "TRENDING_DOWN"
        else:
            regime = "RANGEBOUND"
    else:
        if vol > 0.03:
            regime = "HIGH_VOLATILITY"
        else:
            regime = "RANGEBOUND"

    return regime, latest_adx, vol


# ============================================================
# TECHNICAL SIGNAL LOGIC (Replicated from SignalAgent)
# ============================================================
def compute_signal_vote(ind: dict, regime: str, price: float, vol: float) -> tuple[str, float, str]:
    rsi = ind.get("rsi", 50.0)
    macd_val = ind.get("macd", 0.0)
    signal_val = ind.get("macd_signal", 0.0)
    hist = ind.get("macd_hist", 0.0)
    bollinger_upper = ind.get("bollinger_upper", price)
    bollinger_lower = ind.get("bollinger_lower", price)

    # Base scores
    rsi_score = 0.0
    if rsi < 30:
        rsi_score = 0.3
    elif rsi > 70:
        rsi_score = -0.3

    macd_score = 0.0
    if hist > 0 and macd_val > signal_val:
        macd_score = 0.25
    elif hist < 0 and macd_val < signal_val:
        macd_score = -0.25

    bb_score = 0.0
    if price < bollinger_lower:
        bb_score = 0.2
    elif price > bollinger_upper:
        bb_score = -0.2

    # Dynamic Weighting
    reasons = []
    score = 0.0

    if regime == "RANGEBOUND":
        score = (rsi_score * 1.5) + (bb_score * 1.5) + (macd_score * 0.2)
        if rsi < 30 or rsi > 70:
            reasons.append(f"RSI oversold/overbought boundaries ({rsi:.1f})")
        if price < bollinger_lower or price > bollinger_upper:
            reasons.append("Price touching Bollinger Band boundaries")
            
    elif regime in ("TRENDING_UP", "TRENDING_DOWN"):
        score = macd_score * 1.8
        if regime == "TRENDING_UP":
            if rsi_score > 0:
                score += rsi_score * 0.5
        elif regime == "TRENDING_DOWN":
            if rsi_score < 0:
                score += rsi_score * 0.5
        if hist > 0 and macd_val > signal_val:
            reasons.append("MACD bullish momentum crossover")
        elif hist < 0 and macd_val < signal_val:
            reasons.append("MACD bearish momentum crossover")
            
    elif regime == "HIGH_VOLATILITY":
        score = ((rsi_score * 0.5) + (macd_score * 0.5) + (bb_score * 0.5)) * 0.5
        reasons.append("Dampening signals due to high market volatility")
        
    else:
        score = rsi_score + macd_score + bb_score
        reasons.append("Standard indicators scoring fallback")

    if vol > 0.05:
        score *= 0.7

    # Decision conversion
    if score > 0.2:
        decision = "BUY"
        conf = min(0.5 + score, 0.95)
    elif score < -0.2:
        decision = "SELL"
        conf = min(0.5 + abs(score), 0.95)
    else:
        decision = "HOLD"
        conf = 0.5 + abs(score)

    reasoning = " & ".join(reasons) if reasons else "indicators consolidation"
    return decision, conf, reasoning


# ============================================================
# RULE-BASED EXPERT GENERATOR (Free, Offline Rationale)
# ============================================================
def generate_offline_rationale(
    symbol: str,
    direction: str,
    regime: str,
    ind: dict,
    vol: float,
    outcome_label: str
) -> str:
    """
    Expert heuristic builder producing highly realistic rationale text
    without external API cost.
    """
    rsi = ind.get("rsi", 50.0)
    macd_val = ind.get("macd", 0.0)
    signal_val = ind.get("macd_signal", 0.0)
    hist = ind.get("macd_hist", 0.0)
    
    if direction == "LONG":
        regime_desc = {
            "TRENDING_UP": "strong uptrend showing continuous higher-high structures",
            "TRENDING_DOWN": "bearish downtrend, attempting to catch a short-term pullback",
            "RANGEBOUND": "stable rangebound consolidation area, buying near the support levels",
            "HIGH_VOLATILITY": "highly volatile environment, executing with cautious position sizes"
        }.get(regime, "standard consolidation phase")
        
        indicators_desc = []
        if rsi < 35:
            indicators_desc.append(f"oversold RSI reading of {rsi:.1f}")
        if hist > 0 and macd_val > signal_val:
            indicators_desc.append("a bullish MACD convergence line crossover")
        if not indicators_desc:
            indicators_desc.append("support from lower Bollinger Band price rejections")
            
        r_sentence = f"The system triggered a LONG position on {symbol} within a {regime_desc}. "
        r_sentence += f"Key entry triggers include " + " and ".join(indicators_desc) + ". "
        r_sentence += f"Volatility is currently recorded at {vol*100:.2f}%, and the risk parameters suggest checking intraday position limits."
        
    else: # SHORT
        regime_desc = {
            "TRENDING_UP": "strongly bullish trend, attempting to fade localized exhaustion",
            "TRENDING_DOWN": "dominant downtrend showing clear lower-low sequences",
            "RANGEBOUND": "well-defined trading range, selling near the resistance boundaries",
            "HIGH_VOLATILITY": "high-volatility environment with wide intraday ranges"
        }.get(regime, "standard consolidation phase")
        
        indicators_desc = []
        if rsi > 65:
            indicators_desc.append(f"overbought RSI reading of {rsi:.1f}")
        if hist < 0 and macd_val < signal_val:
            indicators_desc.append("a bearish MACD signal crossover")
        if not indicators_desc:
            indicators_desc.append("resistance levels tested near the upper Bollinger Band")
            
        r_sentence = f"The system triggered a SHORT position on {symbol} due to a {regime_desc}. "
        r_sentence += f"This decision is supported by " + " and ".join(indicators_desc) + ". "
        r_sentence += f"Risk factors include volatility metrics at {vol*100:.2f}% and potential support levels forming nearby."
        
    return r_sentence


# ============================================================
# TEACHER DISTILLATION CALL (GPT-4o / GPT-4o-mini)
# ============================================================
async def distill_teacher_rationale(
    client: OpenAI,
    symbol: str,
    direction: str,
    regime: str,
    ind: dict,
    vol: float,
    signal_vote: dict,
    sentiment_vote: dict
) -> str:
    """
    Asks the teacher model to write an institutional-grade explanation.
    """
    prompt = f"""You are the reasoning module of an algorithmic trading system for Indian equities.
The system has just decided to go {direction} on {symbol}.

Agent votes:
  SignalAgent: {signal_vote['decision']} (confidence={signal_vote['confidence']:.2f}) — {signal_vote['reasoning']}
  SentimentAgent: {sentiment_vote['decision']} (confidence={sentiment_vote['confidence']:.2f}) — {sentiment_vote['reasoning']}
  RiskAgent: HOLD (confidence=0.80) — All risk checks passed
  PortfolioAgent: HOLD (confidence=0.60) — Portfolio healthy

Consensus scores: buy={1.0 if direction == 'LONG' else 0.0:.2f}, sell={1.0 if direction == 'SHORT' else 0.0:.2f}
Risk score: 0.20
Market regime: {regime}
Volatility: {vol*100:.2f}%
RSI: {ind['rsi']:.1f}
MACD Histogram: {ind['macd_hist']:.4f}

Write a 2-3 sentence explanation for the risk manager who will review this trade.
Explain WHY the system chose {direction}, what the key signals were, and what risk factors they should consider. Be specific about the indicators. Be concise. Do not use bullet points. Do not recommend approving or rejecting — just explain the reasoning.
"""

    loop = asyncio.get_event_loop()
    
    def _call():
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a professional financial AI reasoning engine."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=250
        )
        return response.choices[0].message.content.strip()

    try:
        return await loop.run_in_executor(None, _call)
    except Exception as e:
        logger.error(f"Teacher API call failed: {e}")
        # Fallback
        return generate_offline_rationale(symbol, direction, regime, ind, vol, "UNKNOWN")


# ============================================================
# MAIN PIPELINE WORKER
# ============================================================
async def process_ticker(
    ticker: str,
    period: str,
    interval: str,
    distill: bool,
    client: OpenAI | None,
    semaphore: asyncio.Semaphore
) -> list[dict]:
    """
    Downloads candles for one ticker, runs backtest simulations,
    identifies setups, and compiles prompt-response pairs.
    """
    logger.info(f"📥 Loading candles for {ticker}...")
    # Pass ticker directly — load_historical_candles will format it
    candles = load_historical_candles(ticker, period=period, interval=interval)
    
    if not candles or len(candles) < 40:
        logger.warning(f"No candles loaded for {ticker}")
        return []

    df_full = pd.DataFrame(candles)
    for col in ["open", "high", "low", "close", "volume"]:
        df_full[col] = df_full[col].astype(float)

    dataset_items = []
    
    # Run historical simulation window loop
    # Start at index 30 to have history for indicators
    t = 30
    while t < len(candles) - 10: # Stop 10 candles before end to check forward outcome
        window = candles[:t]
        df_window = df_full.iloc[:t]
        
        # 1. Compute regime and indicators
        regime, adx, vol = detect_regime(df_window)
        ind = _compute_indicators(window)
        
        # 2. Get Signal decision
        price = float(df_full.iloc[t]["open"])
        decision, conf, reasoning = compute_signal_vote(ind, regime, price, vol)
        
        if decision in ("BUY", "SELL"):
            # A trade was triggered. Let's see its outcome looking forward (up to 15 candles)
            entry_price = price
            direction = "LONG" if decision == "BUY" else "SHORT"
            
            # Outcome check
            sl = 1.5 / 100.0
            tp = 3.0 / 100.0
            outcome_label = "HOLD/EXPIRED"
            pnl_pct = 0.0
            
            for k in range(t, min(t + 15, len(candles))):
                high = float(df_full.iloc[k]["high"])
                low = float(df_full.iloc[k]["low"])
                close = float(df_full.iloc[k]["close"])
                
                if direction == "LONG":
                    if low <= entry_price * (1 - sl):
                        outcome_label = "LOSS"
                        pnl_pct = -1.5
                        break
                    elif high >= entry_price * (1 + tp):
                        outcome_label = "WIN"
                        pnl_pct = 3.0
                        break
                else: # SHORT
                    if high >= entry_price * (1 + sl):
                        outcome_label = "LOSS"
                        pnl_pct = -1.5
                        break
                    elif low <= entry_price * (1 - tp):
                        outcome_label = "WIN"
                        pnl_pct = 3.0
                        break
                        
            # Format prompt inputs
            clean_symbol = ticker.split(".")[0]
            direction_label = "LONG" if direction == "LONG" else "SHORT"
            
            signal_vote = {"decision": decision, "confidence": conf, "reasoning": reasoning}
            
            # Simple keyword-based mock sentiment
            sent_decision = "BUY" if direction == "LONG" else "SELL"
            sent_reasoning = f"Positive news flows related to {clean_symbol} earnings beats" if direction == "LONG" else f"Negative news coverage of sector consolidation affecting {clean_symbol}"
            sentiment_vote = {"decision": sent_decision, "confidence": 0.85, "reasoning": sent_reasoning}

            # 3. Generate rationale (Teacher or Offline Expert)
            if distill and client:
                async with semaphore:
                    logger.info(f"Distilling rationale from teacher for {clean_symbol} {direction_label}...")
                    rationale = await distill_teacher_rationale(
                        client, clean_symbol, direction_label, regime, ind, vol, signal_vote, sentiment_vote
                    )
            else:
                rationale = generate_offline_rationale(clean_symbol, direction_label, regime, ind, vol, outcome_label)

            # 4. Create Chat ML formatted item
            messages = [
                {
                    "role": "system",
                    "content": "You are a professional financial AI reasoning engine. Your job is to clearly explain algorithmic trading decisions to human risk managers."
                },
                {
                    "role": "user",
                    "content": f"You are the reasoning module of an algorithmic trading system for Indian equities.\n\nThe system has just decided to go {direction_label} on {clean_symbol}.\n\nAgent votes:\n  SignalAgent: {signal_vote['decision']} (confidence={signal_vote['confidence']:.2f}) — {signal_vote['reasoning']}\n  SentimentAgent: {sentiment_vote['decision']} (confidence={sentiment_vote['confidence']:.2f}) — {sentiment_vote['reasoning']}\n  RiskAgent: HOLD (confidence=0.80) — All risk checks passed\n  PortfolioAgent: HOLD (confidence=0.60) — Portfolio healthy — 0 positions\n\nConsensus scores: buy={1.0 if direction_label == 'LONG' else 0.0:.2f}, sell={1.0 if direction_label == 'SHORT' else 0.0:.2f}\nRisk score: 0.20\nAgent disagreement: 0.15\nMarket regime: {regime}\n\nWrite a 2-3 sentence explanation for the risk manager who will review this trade. Explain WHY the system chose {direction_label}, what the key signals were, and what risk factors they should consider. Be specific about the indicators. Be concise. Do not use bullet points. Do not recommend approving or rejecting — just explain the reasoning."
                },
                {
                    "role": "assistant",
                    "content": rationale
                }
            ]
            
            dataset_items.append({
                "messages": messages,
                "metadata": {
                    "symbol": clean_symbol,
                    "regime": regime,
                    "volatility": vol,
                    "direction": direction_label,
                    "outcome": outcome_label,
                    "pnl_pct": pnl_pct
                }
            })
            
            # Skip forward to avoid overlapping trade signals
            t += 10
        else:
            t += 1
            
    return dataset_items


# ============================================================
# MAIN SCRIPT EXECUTION
# ============================================================
async def main():
    parser = argparse.ArgumentParser(description="Bootstrap trading dataset for fine-tuning.")
    parser.add_argument("--tickers", type=str, default="", help="Comma separated tickers (e.g. RELIANCE,TCS)")
    parser.add_argument("--period", type=str, default="3mo", help="yfinance download period (e.g. 3mo, 6mo, 1y)")
    parser.add_argument("--interval", type=str, default="1h", help="yfinance candle interval (1h, 1d)")
    parser.add_argument("--distill", action="store_true", help="Use OpenAI API key for teacher distillation")
    parser.add_argument("--limit", type=int, default=1000, help="Maximum training examples to output")
    args = parser.parse_args()

    # Parse Tickers
    tickers = DEFAULT_TICKERS
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]

    # Setup OpenAI Client if distill mode
    client = None
    if args.distill:
        api_key = settings.OPENAI_API_KEY
        if not api_key:
            logger.error("OPENAI_API_KEY not found in config/env. Falling back to rule-based expert.")
            args.distill = False
        else:
            client = OpenAI(api_key=api_key)

    # Concurrency controller
    semaphore = asyncio.Semaphore(5) # limit to 5 concurrent api requests
    
    logger.info(f"🚀 Starting Bootstrapper | Tickers={len(tickers)} | Period={args.period} | Distill={args.distill}")
    
    tasks = [
        process_ticker(ticker, args.period, args.interval, args.distill, client, semaphore)
        for ticker in tickers
    ]
    
    results = await asyncio.gather(*tasks)
    
    # Flatten list of lists
    all_items = [item for sublist in results for item in sublist]
    
    # Apply limit
    all_items = all_items[:args.limit]
    
    if not all_items:
        logger.warning("No trading setups were identified. Try a longer period or different tickers.")
        return

    # Create directories if they do not exist
    output_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "finetuning_dataset.jsonl")

    # Write JSONL
    logger.info(f"✍️ Writing dataset to {output_file}...")
    with open(output_file, "w") as f:
        for item in all_items:
            f.write(json.dumps(item) + "\n")

    # Log summary statistics
    total = len(all_items)
    wins = len([item for item in all_items if item["metadata"]["outcome"] == "WIN"])
    losses = len([item for item in all_items if item["metadata"]["outcome"] == "LOSS"])
    holds = len([item for item in all_items if item["metadata"]["outcome"] == "HOLD/EXPIRED"])
    
    logger.info(f"✅ Bootstrapping Completed successfully!")
    logger.info(f"  - Total Examples: {total}")
    logger.info(f"  - Winning Setups: {wins} ({wins/total*100:.1f}%)")
    logger.info(f"  - Losing Setups:  {losses} ({losses/total*100:.1f}%)")
    logger.info(f"  - Expired Setups: {holds} ({holds/total*100:.1f}%)")


if __name__ == "__main__":
    asyncio.run(main())
