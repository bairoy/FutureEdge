/**
 * frontend/src/components/trading/HITLModal.tsx
 *
 * Human-in-the-Loop (HITL) approval modal.
 *
 * WHEN IT APPEARS:
 * ─────────────────────────────────────────────────────────
 * The backend pauses a workflow and publishes a "hitl_pending"
 * WebSocket message. useWebSocket dispatches this to the store.
 * The dashboard layout renders this modal when hitlPending !== null.
 * The modal stays open until the risk manager makes a decision —
 * clicking outside does NOT close it (deliberate safety design).
 *
 * WHAT IT SHOWS:
 * ─────────────────────────────────────────────────────────
 * 1. Trade details     — symbol, direction, size, risk score
 * 2. HITL reasons      — why the orchestrator triggered review
 * 3. LLM rationale     — Claude's plain-English explanation (Phase 2)
 * 4. Episodic memories — similar past trades from Qdrant (Phase 2)
 * 5. Agent votes       — all 4 agents with confidence bars
 * 6. Notes input       — optional reviewer comments
 * 7. APPROVE / REJECT  — calls POST /api/v1/workflow/resume
 *
 * ACCESS:
 * ─────────────────────────────────────────────────────────
 * Viewers and traders can SEE the modal (read-only).
 * Only risk_manager and admin can click Approve/Reject.
 *
 * WHAT HAPPENS AFTER:
 * ─────────────────────────────────────────────────────────
 * APPROVE → backend resumes workflow at the interrupt() point
 *           → execution_agent places the real order
 * REJECT  → backend marks trade as rejected, no order placed
 */

"use client";

import { useState } from "react";
import { AlertTriangle, TrendingUp, TrendingDown, CheckCircle, XCircle, Brain, History, ChevronRight, X } from "lucide-react";
import { useTradingStore, useAuthStore } from "@/store";
import { showToast } from "@/components/ui/Toast";
import api from "@/lib/api";
import type { AgentVote, EpisodicMemory } from "@/types";

