/**
 * frontend/src/store/index.ts
 *
 * Global state management using Zustand.
 *
 * WHY ZUSTAND?
 * No Provider wrappers needed, no boilerplate, tiny bundle size.
 * Components subscribe only to the slice of state they need,
 * so they re-render only when that exact data changes.
 *
 * TWO STORES:
 * ─────────────────────────────────────────────────────────────
 * useAuthStore    — who is logged in, their role, loading state
 * useTradingStore — live market ticks, agent votes, HITL state,
 *                   kill switch status, recent trade events
 */

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { UserProfile, AgentVote, EpisodicMemory, KillSwitchStatus, WorkflowRunResponse } from "@/types";

// ─── TYPES ───────────────────────────────────────────────────

// One candlestick point — format required by lightweight-charts
/**
 * One aggregated OHLC candle, NOT one raw tick.
 *
 * `time` is the start of the candle's minute bucket (Unix seconds, UTC).
 * `close` is the last traded price seen inside that minute, so the newest
 * candle's close is always the current LTP.
 *
 * `volume` carries Kite's cumulative day volume as of the latest tick in the
 * bucket, not per-candle volume — nothing renders it today, and deriving a
 * true per-minute delta would mean tracking the cumulative at each candle
 * open. Revisit if a volume histogram is ever added.
 */
export interface TickPoint {
  time: number; // Unix timestamp in seconds — start of the minute bucket
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** Candle width. NSE/Kite's default intraday granularity is one minute. */
export const CANDLE_SECONDS = 60;

/** Floor a Unix-seconds timestamp to the start of its candle. */
export const candleBucket = (timeSec: number): number =>
  Math.floor(timeSec / CANDLE_SECONDS) * CANDLE_SECONDS;

// Data shown in the HITL approval modal
export interface HITLPending {
  threadId: string;
  symbol: string;
  direction: string;
  size: number;
  entryPrice: number;
  riskScore: number;
  stopLoss?: number;
  takeProfit?: number;
  llmRationale: string | null;
  reasons: string[];
  memories: EpisodicMemory[];
  votes: AgentVote[];
  hitlRequired?: boolean;
}

// ─── AUTH STORE ───────────────────────────────────────────────

interface AuthState {
  user: UserProfile | null;
  isLoading: boolean;
  setUser: (user: UserProfile | null) => void;
  setLoading: (v: boolean) => void;
  logout: () => void;
  // Role helpers — use these instead of checking role strings directly
  canTrade: () => boolean;
  canApproveHITL: () => boolean;
  isAdmin: () => boolean;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      isLoading: true,
      setUser: (user) => set({ user }),
      setLoading: (isLoading) => set({ isLoading }),
      logout: () => set({ user: null }),

      canTrade: () => ["trader", "risk_manager", "admin"].includes(get().user?.role ?? ""),
      canApproveHITL: () => ["risk_manager", "admin"].includes(get().user?.role ?? ""),
      isAdmin: () => get().user?.role === "admin",
    }),
    {
      name: "fe-auth-storage",
      storage: createJSONStorage(() => localStorage),
      // Only persist 'user' - isLoading should always start fresh
      partialize: (state) => ({ user: state.user }),
    }
  )
);

// ─── TRADING STORE ────────────────────────────────────────────

interface TradingState {
  ticks: TickPoint[];      // last 500 price candles
  latestVotes: AgentVote[];
  latestDirection: string | null;
  latestRiskScore: number | null;
  llmRationale: string | null;
  hitlPending: HITLPending | null;
  killSwitch: KillSwitchStatus;
  currentSymbol: string;
  paperTrade: boolean;
  runResult: WorkflowRunResponse | null;

  applyTrade: (ltp: number, timeSec: number, volume?: number) => void;
  setTicks: (ticks: TickPoint[]) => void;
  clearTicks: () => void;
  setAgentResult: (votes: AgentVote[], direction: string, risk: number, rationale: string | null) => void;
  setHITLPending: (h: HITLPending | null) => void;
  setKillSwitch: (s: KillSwitchStatus) => void;
  setCurrentSymbol: (s: string) => void;
  setPaperTrade: (val: boolean) => void;
  setRunResult: (res: WorkflowRunResponse | null) => void;
}

export const useTradingStore = create<TradingState>((set) => ({
  ticks: [],
  latestVotes: [],
  latestDirection: null,
  latestRiskScore: null,
  llmRationale: null,
  hitlPending: null,
  killSwitch: { halted: false, status: "ACTIVE" },
  currentSymbol: "NIFTY 50",
  paperTrade: true, // Default to true
  runResult: null,

  /**
   * Fold one trade into the current minute's candle — the same way Kite
   * builds its intraday chart.
   *
   * Previously each tick was pushed as its own bar, carrying Kite's *day-level*
   * OHLC straight from the websocket payload. Every "candle" therefore spanned
   * the whole day's range and closed at the PREVIOUS day's close, which is what
   * produced the solid block of full-height bars on the right of the chart and
   * a Last Price that never matched the live quote.
   *
   * Only the LTP carries per-trade information, so that is what we aggregate:
   * first trade in a minute opens the candle, subsequent trades stretch the
   * high/low and move the close.
   */
  applyTrade: (ltp, timeSec, volume) =>
    set((s) => {
      if (!Number.isFinite(ltp) || ltp <= 0) return s;

      const bucket = candleBucket(timeSec);
      const last = s.ticks[s.ticks.length - 1];

      // Late tick belonging to an already-closed candle. Dropping it keeps the
      // series monotonic, which lightweight-charts requires.
      if (last && bucket < last.time) return s;

      if (last && bucket === last.time) {
        const updated: TickPoint = {
          ...last,
          high:   Math.max(last.high, ltp),
          low:    Math.min(last.low, ltp),
          close:  ltp,
          volume: volume ?? last.volume,
        };
        return { ticks: [...s.ticks.slice(0, -1), updated] };
      }

      // First trade of a new minute opens a fresh candle.
      const opened: TickPoint = {
        time:   bucket,
        open:   ltp,
        high:   ltp,
        low:    ltp,
        close:  ltp,
        volume: volume ?? 0,
      };
      return { ticks: [...s.ticks.slice(-499), opened] };
    }),

  // Historical bars are already OHLC. Snap their timestamps to the same bucket
  // grid so the first live tick continues the last history candle instead of
  // opening a duplicate one a few seconds later.
  setTicks: (ticks) =>
    set({
      ticks: ticks
        .slice(-500)
        .map((t) => ({ ...t, time: candleBucket(t.time) })),
    }),

  clearTicks: () => set({ ticks: [] }),

  setAgentResult: (latestVotes, latestDirection, latestRiskScore, llmRationale) =>
    set({ latestVotes, latestDirection, latestRiskScore, llmRationale }),

  setHITLPending: (hitlPending) => set({ hitlPending }),
  setKillSwitch: (killSwitch) => set({ killSwitch }),
  setCurrentSymbol: (currentSymbol) => set({ currentSymbol }),
  setPaperTrade: (paperTrade) => set({ paperTrade }),
  setRunResult: (runResult) => set({ runResult }),
}));