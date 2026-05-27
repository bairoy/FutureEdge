/**
 * frontend/src/app/dashboard/page.tsx
 *
 * Main trading dashboard — the primary screen for all users.
 *
 * LAYOUT (top to bottom):
 * ─────────────────────────────────────────────────────────
 * 1. Stats row      — last price, signal direction, risk score, market status
 * 2. Run button     — triggers a new agent cycle (trader+ only)
 * 3. Charts row     — live candlestick (left) + cumulative PnL (right)
 * 4. Agent votes    — 4 cards updated in real-time via WebSocket
 * 5. LLM rationale  — Claude's plain-English trade explanation (Phase 2)
 *
 * DATA SOURCES:
 * ─────────────────────────────────────────────────────────
 * • Live ticks, votes, rationale → Zustand store (fed by WebSocket)
 * • Trade history for PnL chart  → GET /api/v1/trades (SWR, 30s refresh)
 * • Kill switch state            → Zustand store (synced on mount)
 */

"use client";

import { useState, useEffect } from "react";
import useSWR from "swr";
import { TrendingUp, TrendingDown, Minus, AlertTriangle, Play, RefreshCw, Brain } from "lucide-react";

import { useTradingStore, useAuthStore, type TickPoint } from "@/store";
import { PriceChart } from "@/components/charts/PriceChart";
import { PnLChart } from "@/components/charts/PnLChart";
import { AgentVoteCard } from "@/components/dashboard/AgentVoteCard";
import { SymbolSearch } from "@/components/dashboard/SymbolSearch";
import { StatCard } from "@/components/dashboard/StatCard";
import { showToast } from "@/components/ui/Toast";
import api from "@/lib/api";
import type { WorkflowRunResponse, Trade } from "@/types";

const fetcher = (url: string) => api.get(url).then((r) => r.data);

