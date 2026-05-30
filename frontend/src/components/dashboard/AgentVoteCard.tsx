/**
 * frontend/src/components/dashboard/AgentVoteCard.tsx
 * Shows one agent's vote: decision badge, confidence bar, reasoning text.
 */
"use client";
import { TrendingUp, TrendingDown, Minus, ShieldAlert, Radio, Newspaper, Shield, Briefcase } from "lucide-react";
import type { AgentVote } from "@/types";

const ICONS: Record<string, React.ElementType> = {
  SignalAgent: Radio, SentimentAgent: Newspaper, RiskAgent: Shield, PortfolioAgent: Briefcase,
};
const DEC: Record<string, { label: string; color: string; bg: string; border: string; Icon: React.ElementType }> = {
  BUY: { label: "BUY", color: "text-green-400", bg: "bg-green-500/10", border: "border-green-500/20", Icon: TrendingUp },
  SELL: { label: "SELL", color: "text-red-400", bg: "bg-red-500/10", border: "border-red-500/20", Icon: TrendingDown },
  HOLD: { label: "HOLD", color: "text-amber-400", bg: "bg-amber-500/10", border: "border-amber-500/20", Icon: Minus },
  VETO: { label: "VETO", color: "text-gray-400", bg: "bg-gray-500/10", border: "border-gray-500/20", Icon: ShieldAlert },
};

export function AgentVoteCard({ vote }: { vote: AgentVote }) {
  const cfg = DEC[vote.decision] ?? DEC.HOLD;
  const AgentIcon = ICONS[vote.agent] ?? Radio;
  const pct = Math.round(vote.confidence * 100);
  const bar = vote.confidence >= 0.7 ? "bg-green-500" : vote.confidence >= 0.5 ? "bg-amber-500" : "bg-red-500";

  return (
    <div className={`card p-4 border flex flex-col justify-between ${cfg.border}`}>
      <div>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <div className={`w-7 h-7 rounded-lg flex items-center justify-center ${cfg.bg}`}>
              <AgentIcon className={`w-3.5 h-3.5 ${cfg.color}`} />
            </div>
            <span className="text-xs font-medium text-gray-400">{vote.agent.replace("Agent", "")}</span>
          </div>
          <div className={`flex items-center gap-1 px-2 py-0.5 rounded ${cfg.bg}`}>
            <cfg.Icon className={`w-3 h-3 ${cfg.color}`} />
            <span className={`text-xs font-bold ${cfg.color}`}>{cfg.label}</span>
          </div>
        </div>
        <div className="mb-3">
          <div className="flex justify-between mb-1">
            <span className="text-xs text-gray-500">Confidence</span>
            <span className="text-xs font-medium text-gray-300">{pct}%</span>
          </div>
          <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
            <div className={`h-full rounded-full transition-all duration-500 ${bar}`} style={{ width: `${pct}%` }} />
          </div>
        </div>
        <p className="text-xs text-gray-400 leading-relaxed mb-3">{vote.reasoning}</p>
      </div>

      {vote.metadata && Object.keys(vote.metadata).length > 0 && (
        <div className="mt-2 pt-3 border-t border-gray-800/60 grid grid-cols-2 gap-1.5 text-[10px]">
          {Object.entries(vote.metadata).map(([key, val]) => {
            // Skip internal or redundant keys
            if (["indicators_cached", "final_score"].includes(key)) return null;

            const label = key
              .replace(/_/g, " ")
              .replace(/\b\w/g, (c) => c.toUpperCase());

            let displayVal = "";
            if (typeof val === "number") {
              if (
                key.includes("pct") ||
                key.includes("fraction") ||
                key.includes("ratio") ||
                key.includes("utilisation")
              ) {
                displayVal = `${(val * 100).toFixed(1)}%`;
              } else if (
                key.includes("price") ||
                key.includes("lower") ||
                key.includes("upper") ||
                key.includes("middle") ||
                key.includes("rupees")
              ) {
                displayVal = `₹${val.toLocaleString("en-IN", { maximumFractionDigits: 1 })}`;
              } else {
                displayVal = val.toFixed(2);
              }
            } else if (typeof val === "boolean") {
              displayVal = val ? "YES" : "NO";
            } else {
              displayVal = String(val);
            }

            return (
              <div key={key} className="flex flex-col bg-gray-950/40 p-1.5 rounded-lg border border-gray-800/40">
                <span className="text-gray-500 font-medium truncate" title={label}>
                  {label}
                </span>
                <span className="text-gray-300 font-bold font-mono mt-0.5 truncate">
                  {displayVal}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}