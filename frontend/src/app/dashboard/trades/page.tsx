/**
 * frontend/src/app/dashboard/trades/page.tsx
 *
 * Trade history page — permanent audit trail from PostgreSQL.
 *
 * DATA SOURCE: GET /api/v1/trades
 * Returns the current user's trades. Admin can see all via ?all=true.
 *
 * FEATURES:
 * ─────────────────────────────────────────────────────────
 * • Summary row    — total trades, realized PnL, win rate
 * • Sortable table — symbol, direction, size, price, PnL, status
 * • HITL column    — shows whether human approved/rejected
 * • Expandable row — click arrow to see all 4 agent votes + notes
 * • Auto-refresh   — SWR re-fetches every 30 seconds
 */

"use client";

import { useState } from "react";
import useSWR from "swr";
import { format } from "date-fns";
import {
  ChevronDown, ChevronUp, CheckCircle,
  XCircle, Clock, RefreshCw,
} from "lucide-react";
import api from "@/lib/api";
import type { Trade, AgentVote } from "@/types";

const fetcher = (url: string) => api.get(url).then((r) => r.data);

export default function TradesPage() {
  const { data: trades, isLoading, mutate } = useSWR<Trade[]>(
    "/api/v1/trades",
    fetcher,
    { refreshInterval: 30_000 }
  );

  // Track which rows are expanded to show agent votes
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  // ── Summary stats ─────────────────────────────────────────
  const closed = trades?.filter((t) => t.realized_pnl != null) ?? [];
  const totalPnL = closed.reduce((s, t) => s + (t.realized_pnl ?? 0), 0);
  const winners = closed.filter((t) => (t.realized_pnl ?? 0) > 0).length;

  return (
    <div className="space-y-5">

      {/* ── Header ──────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-gray-100">Trade History</h1>
          <p className="text-xs text-gray-500 mt-0.5">
            All trades stored in PostgreSQL — permanent audit trail
          </p>
        </div>
        <button onClick={() => mutate()} className="btn-ghost flex items-center gap-2 text-sm">
          <RefreshCw className="w-4 h-4" />Refresh
        </button>
      </div>

      {/* ── Summary ─────────────────────────────────────────── */}
      {trades && trades.length > 0 && (
        <div className="grid grid-cols-3 gap-4">
          <div className="card p-4">
            <p className="section-label mb-1">Total Trades</p>
            <p className="text-2xl font-bold text-gray-100">{trades.length}</p>
          </div>
          <div className="card p-4">
            <p className="section-label mb-1">Realized PnL</p>
            <p className={`text-2xl font-bold ${totalPnL >= 0 ? "text-green-400" : "text-red-400"}`}>
              {totalPnL >= 0 ? "+" : ""}₹{totalPnL.toLocaleString("en-IN", { maximumFractionDigits: 2 })}
            </p>
          </div>
          <div className="card p-4">
            <p className="section-label mb-1">Win Rate</p>
            <p className="text-2xl font-bold text-gray-100">
              {closed.length > 0
                ? `${Math.round((winners / closed.length) * 100)}%`
                : "—"}
            </p>
            <p className="text-xs text-gray-500 mt-0.5">{winners}/{closed.length} trades</p>
          </div>
        </div>
      )}

      {/* ── Table ───────────────────────────────────────────── */}
      <div className="card overflow-hidden">
        {isLoading ? (
          <div className="p-10 text-center text-gray-500 text-sm">Loading trades…</div>
        ) : !trades || trades.length === 0 ? (
          <div className="p-10 text-center text-gray-500 text-sm">
            No trades yet. Run an agent cycle to see results here.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                {["Symbol", "Direction", "Shares", "Entry ₹", "PnL", "Status", "HITL", "Date", ""].map((h) => (
                  <th key={h} className="px-4 py-3 text-left section-label whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {trades.map((trade) => {
                const isOpen = expanded.has(trade.id);

                return (
                  <>
                    {/* Main row */}
                    <tr key={trade.id} className="hover:bg-gray-800/20 transition-colors">
                      <td className="px-4 py-3 font-medium text-gray-100">{trade.symbol}</td>

                      <td className="px-4 py-3">
                        <span className={
                          trade.direction === "LONG" ? "badge-bull" :
                            trade.direction === "SHORT" ? "badge-bear" : "badge-hold"
                        }>
                          {trade.direction}
                        </span>
                      </td>

                      <td className="px-4 py-3 text-gray-300">{trade.size}</td>

                      <td className="px-4 py-3 text-gray-300">
                        ₹{trade.entry_price.toLocaleString("en-IN")}
                      </td>

                      <td className="px-4 py-3">
                        {trade.realized_pnl != null ? (
                          <span className={trade.realized_pnl >= 0 ? "text-green-400" : "text-red-400"}>
                            {trade.realized_pnl >= 0 ? "+" : ""}₹{trade.realized_pnl.toFixed(2)}
                            {trade.pnl_pct != null && (
                              <span className="text-xs opacity-60 ml-1">({trade.pnl_pct.toFixed(1)}%)</span>
                            )}
                          </span>
                        ) : (
                          <span className="text-gray-600">—</span>
                        )}
                      </td>

                      <td className="px-4 py-3">
                        <span className={
                          trade.status === "OPEN" ? "badge-hold" :
                            trade.status === "CLOSED" ? "badge-bull" : "badge-bear"
                        }>
                          {trade.status}
                        </span>
                      </td>

                      <td className="px-4 py-3">
                        {!trade.hitl_required ? (
                          <span className="text-gray-600 text-xs">—</span>
                        ) : trade.human_approved === true ? (
                          <CheckCircle className="w-4 h-4 text-green-400" />
                        ) : trade.human_approved === false ? (
                          <XCircle className="w-4 h-4 text-red-400" />
                        ) : (
                          <Clock className="w-4 h-4 text-amber-400" />
                        )}
                      </td>

                      <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">
                        {format(new Date(trade.opened_at), "dd MMM, HH:mm")}
                      </td>

                      <td className="px-4 py-3">
                        {trade.agent_consensus && trade.agent_consensus.length > 0 && (
                          <button onClick={() => toggle(trade.id)}
                            className="p-1 rounded hover:bg-gray-700 text-gray-500 hover:text-gray-300 transition-colors">
                            {isOpen
                              ? <ChevronUp className="w-4 h-4" />
                              : <ChevronDown className="w-4 h-4" />
                            }
                          </button>
                        )}
                      </td>
                    </tr>

                    {/* Expanded: agent votes + reviewer notes */}
                    {isOpen && trade.agent_consensus && (
                      <tr key={`${trade.id}-exp`} className="bg-gray-900/60">
                        <td colSpan={9} className="px-4 py-4">
                          <p className="section-label mb-3">Agent Votes</p>
                          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-3">
                            {trade.agent_consensus.map((v: AgentVote) => (
                              <div key={v.agent} className="bg-gray-800 rounded-lg p-3">
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
                                <p className="text-xs text-gray-500 line-clamp-2">{v.reasoning}</p>
                              </div>
                            ))}
                          </div>
                          {trade.human_notes && (
                            <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/20">
                              <span className="text-xs font-medium text-amber-400">Reviewer notes: </span>
                              <span className="text-xs text-amber-300">{trade.human_notes}</span>
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}