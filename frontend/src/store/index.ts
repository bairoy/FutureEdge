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
import type { UserProfile, AgentVote, EpisodicMemory, KillSwitchStatus } from "@/types";

// ─── TYPES ───────────────────────────────────────────────────

// One candlestick point — format required by lightweight-charts
export interface TickPoint {
  time: number; // Unix timestamp in seconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

// Data shown in the HITL approval modal
export interface HITLPending {
  threadId: string;
  symbol: string;
  direction: string;
  size: number;
  entryPrice: number;
  riskScore: number;
  llmRationale: string | null;
  reasons: string[];
  memories: EpisodicMemory[];
  votes: AgentVote[];
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

  addTick: (tick: TickPoint) => void;
  setTicks: (ticks: TickPoint[]) => void;
  clearTicks: () => void;
  setAgentResult: (votes: AgentVote[], direction: string, risk: number, rationale: string | null) => void;
  setHITLPending: (h: HITLPending | null) => void;
  setKillSwitch: (s: KillSwitchStatus) => void;
  setCurrentSymbol: (s: string) => void;
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

  // Keep only last 500 ticks so memory stays bounded
  addTick: (tick) =>
    set((s) => ({ ticks: [...s.ticks.slice(-499), tick] })),

  setTicks: (ticks) => set({ ticks: ticks.slice(-500) }),

  clearTicks: () => set({ ticks: [] }),

  setAgentResult: (latestVotes, latestDirection, latestRiskScore, llmRationale) =>
    set({ latestVotes, latestDirection, latestRiskScore, llmRationale }),

  setHITLPending: (hitlPending) => set({ hitlPending }),
  setKillSwitch: (killSwitch) => set({ killSwitch }),
  setCurrentSymbol: (currentSymbol) => set({ currentSymbol }),
}));