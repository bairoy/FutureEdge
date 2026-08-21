/**
 * frontend/src/app/dashboard/investing/page.tsx
 *
 * Entry point for investing mode — the watchlist, the holdings register, and
 * a box to look up any symbol.
 *
 * WHY THE INVESTING WATCHLIST IS NOT THE TRADING WATCHLIST:
 * ───────────────────────────────────────────────────────────
 * They are kept deliberately disjoint. Holding a symbol in demat while the
 * trading side shorts it intraday can be treated by the broker as a delivery
 * sell, so the two lists do not overlap by default. Banks and NBFCs are also
 * excluded: leverage and interest-coverage ratios do not carry their usual
 * meaning for a lender, and the Stage 2 checklist would grade them wrongly.
 */

"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { BookOpen, Loader2, Wallet } from "lucide-react";

import api from "@/lib/api";
import { SymbolPicker } from "@/components/investing/SymbolPicker";
import type { InvestingHolding } from "@/types";

export default function InvestingIndexPage() {
  const router = useRouter();
  const [symbols, setSymbols] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [holdings, setHoldings] = useState<InvestingHolding[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const [w, h] = await Promise.all([
          api.get<{ symbols: string[]; note: string }>("/api/v1/investing/watchlist"),
          api.get<InvestingHolding[]>("/api/v1/investing/holdings"),
        ]);
        setSymbols(w.data.symbols ?? []);
        setNote(w.data.note ?? "");
        setHoldings(h.data ?? []);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const held = new Set(holdings.map((h) => h.symbol));

  return (
    <div className="space-y-5 max-w-4xl">

      <div>
        <h1 className="text-lg font-bold text-gray-100">Investing</h1>
        <p className="text-sm text-gray-500 mt-0.5">
          Long-term fundamental analysis. Advisory only — nothing here places an order,
          and the decision to act is yours.
        </p>
      </div>

      {/* ── Look up any symbol ─────────────────────────────────
          Suggestions only — there is no "use it anyway" row. An unrecognised
          symbol would otherwise start a multi-minute analysis before failing. */}
      <div>
        <SymbolPicker onSelect={(symbol) => router.push(`/dashboard/investing/${symbol}`)} />
        <p className="text-xs text-gray-600 mt-1.5">
          Pick from the suggestions — matched against the NSE instrument list, so a
          mistyped ticker is caught before an analysis starts.
        </p>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-gray-500 py-10 justify-center">
          <Loader2 className="w-4 h-4 animate-spin" /><span className="text-sm">Loading…</span>
        </div>
      ) : (
        <>
          {/* ── Holdings ─────────────────────────────────────── */}
          <section className="card p-5">
            <div className="flex items-center gap-2 mb-1">
              <Wallet className="w-4 h-4 text-gray-500" />
              <h2 className="text-sm font-semibold text-gray-100">Your holdings</h2>
            </div>
            <p className="text-xs text-gray-500 mb-4">
              What you bought yourself, at your own broker. Recorded here because owning a
              position changes the stance — the same numbers read ADD instead of BUY, and
              HOLD instead of WATCH.
            </p>

            {holdings.length === 0 ? (
              <p className="text-xs text-gray-600">
                Nothing recorded yet. Open a symbol and use “I hold this”.
              </p>
            ) : (
              <ul className="divide-y divide-gray-800">
                {holdings.map((h) => (
                  <li key={h.symbol}>
                    <Link href={`/dashboard/investing/${h.symbol}`}
                          className="flex items-center justify-between py-2.5 group">
                      <div>
                        <p className="text-sm font-medium text-gray-100 group-hover:text-blue-400 transition-colors">
                          {h.symbol}
                        </p>
                        <p className="text-xs text-gray-500">
                          {h.quantity} @ ₹{h.avg_buy_price.toLocaleString("en-IN")} · since {h.buy_date}
                        </p>
                      </div>
                      <span className="text-xs text-gray-600 tabular-nums">
                        ₹{(h.quantity * h.avg_buy_price).toLocaleString("en-IN", { maximumFractionDigits: 0 })} invested
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* ── Watchlist ────────────────────────────────────── */}
          <section className="card p-5">
            <div className="flex items-center gap-2 mb-1">
              <BookOpen className="w-4 h-4 text-gray-500" />
              <h2 className="text-sm font-semibold text-gray-100">Watchlist</h2>
            </div>
            {note && <p className="text-xs text-gray-500 mb-4 leading-relaxed">{note}</p>}

            <div className="flex flex-wrap gap-2">
              {symbols.map((s) => (
                <Link key={s} href={`/dashboard/investing/${s}`}
                  className="px-3 py-1.5 rounded-lg bg-gray-800 border border-gray-700
                             text-sm text-gray-300 hover:text-blue-400 hover:border-blue-500/40
                             transition-colors">
                  {s}
                  {held.has(s) && <span className="ml-1.5 text-[11px] text-green-400">held</span>}
                </Link>
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
