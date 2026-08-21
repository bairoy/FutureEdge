/**
 * frontend/src/components/investing/StanceHeader.tsx
 *
 * The verb — BUY / ADD / HOLD / WATCH / EXIT / AVOID / NOT_RATED — and the
 * exact matrix cell that produced it.
 *
 * WHY `rule_applied` IS NOT OPTIONAL HERE:
 * ─────────────────────────────────────────
 * A verb on its own is an oracle. The backend computes `rule_applied` for
 * every branch precisely so the answer can always be inverted back into its
 * reasoning, and rendering the verb without it would throw that away at the
 * last step. They are one component for that reason — there is no prop
 * combination that shows the action and hides the rule.
 *
 * WHY OWNERSHIP IS ON THE HEADER:
 * ─────────────────────────────────
 * The same company at the same price with the same band is HOLD if you own it
 * and WATCH if you don't. Ownership is an input to the verb, so it is shown
 * beside the verb rather than buried in a holdings tab.
 */

"use client";

import { Info, ShieldCheck, Wallet } from "lucide-react";
import type { StanceAction, Thesis } from "@/types";

// Colour carries meaning: green = act, blue = keep, amber = wait, red = get out.
// NOT_RATED is deliberately NOT neutral grey — a refusal to judge is a real
// result the reader must notice, not an empty state.
const ACTION_STYLE: Record<StanceAction, { box: string; text: string; label: string }> = {
  BUY:       { box: "bg-green-500/10 border-green-500/40",  text: "text-green-400",  label: "Below your buy level" },
  ADD:       { box: "bg-green-500/10 border-green-500/40",  text: "text-green-400",  label: "Below your buy level" },
  HOLD:      { box: "bg-blue-500/10 border-blue-500/40",    text: "text-blue-400",   label: "Thesis intact" },
  WATCH:     { box: "bg-amber-500/10 border-amber-500/40",  text: "text-amber-400",  label: "Waiting for a price" },
  EXIT:      { box: "bg-red-500/10 border-red-500/40",      text: "text-red-400",    label: "Quality failed" },
  AVOID:     { box: "bg-red-500/10 border-red-500/40",      text: "text-red-400",    label: "Quality failed" },
  NOT_RATED: { box: "bg-purple-500/10 border-purple-500/40", text: "text-purple-300", label: "No verdict issued" },
};

const POSITION_LABEL: Record<string, string> = {
  BELOW_MOS:     "below margin of safety",
  UNDERVALUED:   "below the band",
  FAIRLY_VALUED: "inside the band",
  OVERVALUED:    "above the band",
};

const rupees = (n: number | null | undefined) =>
  n == null ? "—" : `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

export function StanceHeader({ thesis }: { thesis: Thesis }) {
  const { stance, owned, symbol } = thesis;
  const style = ACTION_STYLE[stance.action] ?? ACTION_STYLE.NOT_RATED;

  return (
    <div className={`rounded-xl border p-5 ${style.box}`}>
      <div className="flex flex-wrap items-start justify-between gap-4">

        {/* ── The verb ─────────────────────────────────────── */}
        <div>
          <p className="section-label mb-1">{symbol} · stance</p>
          <div className="flex items-baseline gap-3">
            <span className={`text-4xl font-bold tracking-tight ${style.text}`}>
              {stance.action.replace("_", " ")}
            </span>
            <span className="text-sm text-gray-400">{style.label}</span>
          </div>

          {/* The rule travels with the verb, always. */}
          <p className="text-sm text-gray-300 mt-3 max-w-2xl leading-relaxed">
            {stance.rule_applied}
          </p>
        </div>

        {/* ── The inputs that produced it ──────────────────── */}
        <div className="text-right space-y-1.5 shrink-0">
          <div>
            <p className="section-label">Market price</p>
            <p className="text-xl font-bold text-gray-100">{rupees(stance.current_price)}</p>
            {stance.price_vs_band && (
              <p className="text-xs text-gray-500">
                {POSITION_LABEL[stance.price_vs_band] ?? stance.price_vs_band}
              </p>
            )}
          </div>
          {stance.trigger_price != null && (
            <div className="pt-1">
              <p className="section-label">Buy below</p>
              <p className="text-sm font-semibold text-gray-300">{rupees(stance.trigger_price)}</p>
            </div>
          )}
        </div>
      </div>

      {/* ── Ownership + horizon + the advisory statement ──── */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 mt-4 pt-4 border-t border-gray-700/50 text-xs">
        <span className="flex items-center gap-1.5 text-gray-400">
          <Wallet className="w-3.5 h-3.5" />
          {owned
            ? <>Held: <strong className="text-gray-200">{owned.quantity}</strong> @ {rupees(owned.avg_buy_price)} since {owned.buy_date}</>
            : <>Not held — <span className="text-gray-500">ownership changes this verb</span></>}
        </span>

        <span className="flex items-center gap-1.5 text-gray-500">
          <Info className="w-3.5 h-3.5" />
          {stance.horizon}
        </span>

        {/* Restated on the page because the backend restates it on every
            response: this surface cannot place an order. */}
        <span className="flex items-center gap-1.5 text-gray-500 ml-auto">
          <ShieldCheck className="w-3.5 h-3.5" />
          Advisory only — no order is placed from this page
        </span>
      </div>
    </div>
  );
}
