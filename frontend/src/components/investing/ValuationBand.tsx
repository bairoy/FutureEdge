/**
 * frontend/src/components/investing/ValuationBand.tsx
 *
 * Stage 3 — the DCF result, rendered as a band and a grid.
 *
 * WHY THERE IS NO BIG SINGLE NUMBER HERE:
 * ─────────────────────────────────────────
 * A DCF produces a number with maybe two significant figures of honesty in it.
 * Printing "₹391" in 48px would claim precision the model does not have, which
 * is the exact failure the ±10% band exists to prevent. So the band is the
 * primary object on this panel, the intrinsic value is a tick inside it, and
 * the sensitivity grid is shown by default rather than hidden behind a toggle —
 * the reader should see how much of the answer is assumption before they see
 * the answer.
 */

"use client";

import { AlertTriangle } from "lucide-react";
import type { Thesis } from "@/types";

const rupees = (n: number | null | undefined, dp = 0) =>
  n == null ? "—" : `₹${n.toLocaleString("en-IN", { maximumFractionDigits: dp })}`;

const num = (k: string) => parseFloat(k.split("_")[1]);

export function ValuationBand({ thesis }: { thesis: Thesis }) {
  const v = thesis.valuation;
  const a = v?.assumptions ?? {};

  if (!v?.complete) {
    return (
      <section className="card p-5">
        <h2 className="text-sm font-semibold text-gray-100">Valuation</h2>
        <p className="text-xs text-gray-500 mt-0.5 mb-4">Stage 3 — discounted cash flow</p>
        <div className="flex gap-2 p-3 rounded-lg bg-gray-800/50 border border-gray-700">
          <AlertTriangle className="w-4 h-4 text-gray-500 shrink-0 mt-0.5" />
          <p className="text-xs text-gray-400 leading-relaxed">
            No valuation band was produced for this run, so there is nothing to price
            against. Any stance shown above rests on quality alone.
          </p>
        </div>
      </section>
    );
  }

  const { intrinsic, lower_band: lo, upper_band: hi, mos_buy_price: mos } = v;
  const price = a.current_price ?? null;

  // Scale spans everything that must be visible, with margin so the end
  // markers are not flush against the edges.
  const points = [mos, lo, hi, intrinsic, price].filter((n): n is number => n != null);
  const min = Math.min(...points) * 0.92;
  const max = Math.max(...points) * 1.08;
  const pos = (n: number) => Math.min(100, Math.max(0, ((n - min) / (max - min)) * 100));

  // Sensitivity grid — rows are discount rates, columns terminal growth.
  // Cells are skipped where terminal growth >= discount rate, so the column
  // set is the union across rows rather than any single row's keys.
  const grid = v.sensitivity?.intrinsic_by_discount_and_terminal_growth ?? {};
  const rows = Object.keys(grid).sort((x, y) => num(x) - num(y));
  const cols = Array.from(
    new Set(rows.flatMap((r) => Object.keys(grid[r])))
  ).sort((x, y) => num(x) - num(y));
  const baseDr = v.sensitivity?.base_discount_rate_pct;

  return (
    <section className="card p-5">
      <div className="flex items-start justify-between gap-4 mb-5">
        <div>
          <h2 className="text-sm font-semibold text-gray-100">Valuation</h2>
          <p className="text-xs text-gray-500 mt-0.5">Stage 3 — discounted cash flow</p>
        </div>
        <div className="text-right">
          <p className="section-label">Value band</p>
          <p className="text-lg font-bold text-gray-100">
            {rupees(lo)} – {rupees(hi)}
          </p>
        </div>
      </div>

      {/* ── The band ─────────────────────────────────────────── */}
      <div className="pt-8 pb-9 px-1">
        <div className="relative h-2 rounded-full bg-gray-800">

          {/* Margin-of-safety zone: everything below the buy trigger. */}
          {mos != null && (
            <div className="absolute inset-y-0 left-0 rounded-l-full bg-green-500/25"
                 style={{ width: `${pos(mos)}%` }} />
          )}

          {/* The band itself. */}
          {lo != null && hi != null && (
            <div className="absolute inset-y-0 bg-blue-500/40"
                 style={{ left: `${pos(lo)}%`, width: `${pos(hi) - pos(lo)}%` }} />
          )}

          {/* Intrinsic value — a tick, not a headline. */}
          {intrinsic != null && (
            <div className="absolute -top-1 -bottom-1 w-0.5 bg-blue-300"
                 style={{ left: `${pos(intrinsic)}%` }}>
              <span className="absolute -top-7 left-1/2 -translate-x-1/2 whitespace-nowrap
                               text-[11px] text-blue-300">
                {rupees(intrinsic)}
              </span>
            </div>
          )}

          {/* Buy-below marker. */}
          {mos != null && (
            <div className="absolute -bottom-6 -translate-x-1/2 whitespace-nowrap
                            text-[11px] text-green-400"
                 style={{ left: `${pos(mos)}%` }}>
              buy below {rupees(mos)}
            </div>
          )}

          {/* Where the market actually is. */}
          {price != null && (
            <div className="absolute -top-3.5 -bottom-3.5 w-0.5 bg-gray-100"
                 style={{ left: `${pos(price)}%` }}>
              <span className="absolute -bottom-6 left-1/2 -translate-x-1/2 whitespace-nowrap
                               text-[11px] font-semibold text-gray-100">
                market {rupees(price)}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* ── Assumptions ──────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-3 pt-4 border-t border-gray-800">
        <Fact label="Discount rate" value={a.discount_rate_pct != null ? `${a.discount_rate_pct}%` : "—"}
              sub={a.beta != null ? `beta ${a.beta}` : undefined} />
        <Fact label="Growth" value={`${a.stage1_growth_pct ?? "—"}% → ${a.stage2_growth_pct ?? "—"}%`}
              sub="years 1-5, then 6-10" />
        <Fact label="Terminal growth" value={a.terminal_growth_pct != null ? `${a.terminal_growth_pct}%` : "—"}
              sub={a.terminal_share_of_value != null
                ? `${Math.round(a.terminal_share_of_value * 100)}% of value`
                : undefined} />
        <Fact label="Base free cash flow" value={a.base_fcf_cr != null ? `₹${a.base_fcf_cr.toLocaleString("en-IN")} Cr` : "—"}
              sub={a.net_debt_cr != null
                ? `net ${a.net_debt_cr < 0 ? "cash" : "debt"} ₹${Math.abs(a.net_debt_cr).toLocaleString("en-IN")} Cr`
                : undefined} />
      </div>

      {/* Risk-free rate is a hardcoded figure with a review date. Showing the
          date is what stops a stale bond yield from quietly biasing every
          intrinsic value the system produces. */}
      {a.risk_free_rate_pct != null && (
        <p className="text-[11px] text-gray-600 mt-3">
          CAPM: {a.risk_free_rate_pct}% risk-free
          {a.risk_free_reviewed && ` (reviewed ${a.risk_free_reviewed})`}
          {a.beta != null && ` + ${a.beta} beta`}
          {a.equity_risk_premium_pct != null && ` × ${a.equity_risk_premium_pct}% equity risk premium`}
          {a.beta_note && ` — ${a.beta_note}`}
        </p>
      )}

      {/* ── Reverse DCF ──────────────────────────────────────── */}
      {v.reverse_dcf_implied_fcf != null && (
        <div className="mt-4 p-3 rounded-lg bg-gray-800/40 border border-gray-700">
          <p className="section-label mb-1">What the market price implies</p>
          <p className="text-sm text-gray-200">
            Today's price requires a base free cash flow of{" "}
            <strong className="text-gray-100">
              ₹{v.reverse_dcf_implied_fcf.toLocaleString("en-IN", { maximumFractionDigits: 0 })} Cr
            </strong>
            {a.base_fcf_cr != null && (
              <span className="text-gray-400">
                {" "}versus ₹{a.base_fcf_cr.toLocaleString("en-IN")} Cr actual
              </span>
            )}
          </p>
          {a.reverse_dcf_note && (
            <p className="text-xs text-gray-500 mt-1.5 leading-relaxed">{a.reverse_dcf_note}</p>
          )}
        </div>
      )}

      {/* ── Warnings ─────────────────────────────────────────── */}
      {(a.warnings?.length ?? 0) > 0 && (
        <ul className="mt-4 space-y-1.5">
          {a.warnings!.map((w, i) => (
            <li key={i} className="flex gap-2 text-xs text-amber-300/80">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span className="leading-relaxed">{w}</span>
            </li>
          ))}
        </ul>
      )}

      {/* ── Sensitivity ──────────────────────────────────────── */}
      {rows.length > 0 && (
        <div className="mt-5 pt-4 border-t border-gray-800">
          <p className="section-label mb-1">Sensitivity — intrinsic value per share</p>
          <p className="text-xs text-gray-600 mb-3">
            Rows are discount rates, columns terminal growth. Green means the
            assumption set leaves the stock cheaper than it trades today.
          </p>
          <div className="overflow-x-auto">
            <table className="text-xs w-full">
              <thead>
                <tr className="text-gray-500">
                  <th className="text-left font-medium py-1.5 pr-3">discount ↓ / terminal →</th>
                  {cols.map((c) => (
                    <th key={c} className={`text-right font-medium py-1.5 px-2
                      ${num(c) === a.terminal_growth_pct ? "text-blue-300" : ""}`}>
                      {num(c)}%
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const isBase = baseDr != null && Math.abs(num(r) - baseDr) < 0.05;
                  return (
                    <tr key={r} className={isBase ? "bg-blue-500/5" : ""}>
                      <td className={`py-1.5 pr-3 ${isBase ? "text-blue-300 font-medium" : "text-gray-400"}`}>
                        {num(r)}%{isBase && " (base)"}
                      </td>
                      {cols.map((c) => {
                        const cell = grid[r]?.[c];
                        if (cell == null) {
                          // Terminal growth at or above the discount rate — the
                          // formula has no meaning there, so nothing is shown.
                          return <td key={c} className="text-right py-1.5 px-2 text-gray-700">—</td>;
                        }
                        const cheap = price != null && cell > price;
                        return (
                          <td key={c}
                              className={`text-right py-1.5 px-2 tabular-nums
                                ${cheap ? "text-green-400" : "text-gray-400"}`}>
                            {cell.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}

function Fact({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <p className="section-label">{label}</p>
      <p className="text-sm font-semibold text-gray-100 mt-0.5">{value}</p>
      {sub && <p className="text-[11px] text-gray-600">{sub}</p>}
    </div>
  );
}