export function HITLModal() {
  const { hitlPending, setHITLPending } = useTradingStore();
  const { canApproveHITL, canTrade } = useAuthStore();

  const [notes, setNotes] = useState("");
  const [quantity, setQuantity] = useState<number | "">(
    hitlPending && hitlPending.entryPrice > 0
      ? Math.max(1, Math.floor(hitlPending.size / hitlPending.entryPrice))
      : 1
  );
  const [submitting, setSubmitting] = useState(false);
  const [active, setActive] = useState<"APPROVE" | "REJECT" | null>(null);

  if (!hitlPending) return null;

  const { threadId, symbol, direction, size, entryPrice, riskScore,
    stopLoss, takeProfit, llmRationale, reasons, memories, votes, hitlRequired } = hitlPending;

  const isRisky = hitlRequired ?? (reasons && reasons.length > 0);
  const allowedToApprove = isRisky ? canApproveHITL() : canTrade();

  async function submit(decision: "APPROVE" | "REJECT") {
    if (!allowedToApprove) return;
    if (decision === "APPROVE" && (!quantity || Number(quantity) <= 0)) {
      showToast("Order quantity is required to approve the trade", "error");
      return;
    }

    setActive(decision);
    setSubmitting(true);

    try {
      await api.post("/api/v1/workflow/resume", {
        thread_id: threadId,
        decision,
        notes,
        quantity: decision === "APPROVE" ? Number(quantity) : undefined,
      });
      showToast(
        decision === "APPROVE" 
          ? (isRisky ? "Trade approved — executing order" : "Trade submitted — executing order")
          : (isRisky ? "Trade rejected" : "Trade cancelled"),
        decision === "APPROVE" ? "success" : "info"
      );
      setHITLPending(null);
    } catch (err: any) {
      showToast(err?.response?.data?.detail ?? "Failed to submit decision", "error");
      setActive(null);
    } finally {
      setSubmitting(false);
    }
  }

  const isLong = direction === "LONG";
  const DirIcon = isLong ? TrendingUp : TrendingDown;
  const dirColor = isLong ? "text-green-400" : "text-red-400";

  const riskColor =
    riskScore > 0.8 ? "text-red-400" :
      riskScore > 0.6 ? "text-amber-400" :
        "text-green-400";

  return (
    // Full-screen overlay — no click-outside-to-close by design
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/75 backdrop-blur-sm" />

      <div className="relative z-10 w-full max-w-2xl max-h-[90vh] overflow-y-auto
                      card border border-amber-500/30 shadow-2xl animate-slide-up">

        {/* ── Header ───────────────────────────────────────────── */}
        <div className="flex items-center justify-between p-5 border-b border-gray-800">
          <div className="flex items-center gap-3">
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${isRisky ? "bg-amber-500/10" : "bg-blue-500/10"}`}>
              <AlertTriangle className={`w-4 h-4 ${isRisky ? "text-amber-400" : "text-blue-400"}`} />
            </div>
            <div>
              <h2 className="font-bold text-gray-100">{isRisky ? "Trade Requires Approval" : "Confirm Trade Execution"}</h2>
              <p className="text-xs text-gray-500 mt-0.5">{isRisky ? "Human-in-the-Loop review before execution" : "Verify details and select quantity to execute"}</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {!allowedToApprove && (
              <span className="text-xs text-gray-500">
                Read only
              </span>
            )}
            <button onClick={() => setHITLPending(null)} className="p-1 rounded text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors" title="Close">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        <div className="p-5 space-y-4">

          {/* ── Trade details ────────────────────────────────────── */}
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-3">
            {[
              { label: "Symbol", value: symbol, cls: "text-gray-100 font-mono" },
              { label: "Direction", value: direction, cls: dirColor },
              { label: "Entry Price", value: `₹${entryPrice.toLocaleString("en-IN", { minimumFractionDigits: 2 })}`, cls: "text-gray-100 font-mono" },
              { label: "Stop Loss", value: stopLoss ? `₹${stopLoss.toLocaleString("en-IN", { minimumFractionDigits: 2 })}` : "N/A", cls: "text-red-400 font-mono font-semibold" },
              { label: "Target", value: takeProfit ? `₹${takeProfit.toLocaleString("en-IN", { minimumFractionDigits: 2 })}` : "N/A", cls: "text-green-400 font-mono font-semibold" },
              { label: "Risk Score", value: riskScore.toFixed(2), cls: riskColor },
            ].map(({ label, value, cls }) => (
              <div key={label} className="bg-gray-800/50 rounded-lg p-3">
                <p className="section-label mb-1 text-[10px] uppercase text-gray-500 font-semibold">{label}</p>
                <div className="flex items-center gap-1.5 mt-0.5">
                  {label === "Direction" && <DirIcon className={`w-3.5 h-3.5 ${dirColor}`} />}
                  <span className={`font-bold text-xs sm:text-sm ${cls}`}>{value}</span>
                </div>
              </div>
            ))}
          </div>

          {/* ── HITL reasons ─────────────────────────────────────── */}
          {reasons.length > 0 && (
            <div className="bg-amber-500/5 border border-amber-500/20 rounded-lg p-4">
              <p className="section-label mb-2">Why approval is required</p>
              <ul className="space-y-1">
                {reasons.map((r, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-amber-300">
                    <ChevronRight className="w-4 h-4 shrink-0 mt-0.5" />{r}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* ── LLM Rationale (Phase 2) ──────────────────────────── */}
          {llmRationale && (
            <div className="bg-blue-500/5 border border-blue-500/20 rounded-lg p-4">
              <div className="flex items-center gap-2 mb-2">
                <Brain className="w-4 h-4 text-blue-400" />
                <p className="section-label">AI Rationale (Claude)</p>
              </div>
              <p className="text-sm text-gray-300 leading-relaxed">{llmRationale}</p>
            </div>
          )}

          {/* ── Episodic memories (Phase 2) ──────────────────────── */}
          {memories.length > 0 && (
            <div className="bg-gray-800/30 border border-gray-700/50 rounded-lg p-4">
              <div className="flex items-center gap-2 mb-3">
                <History className="w-4 h-4 text-gray-400" />
                <p className="section-label">Similar Past Trades (Qdrant)</p>
              </div>
              {memories.map((m: EpisodicMemory, i) => (
                <div key={i} className="flex items-center justify-between text-xs py-1 border-b border-gray-700/30 last:border-0">
                  <span className="text-gray-400">{m.symbol} {m.direction} — {m.regime}</span>
                  <div className="flex items-center gap-3">
                    <span className={m.outcome === "WIN" ? "text-green-400" : m.outcome === "LOSS" ? "text-red-400" : "text-gray-500"}>
                      {m.outcome} ({m.pnl_pct > 0 ? "+" : ""}{m.pnl_pct.toFixed(1)}%)
                    </span>
                    <span className="text-gray-600">sim={m.similarity.toFixed(2)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* ── Agent votes ──────────────────────────────────────── */}
          <div>
            <p className="section-label mb-3">Agent Votes</p>
            <div className="grid grid-cols-2 gap-2">
              {votes.map((v: AgentVote) => (
                <div key={v.agent} className="bg-gray-800/40 rounded-lg p-3">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-xs text-gray-400">{v.agent.replace("Agent", "")}</span>
                    <span className={`text-xs font-bold ${v.decision === "BUY" ? "text-green-400" :
                        v.decision === "SELL" ? "text-red-400" :
                          v.decision === "VETO" ? "text-gray-400" : "text-amber-400"
                      }`}>{v.decision}</span>
                  </div>
                  <div className="h-1 bg-gray-700 rounded-full overflow-hidden mb-2">
                    <div className="h-full bg-blue-500 rounded-full"
                      style={{ width: `${v.confidence * 100}%` }} />
                  </div>
                  <p className="text-xs text-gray-600 line-clamp-2">{v.reasoning}</p>
                </div>
              ))}
            </div>
          </div>

          {/* ── Quantity Selection ────────────────────────────────── */}
          {allowedToApprove && (
            <div className="bg-gray-850/50 border border-gray-700/50 rounded-lg p-4 space-y-3">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                  <label className="block text-sm font-semibold text-gray-200">
                    Order Quantity (Shares) <span className="text-red-400">*</span>
                  </label>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Select how many shares to buy. Whole numbers only.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min="1"
                    step="1"
                    placeholder="Qty"
                    value={quantity}
                    onChange={(e) => {
                      const val = e.target.value;
                      if (val === "") {
                        setQuantity("");
                      } else {
                        const parsed = parseInt(val, 10);
                        setQuantity(isNaN(parsed) ? "" : Math.max(1, parsed));
                      }
                    }}
                    className="input w-36 font-mono text-center font-bold text-lg bg-black border-amber-500/30 text-amber-400 focus:border-amber-400 focus:ring-1 focus:ring-amber-400"
                  />
                  <span className="text-xs text-gray-400">shares</span>
                </div>
              </div>
              {quantity && entryPrice ? (
                <div className="text-right text-xs text-gray-400">
                  Estimated Trade Value:{" "}
                  <span className="font-bold text-gray-200 font-mono">
                    ₹{(quantity * entryPrice).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                  </span>
                </div>
              ) : null}
            </div>
          )}

          {/* ── Notes ────────────────────────────────────────────── */}
          {allowedToApprove && (
            <div>
              <label className="block text-sm text-gray-400 mb-1.5">Reviewer notes (optional)</label>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="e.g. Risk acceptable given current regime…"
                rows={2}
                className="input resize-none"
              />
            </div>
          )}

          {/* ── Actions ──────────────────────────────────────────── */}
          {allowedToApprove ? (
            <div className="flex gap-3 pt-1">
              <button onClick={() => setHITLPending(null)} disabled={submitting}
                className="px-4 py-2 bg-gray-800 text-gray-300 font-medium rounded-lg hover:bg-gray-700 hover:text-gray-100 transition-colors border border-gray-700">
                Cancel
              </button>
              <button onClick={() => submit("REJECT")} disabled={submitting}
                className="btn-danger flex items-center gap-2 flex-1 justify-center">
                <XCircle className="w-4 h-4" />
                {submitting && active === "REJECT" ? "Rejecting…" : isRisky ? "Reject Trade" : "Cancel Trade"}
              </button>
              <button onClick={() => submit("APPROVE")} disabled={submitting || !quantity || Number(quantity) <= 0}
                className="btn-success flex items-center gap-2 flex-1 justify-center disabled:opacity-50 disabled:cursor-not-allowed">
                <CheckCircle className="w-4 h-4" />
                {submitting && active === "APPROVE" ? "Approving…" : isRisky ? "Approve Trade" : "Execute Trade"}
              </button>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-3 pt-1">
              <p className="text-center text-sm text-gray-500">
                {isRisky ? (
                  <>Only <span className="text-blue-400">risk_manager</span> or <span className="text-purple-400">admin</span> can approve or reject trades.</>
                ) : (
                  <>Only authorized <span className="text-blue-400">traders</span> can execute trades.</>
                )}
              </p>
              <button onClick={() => setHITLPending(null)}
                className="px-4 py-2 bg-gray-800 text-gray-300 font-medium rounded-lg hover:bg-gray-700 hover:text-gray-100 transition-colors border border-gray-700 w-full max-w-xs">
                Close
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}