export default function DashboardPage() {
  const {
    ticks, latestVotes, latestDirection,
    latestRiskScore, llmRationale, killSwitch, currentSymbol,
    setAgentResult, setHITLPending
  } = useTradingStore();

  const { canTrade } = useAuthStore();

  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<WorkflowRunResponse | null>(null);

  // Fetch history when symbol changes
  const { setTicks } = useTradingStore();
  useEffect(() => {
    if (!currentSymbol) return;
    (async () => {
      try {
        const { data } = await api.get<TickPoint[]>(`/api/v1/market/history/${encodeURIComponent(currentSymbol)}`);
        if (data && data.length > 0) {
          setTicks(data);
        }
      } catch (err) {
        console.error("[Dashboard] Failed to seed history:", err);
      }
    })();
  }, [currentSymbol, setTicks]);

  // Trade history for the PnL chart — refreshes every 30 seconds
  const { data: trades } = useSWR<Trade[]>(
    "/api/v1/trades",
    fetcher,
    { refreshInterval: 30_000 }
  );

  // ── Run a new agent cycle ──────────────────────────────────
  async function handleRun() {
    setRunning(true);
    setRunResult(null);

    try {
      const { data } = await api.post<WorkflowRunResponse>("/api/v1/workflow/run", {
        symbol: currentSymbol,
        use_live_data: true,
      });

      setRunResult(data);

      // Immediately sync with store so charts and agent cards update
      if (data.votes) {
        setAgentResult(data.votes, data.direction, data.risk_score, null);
      }

      if (data.hitl_status === "PENDING") {
        showToast("Workflow paused — awaiting HITL approval", "info");
        // Trigger the modal if all data is present
        if (data.proposal && data.votes) {
          setHITLPending({
            threadId: data.thread_id,
            symbol: data.proposal.symbol || currentSymbol,
            direction: data.proposal.direction,
            size: data.proposal.size,
            entryPrice: data.proposal.entry_price,
            riskScore: data.proposal.risk_score,
            llmRationale: null,
            reasons: data.reasons || [],
            memories: [],
            votes: data.votes,
          });
        }
      } else if (data.execution_error) {
        showToast(`Error: ${data.execution_error}`, "error");
      } else {
        showToast(`Cycle complete — ${data.direction}`, "success");
      }
    } catch (err: any) {
      showToast(err?.response?.data?.detail ?? "Workflow failed", "error");
    } finally {
      setRunning(false);
    }
  }

  // ── Helpers ───────────────────────────────────────────────
  const lastPrice = ticks.length > 0 ? ticks[ticks.length - 1].close : null;

  const dirCfg = {
    LONG: { color: "text-green-400", Icon: TrendingUp, label: "LONG" },
    SHORT: { color: "text-red-400", Icon: TrendingDown, label: "SHORT" },
    NONE: { color: "text-gray-400", Icon: Minus, label: "HOLD" },
  };

  const riskClass =
    latestRiskScore == null && runResult?.risk_score == null ? "text-gray-400" :
      (latestRiskScore ?? runResult?.risk_score ?? 0) > 0.7 ? "text-red-400" :
        (latestRiskScore ?? runResult?.risk_score ?? 0) > 0.4 ? "text-amber-400" :
          "text-green-400";

  const effectiveDirection = latestDirection || runResult?.direction || "NONE";
  const effectiveRisk = latestRiskScore ?? runResult?.risk_score ?? null;

  const dir = dirCfg[effectiveDirection as keyof typeof dirCfg] ?? dirCfg.NONE;

  return (
    <div className="space-y-5">

      {/* ── Page header ─────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <SymbolSearch />

        {canTrade() && (
          <button
            onClick={handleRun}
            disabled={running || killSwitch.halted}
            className="btn-primary flex items-center gap-2 text-sm"
          >
            {running
              ? <><RefreshCw className="w-3.5 h-3.5 animate-spin" />Running…</>
              : <><Play className="w-3.5 h-3.5" />Run Agent Cycle</>
            }
          </button>
        )}
      </div>

      {/* ── Run result banner ───────────────────────────────── */}
      {runResult && (
        <div className={`p-3.5 rounded-xl border text-sm animate-slide-up flex flex-wrap items-center justify-between gap-3
          ${runResult.hitl_status === "PENDING"
            ? "bg-amber-500/10 border-amber-500/30 text-amber-300"
            : "bg-green-500/10 border-green-500/30 text-green-300"
          }`}>
          <div className="flex items-center gap-2 font-medium">
            <AlertTriangle className="w-4 h-4" />
            <div>
              {runResult.hitl_status === "PENDING"
                ? "⏸ Paused — awaiting HITL approval"
                : `✓ ${runResult.direction} | risk=${runResult.risk_score?.toFixed(2)}`
              }
              <p className="text-[10px] opacity-60 mt-0.5 uppercase tracking-wider">thread: {runResult.thread_id}</p>
            </div>
          </div>

          {runResult.hitl_status === "PENDING" && (
            <button
              onClick={() => {
                if (runResult.proposal && runResult.votes) {
                  setHITLPending({
                    threadId: runResult.thread_id,
                    symbol: runResult.proposal.symbol || currentSymbol,
                    direction: runResult.proposal.direction,
                    size: runResult.proposal.size,
                    entryPrice: runResult.proposal.entry_price,
                    riskScore: runResult.proposal.risk_score,
                    llmRationale: null,
                    reasons: runResult.reasons || [],
                    memories: [],
                    votes: runResult.votes,
                  });
                }
              }}
              className="px-3 py-1.5 bg-amber-500 text-black text-xs font-bold rounded-lg hover:bg-amber-400 transition-colors shadow-lg"
            >
              Review & Approve
            </button>
          )}
        </div>
      )}

      {/* ── Stats row ───────────────────────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard
          label="Last Price"
          value={lastPrice ? `₹${lastPrice.toLocaleString("en-IN")}` : "—"}
          sub={currentSymbol}
        />
        <StatCard
          label="Signal"
          value={dir.label}
          valueClass={dir.color}
          icon={<dir.Icon className={`w-5 h-5 ${dir.color}`} />}
        />
        <StatCard
          label="Risk Score"
          value={effectiveRisk != null ? effectiveRisk.toFixed(2) : "—"}
          valueClass={riskClass}
          sub={effectiveRisk != null
            ? effectiveRisk > 0.7 ? "High risk" : "Acceptable"
            : "No data"}
        />
        <StatCard
          label="Market"
          value={killSwitch.halted ? "HALTED" : "ACTIVE"}
          valueClass={killSwitch.halted ? "text-red-400" : "text-green-400"}
          sub={killSwitch.halted ? "Kill switch on" : "Trading enabled"}
        />
      </div>

      {/* ── Charts ──────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">

        {/* Candlestick chart — takes 2 of 3 columns */}
        <div className="lg:col-span-2 card p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold text-gray-100 text-sm">Live Price</h2>
            <span className="text-xs text-gray-600">{ticks.length} ticks via WebSocket</span>
          </div>
          <PriceChart ticks={ticks} height={280} />
        </div>

        {/* PnL chart — takes 1 of 3 columns */}
        <div className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold text-gray-100 text-sm">Cumulative PnL</h2>
            <span className="text-xs text-gray-600">
              {trades?.filter((t) => t.realized_pnl != null).length ?? 0} closed
            </span>
          </div>
          <PnLChart trades={trades ?? []} height={280} />
        </div>
      </div>

      {/* ── Agent votes ─────────────────────────────────────── */}
      <div>
        <h2 className="font-semibold text-gray-100 text-sm mb-3">Agent Votes</h2>
        {latestVotes.length === 0 ? (
          <div className="card p-8 text-center text-gray-500 text-sm">
            No votes yet — run an agent cycle to see results
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {latestVotes.map((v: any) => <AgentVoteCard key={v.agent} vote={v} />)}
          </div>
        )}
      </div>

      {/* ── LLM rationale (Phase 2) ─────────────────────────── */}
      {llmRationale && (
        <div className="card p-4">
          <div className="flex items-center gap-2 mb-2">
            <Brain className="w-4 h-4 text-blue-400" />
            <h2 className="font-semibold text-gray-100 text-sm">AI Rationale</h2>
            <span className="text-xs text-gray-600">Claude</span>
          </div>
          <p className="text-sm text-gray-300 leading-relaxed">{llmRationale}</p>
        </div>
      )}
    </div>
  );
}