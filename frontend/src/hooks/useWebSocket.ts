/**
 * frontend/src/hooks/useWebSocket.ts
 *
 * Manages the WebSocket connection to the backend market stream.
 *
 * WHAT IT DOES:
 * 1. Opens ws://backend/api/v1/market/stream?token=<jwt>
 * 2. Parses every incoming JSON message by type
 * 3. Dispatches to the Zustand trading store
 * 4. Reconnects with exponential backoff on disconnect
 * 5. Closes cleanly on component unmount
 *
 * WHY TOKEN AS QUERY PARAM?
 * WebSocket upgrades cannot send custom headers in browsers.
 * The backend accepts ?token= and validates it before accepting.
 * Close code 4001 = unauthorized (our convention) → redirect to login.
 *
 * CALL THIS ONCE in the dashboard layout — all child components
 * just read from the store, completely decoupled from the WS.
 */

"use client";

import { useEffect, useRef } from "react";
import { tokenStore } from "@/lib/api";
import { useTradingStore, useAuthStore, type HITLPending } from "@/store";
import type { WebSocketMessage } from "@/types";

const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const backoffMs = useRef(1000);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);

  const { currentSymbol, applyTrade, clearTicks, setAgentResult, setHITLPending, setKillSwitch } = useTradingStore();
  const user = useAuthStore((s) => s.user);
  const isLoading = useAuthStore((s) => s.isLoading);

  useEffect(() => {
    if (isLoading || !user) return;

    alive.current = true;
    connect();

    return () => {
      alive.current = false;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close(1000, "unmount");
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, isLoading]);

  // Send subscribe command when currentSymbol changes
  useEffect(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      console.log(`[WS] Switching to ${currentSymbol}`);
      wsRef.current.send(JSON.stringify({ type: "subscribe", symbol: currentSymbol }));
    }
  }, [currentSymbol]);

  function connect() {
    const token = tokenStore.getAccessToken();
    if (!token || !alive.current) return;

    const ws = new WebSocket(`${WS_BASE}/api/v1/market/stream?token=${token}`);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log("[WS] Connected");
      backoffMs.current = 1000; // reset backoff on success
    };

    ws.onmessage = ({ data }) => {
      try { dispatch(JSON.parse(data)); }
      catch { /* ignore malformed frames */ }
    };

    ws.onclose = ({ code }) => {
      if (code === 4001) {
        // Our custom unauthorized code — token invalid, go to login
        if (typeof window !== "undefined") window.location.href = "/login";
        return;
      }
      // Any other close — reconnect with exponential backoff (max 30s)
      if (alive.current) {
        reconnectTimer.current = setTimeout(() => {
          backoffMs.current = Math.min(backoffMs.current * 2, 30_000);
          connect();
        }, backoffMs.current);
      }
    };

    ws.onerror = () => { /* onclose fires after onerror, handled there */ };
  }

  function dispatch(msg: WebSocketMessage) {
    switch (msg.type) {

      case "tick": {
        const d = msg.data;
        // Only `ltp` is per-trade. The open/high/low/close on this payload are
        // Kite's DAY-level OHLC (and `close` is the previous day's close), so
        // they must not be used as candle values — the store folds the LTP
        // into the current minute's candle instead.
        applyTrade(
          d.ltp,
          Math.floor(new Date(d.timestamp).getTime() / 1000), // seconds, not ms
          d.volume,
        );
        break;
      }

      case "agent_result": {
        const d = msg.data;
        setAgentResult(d.votes, d.direction, d.risk_score, d.llm_rationale);
        break;
      }

      case "hitl_pending": {
        const d = msg.data;

        // Resolution notice — close the modal and stamp the outcome on the run
        if (!d || d.status === "RESOLVED") {
          setHITLPending(null);
          const store = useTradingStore.getState();
          if (d && store.runResult && store.runResult.thread_id === d.run_id) {
            store.setRunResult({
              ...store.runResult,
              hitl_status: d.hitl_status,
            });
          }
          break;
        }

        // Proposal awaiting approval. Everything but symbol/reasons lives on
        // `proposal` — reading llm_rationale off the top level (as this used to)
        // always yielded undefined, so the modal showed no rationale.
        const p = d.proposal;
        const pending: HITLPending = {
          threadId: p.run_id,
          symbol: d.symbol,
          direction: p.direction,
          size: p.size,
          entryPrice: p.entry_price,
          riskScore: p.risk_score,
          stopLoss: p.stop_loss ?? undefined,
          takeProfit: p.take_profit ?? undefined,
          llmRationale: p.llm_rationale,
          reasons: d.reasons,
          memories: [], // this channel doesn't carry episodic memories
          votes: p.votes,
          hitlRequired: p.hitl_required,
        };
        setHITLPending(pending);
        break;
      }

      case "kill_switch": {
        const d = msg.data;
        setKillSwitch({ halted: d.halted, status: d.halted ? "HALTED" : "ACTIVE" });
        break;
      }

      case "clear_ticks":
        clearTicks();
        break;

      case "trade":
        // Lightweight notification; full data is on the trades REST endpoint
        console.log("[WS] trade event:", msg.data);
        break;
    }
  }
}