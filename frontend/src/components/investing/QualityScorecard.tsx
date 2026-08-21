/**
 * frontend/src/components/investing/QualityScorecard.tsx
 *
 * Stage 2 — the 10-point financial checklist, plus the grade it rolls up to.
 *
 * WHY COMPLETENESS SITS BESIDE THE GRADE:
 * ─────────────────────────────────────────
 * "INVESTMENT_GRADE on 6 of 10 checks" and "INVESTMENT_GRADE on 10 of 10" are
 * different claims, and the grade alone renders them identically. The whole
 * point of tracking missing data is defeated if the UI shows the verdict and
 * hides how much of it was actually computed, so the two are one block.
 *
 * CONVICTION IS NOT COMPLETENESS:
 * ─────────────────────────────────
 * Completeness asks "how much did we manage to compute". Conviction asks "how
 * strong is the result we computed". A company can be fully analysed and
 * marginal (high completeness, low conviction), or barely analysed and
 * obviously excellent as far as it goes. Collapsing them into one bar would
 * lose the distinction, so both are shown.
 *
 * Checks numbered 100+ are calculation cautions, not scored checks — they are
 * separated out below so they cannot dilute the pass/fail count.
 */

"use client";

import { AlertTriangle, Check, HelpCircle, Minus, X } from "lucide-react";
import type { CheckStatus, FinancialCheck, Thesis } from "@/types";

const GRADE_STYLE: Record<string, string> = {
  INVESTMENT_GRADE: "text-green-400",
  WATCHLIST:        "text-amber-400",
  NOT_INVESTABLE:   "text-red-400",
  NOT_RATED:        "text-purple-300",
};

const STATUS_ICON: Record<CheckStatus, React.ReactNode> = {
  PASS:           <Check className="w-4 h-4 text-green-400" />,
  FAIL:           <X className="w-4 h-4 text-red-400" />,
  FLAG:           <AlertTriangle className="w-4 h-4 text-amber-400" />,
  NOT_COMPUTABLE: <Minus className="w-4 h-4 text-gray-600" />,
};

const STATUS_TEXT: Record<CheckStatus, string> = {
  PASS: "text-green-400", FAIL: "text-red-400",
  FLAG: "text-amber-400", NOT_COMPUTABLE: "text-gray-500",
};

function Meter({ label, value, hint }: { label: string; value: number; hint: string }) {
  const pct = Math.round((value ?? 0) * 100);
  const bar = pct >= 80 ? "bg-green-500" : pct >= 50 ? "bg-amber-500" : "bg-red-500";
  return (
    <div className="flex-1 min-w-[140px]">
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="section-label">{label}</span>
        <span className="text-sm font-semibold text-gray-200">{pct}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-gray-800 overflow-hidden">
        <div className={`h-full rounded-full ${bar}`} style={{ width: `${pct}%` }} />
      </div>
      <p className="text-xs text-gray-600 mt-1">{hint}</p>
    </div>
  );
}

export function QualityScorecard({ thesis }: { thesis: Thesis }) {
  const { quality, financial } = thesis;
  const checks = financial?.checks ?? [];

  const scored   = checks.filter((c) => c.n < 100);
  const cautions = checks.filter((c) => c.n >= 100);

  // Counted separately rather than reduced to "N of 10 passed". A check that
  // could not be computed is not a check that failed, and rolling the two
  // together would make missing data look like bad results.
  const passed   = scored.filter((c) => c.status === "PASS").length;
  const flagged  = scored.filter((c) => c.status === "FLAG").length;
  const failed   = scored.filter((c) => c.status === "FAIL").length;
  const uncomputed = scored.filter((c) => c.status === "NOT_COMPUTABLE").length;

  return (
    <section className="card p-5">
      <div className="flex items-start justify-between gap-4 mb-4">
        <div>
          <h2 className="text-sm font-semibold text-gray-100">Quality</h2>
          <p className="text-xs text-gray-500 mt-0.5">Stage 2 — 10-point financial due diligence</p>
        </div>
        <div className="text-right">
          <p className={`text-lg font-bold ${GRADE_STYLE[quality.grade] ?? "text-gray-300"}`}>
            {quality.grade.replace(/_/g, " ")}
          </p>
          <p className="text-xs text-gray-500">
            {[
              `${passed} passed`,
              failed ? `${failed} failed` : null,
              flagged ? `${flagged} flagged` : null,
              uncomputed ? `${uncomputed} not computable` : null,
            ].filter(Boolean).join(" · ")}
          </p>
        </div>
      </div>

      {/* NOT_RATED is a refusal, and the reason is the substance of it. */}
      {quality.grade === "NOT_RATED" && quality.not_rated_reason && (
        <div className="flex gap-2 mb-4 p-3 rounded-lg bg-purple-500/10 border border-purple-500/30">
          <HelpCircle className="w-4 h-4 text-purple-300 shrink-0 mt-0.5" />
          <p className="text-xs text-purple-200 leading-relaxed">{quality.not_rated_reason}</p>
        </div>
      )}

      <div className="flex flex-wrap gap-5 mb-5">
        <Meter label="Completeness" value={quality.completeness}
               hint="how much could be computed" />
        <Meter label="Conviction" value={quality.conviction}
               hint="how strong the computed result is" />
      </div>

      {/* ── Red flags, listed apart from routine findings ──── */}
      {quality.red_flags?.length > 0 && (
        <div className="mb-5">
          <p className="section-label mb-2">Red flags ({quality.red_flags.length})</p>
          <ul className="space-y-1.5">
            {quality.red_flags.map((f, i) => (
              <li key={i} className="flex gap-2 text-xs text-red-300 bg-red-500/5
                                     border border-red-500/20 rounded-lg px-3 py-2">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                <span className="leading-relaxed">
                  <span className="text-red-400/70">Q{f.question}</span> — {f.flag}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ── The checks themselves ────────────────────────────── */}
      {scored.length > 0 ? (
        <ul className="divide-y divide-gray-800">
          {scored.map((c: FinancialCheck) => (
            <li key={c.n} className="py-2.5 flex gap-3">
              <span className="shrink-0 mt-0.5">{STATUS_ICON[c.status]}</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm text-gray-200">
                    <span className="text-gray-600 mr-1.5">{c.n}.</span>{c.name}
                  </span>
                  {/* The status, not `value`. Each check's `value` is a
                      different unitless quantity — a growth gap on one, a
                      debt/equity ratio on another, and on a NOT_COMPUTABLE
                      check a proxy that was explicitly rejected. Printed bare
                      it reads as a computed result. The numbers live in
                      `detail`, where they carry their units. */}
                  <span className={`text-xs font-medium shrink-0 ${STATUS_TEXT[c.status]}`}>
                    {c.status === "NOT_COMPUTABLE" ? "not computable" : c.status.toLowerCase()}
                  </span>
                </div>
                <p className="text-xs text-gray-500 mt-0.5 leading-relaxed">{c.detail}</p>
                {c.source && <p className="text-[11px] text-gray-700 mt-0.5">source: {c.source}</p>}
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-gray-600">No checks were computed for this run.</p>
      )}

      {/* Cautions qualify the numbers above, so they sit beside them rather
          than in a separate panel nobody scrolls to. They carry no pass/fail. */}
      {cautions.length > 0 && (
        <div className="mt-4 pt-4 border-t border-gray-800">
          <p className="section-label mb-2">Calculation cautions</p>
          <ul className="space-y-1.5">
            {cautions.map((c, i) => (
              <li key={i} className="flex gap-2 text-xs text-amber-300/80">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                <span className="leading-relaxed">{c.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
