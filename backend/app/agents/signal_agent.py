"""
app/agents/signal_agent.py
===========================
Technical analysis agent — reads live candles, computes
indicators via the cache, generates BUY/SELL/HOLD vote.

CHANGES FROM ORIGINAL:
-----------------------
1. Now ASYNC  (async def signal_agent_node)
   Allows await calls inside without blocking the event loop.

2. Uses indicator_cache instead of computing from scratch
   RSI/MACD/Bollinger are cached in Redis for 60 seconds.
   Back-to-back cycles reuse the same values — much faster.

3. Reads candles from live feed when available
   Falls back to whatever candles are in state (mock/historical).

HOW IT FITS IN THE GRAPH:
--------------------------
START → signal_agent_node → orchestrator

This runs IN PARALLEL with sentiment_agent, risk_agent,
and portfolio_agent (all four start at the same time).
"""

from loguru import logger
import pandas as pd

from app.graph.state import AgentState, AgentVote
from app.data.indicator_cache import get_indicators


# ============================================================
# SIGNAL AGENT NODE  (async)
# ============================================================

async def signal_agent_node(state: AgentState) -> dict:
    """
    Reads market candles → computes indicators → votes BUY/SELL/HOLD.

    Returns a partial state update with signal_vote set.
    """

    try:
        ctx    = state["market_context"]
        symbol = ctx.symbol
        price  = ctx.current_price

        # --------------------------------------------------------
        # VALIDATE DATA
        # --------------------------------------------------------

        if not ctx.ohlcv_1m:
            logger.warning(f"SignalAgent | No candles for {symbol}")

            return {
                "signal_vote": AgentVote(
                    agent     = "SignalAgent",
                    decision  = "HOLD",
                    confidence= 0.4,
                    reasoning = "No candle data available",
                ),
                "completed_nodes": ["signal_agent"],
                "logs": [f"SignalAgent skipped — no data for {symbol}"],
            }

        # --------------------------------------------------------
        # GET INDICATORS  (from cache or computed fresh)
        # --------------------------------------------------------

        # get_indicators checks Redis first.
        # If cached value exists and is < 60s old → returns it.
        # Otherwise computes from candles and caches the result.

        ind = await get_indicators(symbol, ctx.ohlcv_1m)

        rsi             = ind["rsi"]
        macd_val        = ind["macd"]
        signal_val      = ind["macd_signal"]
        hist            = ind["macd_hist"]
        bollinger_upper = ind["bollinger_upper"]
        bollinger_middle= ind["bollinger_middle"]
        bollinger_lower = ind["bollinger_lower"]
        ema_9           = ind.get("ema_9", price)
        ema_21          = ind.get("ema_21", price)
        ema_9_prev      = ind.get("ema_9_prev", ema_9)   # Previous bar EMA — from cache, no recompute
        ema_21_prev     = ind.get("ema_21_prev", ema_21)  # Previous bar EMA — from cache, no recompute
        vwap            = ind.get("vwap", price)
        atr             = ind.get("atr", price * 0.002)   # Real ATR — for SL/TP in orchestrator
        from_cache      = ind["from_cache"]

        # --------------------------------------------------------
        # CALL MARKET TOOLS (Order Book Spread & Live Price)
        # --------------------------------------------------------
        from app.agents.tools.market_tools import get_live_price, get_order_book
        
        live_price = await get_live_price(symbol)
        order_book = await get_order_book(symbol)
        
        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])
        spread_pct = 0.0
        best_bid = 0.0
        best_ask = 0.0
        if bids and asks:
            best_bid = bids[0]["price"]
            best_ask = asks[0]["price"]
            if best_bid > 0:
                spread_pct = (best_ask - best_bid) / best_bid

        # --------------------------------------------------------
        # VOLUME SPIKE DETECTION
        # --------------------------------------------------------
        volume_spike = False
        avg_volume = 0.0
        latest_volume = 0
        if ctx.ohlcv_1m and len(ctx.ohlcv_1m) > 1:
            volumes = [c.get("volume", 0) for c in ctx.ohlcv_1m]
            latest_volume = volumes[-1]
            if len(volumes) > 20:
                avg_volume = sum(volumes[-21:-1]) / 20.0
            else:
                avg_volume = sum(volumes[:-1]) / len(volumes[:-1])
            
            if avg_volume > 0 and latest_volume > (avg_volume * 2.0):
                volume_spike = True

        # --------------------------------------------------------
        # REGIME-AWARE SCORING SYSTEM & INDICATORS
        # --------------------------------------------------------
        regime = ctx.regime if hasattr(ctx, "regime") else "RANGEBOUND"
        score   = 0.0
        reasons = []

        # --- 1. Dynamic RSI (Relative Strength Index) ---
        rsi_oversold = 40.0 if regime == "TRENDING_UP" else 30.0
        rsi_overbought = 60.0 if regime == "TRENDING_DOWN" else 70.0
        
        rsi_score = 0.0
        if rsi < rsi_oversold:
            rsi_score = 0.3
            reasons.append(f"RSI oversold ({rsi:.1f} < {rsi_oversold})")
        elif rsi > rsi_overbought:
            rsi_score = -0.3
            reasons.append(f"RSI overbought ({rsi:.1f} > {rsi_overbought})")

        # --- 2. MACD (Moving Average Convergence Divergence) ---
        macd_score = 0.0
        if hist > 0 and macd_val > signal_val:
            macd_score = 0.25
        elif hist < 0 and macd_val < signal_val:
            macd_score = -0.25

        # --- 3. Bollinger Bands ---
        bb_score = 0.0
        if price < bollinger_lower:
            bb_score = 0.2
        elif price > bollinger_upper:
            bb_score = -0.2

        # --- 4. EMA 9/21 Crossover (from cache — no recomputation needed) ---
        # ema_9, ema_21, ema_9_prev, ema_21_prev are read directly from the
        # indicator cache, which already computed them once. No raw candle
        # processing needed here.
        ema_9_curr  = ema_9
        ema_21_curr = ema_21

        crossover_score = 0.0
        if ema_9_curr > ema_21_curr:
            crossover_score = 0.2
            if ema_9_prev <= ema_21_prev:
                crossover_score += 0.1
                reasons.append("Fresh EMA 9/21 Bullish Crossover")
            else:
                reasons.append("EMA 9 > EMA 21 (bullish trend)")
        elif ema_9_curr < ema_21_curr:
            crossover_score = -0.2
            if ema_9_prev >= ema_21_prev:
                crossover_score -= 0.1
                reasons.append("Fresh EMA 9/21 Bearish Crossover")
            else:
                reasons.append("EMA 9 < EMA 21 (bearish trend)")

        # --- 5. VWAP Position ---
        vwap_score = 0.0
        if price > vwap:
            vwap_score = 0.15
            reasons.append(f"Price above VWAP (₹{price:.2f} > ₹{vwap:.2f})")
        elif price < vwap:
            vwap_score = -0.15
            reasons.append(f"Price below VWAP (₹{price:.2f} < ₹{vwap:.2f})")

        # --- DYNAMIC REGIME WEIGHTING ---
        if regime == "RANGEBOUND":
            # Amplify mean-reversion (RSI + Bollinger) and suppress trend-following (MACD, Crossover, VWAP)
            score = (rsi_score * 1.5) + (bb_score * 1.5) + (macd_score * 0.2) + (vwap_score * 0.1)
            reasons.append("Regime: Rangebound (Mean Reversion amplified)")
            if price < bollinger_lower or price > bollinger_upper:
                reasons.append("Bollinger Band boundary trigger")
                
        elif regime in ("TRENDING_UP", "TRENDING_DOWN"):
            # Amplify trend-following (MACD, Crossover, VWAP) and ignore counter-trend mean reversion (RSI / Bollinger)
            score = (macd_score * 1.2) + (crossover_score * 1.5) + (vwap_score * 1.2)
            
            # Damp counter-trend RSI signals
            if regime == "TRENDING_UP":
                if rsi_score < 0:
                    logger.debug("SignalAgent | Dampened overbought RSI sell signal during TRENDING_UP")
                if rsi_score > 0:
                    score += rsi_score * 0.5
            elif regime == "TRENDING_DOWN":
                if rsi_score > 0:
                    logger.debug("SignalAgent | Dampened oversold RSI buy signal during TRENDING_DOWN")
                if rsi_score < 0:
                    score += rsi_score * 0.5
                    
            reasons.append(f"Regime: Trend Following ({regime.replace('_', ' ')})")
            if hist > 0 and macd_val > signal_val:
                reasons.append("MACD Bullish crossover")
            elif hist < 0 and macd_val < signal_val:
                reasons.append("MACD Bearish crossover")
                
        elif regime == "HIGH_VOLATILITY":
            # Damp all signals across the board to remain conservative
            score = ((rsi_score * 0.4) + (macd_score * 0.4) + (bb_score * 0.4) + (crossover_score * 0.4)) * 0.5
            reasons.append("Regime: High Volatility (Signals dampened by 50%)")
            
        else:
            # Fallback to standard baseline scoring
            score = rsi_score + macd_score + bb_score + crossover_score + vwap_score
            reasons.append("Regime: Unknown (Baseline indicator scoring applied)")

        # --- 6. Multi-timeframe trend confirmation ---
        try:
            df_1m = pd.DataFrame(ctx.ohlcv_1m)
            df_1m["timestamp"] = pd.to_datetime(df_1m["timestamp"])
            df_1m.set_index("timestamp", inplace=True)
            
            df_5m = df_1m.resample("5Min").agg({"close": "last"}).dropna()
            df_15m = df_1m.resample("15Min").agg({"close": "last"}).dropna()
            
            trend_5m = 0
            trend_15m = 0
            
            if len(df_5m) >= 21:
                ema9_5m = df_5m["close"].ewm(span=9, adjust=False).mean().iloc[-1]
                ema21_5m = df_5m["close"].ewm(span=21, adjust=False).mean().iloc[-1]
                trend_5m = 1 if ema9_5m > ema21_5m else -1
                
            if len(df_15m) >= 21:
                ema9_15m = df_15m["close"].ewm(span=9, adjust=False).mean().iloc[-1]
                ema21_15m = df_15m["close"].ewm(span=21, adjust=False).mean().iloc[-1]
                trend_15m = 1 if ema9_15m > ema21_15m else -1
                
            if score > 0.1:
                if trend_5m == -1 or trend_15m == -1:
                    score *= 0.6
                    reasons.append("MTF warning: 5m or 15m trend is Bearish")
            elif score < -0.1:
                if trend_5m == 1 or trend_15m == 1:
                    score *= 0.6
                    reasons.append("MTF warning: 5m or 15m trend is Bullish")
        except Exception as mtf_err:
            logger.warning(f"Failed to calculate MTF confirmation: {mtf_err}")

        # --- Volume Confirmation ---
        if volume_spike:
            if score > 0:
                score *= 1.25
                reasons.append(f"Volume spike confirmed Buy pressure (+25%)")
            elif score < 0:
                score *= 1.25
                reasons.append(f"Volume spike confirmed Sell pressure (+25%)")

        # --- Bid/Ask Spread confirmation ---
        if spread_pct > 0.005:
            score *= 0.5
            reasons.append(f"Wide bid/ask spread ({spread_pct*100:.2f}%) — signal score halved")

        # --- Volatility filter ---
        if ctx.volatility_24h > 0.05:
            score *= 0.7
            reasons.append(f"Elevated volatility ({ctx.volatility_24h*100:.1f}%) — signal dampened")

        # --------------------------------------------------------
        # CONVERT SCORE TO DECISION
        # --------------------------------------------------------
        if score > 0.2:
            decision   = "BUY"
            confidence = min(0.5 + score, 0.95)
        elif score < -0.2:
            decision   = "SELL"
            confidence = min(0.5 + abs(score), 0.95)
        else:
            decision   = "HOLD"
            confidence = 0.5 + abs(score)

        # Check if there is an active open position in the portfolio for this symbol
        portfolio = state.get("portfolio")
        open_positions = portfolio.open_positions if portfolio and hasattr(portfolio, "open_positions") else []
        active_position = None
        for pos in open_positions:
            if pos.get("symbol") == symbol:
                active_position = pos
                break

        if active_position and decision == "HOLD":
            qty = active_position.get("quantity", 0)
            if qty > 0:  # We are LONG
                if ema_9_curr < ema_21_curr or hist < 0 or score <= 0.0:
                    decision = "CLOSE"
                    reasons.append("SignalAgent: Trend weakened for LONG position - recommending CLOSE")
            elif qty < 0:  # We are SHORT
                if ema_9_curr > ema_21_curr or hist > 0 or score >= 0.0:
                    decision = "CLOSE"
                    reasons.append("SignalAgent: Trend weakened for SHORT position - recommending CLOSE")

        reasoning = " | ".join(reasons) if reasons else "No strong signals detected"

        # --------------------------------------------------------
        # BUILD VOTE
        # --------------------------------------------------------
        vote = AgentVote(
            agent     = "SignalAgent",
            decision  = decision,
            confidence= round(confidence, 3),
            reasoning = reasoning,
            metadata  = {
                "rsi":              rsi,
                "macd":             macd_val,
                "macd_signal":      signal_val,
                "macd_hist":        hist,
                "bollinger_upper":  bollinger_upper,
                "bollinger_middle": bollinger_middle,
                "bollinger_lower":  bollinger_lower,
                "ema_9":            ema_9,
                "ema_21":           ema_21,
                "vwap":             vwap,
                "atr":              atr,
                "final_score":      round(score, 4),
                "volatility_24h":   ctx.volatility_24h,
                "indicators_cached": from_cache,
                "volume_spike":     volume_spike,
                "live_price":       live_price,
                "spread_pct":       round(spread_pct, 6),
            },
        )

        logger.info(
            f"📡 SignalAgent | {symbol} | {decision} | "
            f"conf={confidence:.2f} | score={score:.3f} | "
            f"cached={from_cache}"
        )

        return {
            "signal_vote":     vote,
            "completed_nodes": ["signal_agent"],
            "logs":            [f"SignalAgent generated {decision} for {symbol}"],
        }

    except Exception as e:
        logger.exception(f"SignalAgent failure: {e}")

        return {
            "signal_vote": AgentVote(
                agent     = "SignalAgent",
                decision  = "HOLD",
                confidence= 0.1,
                reasoning = f"SignalAgent error: {e}",
            ),
            "completed_nodes": ["signal_agent"],
            "logs":            [f"SignalAgent failed: {e}"],
        }