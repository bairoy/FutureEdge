/**
 * frontend/src/app/dashboard/investing/[symbol]/page.tsx
 *
 * The investment thesis view — Stage 1, 2 and 3 for one company.
 *
 * WHAT THIS PAGE MUST NOT BECOME:
 * ─────────────────────────────────
 * A trade ticket. There is no quantity field, no order button, and no path
 * from here to the broker — investing mode is advisory, and the human executes
 * at their own broker. The only actions available are: run the analysis again,
 * record that you own the thing, and supply a figure the system could not
 * compute. If a future change adds an execute button here, the graph branch
 * and the execution agent will both refuse it, and they should keep refusing.
 *
 * WHY THE STANCE IS RE-FETCHED RATHER THAN CACHED:
 * ──────────────────────────────────────────────────
 * The verb depends on the live price. The backend recomputes it on every read
 * from the stored band, so a stale client cache would show yesterday's verb
 * against today's price — the precise failure that keeping stance out of the
 * database was meant to avoid.
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { AlertTriangle, ArrowLeft, Loader2, RefreshCw, Search } from "lucide-react";

import api from "@/lib/api";
import { useAuthStore } from "@/store";
import { showToast } from "@/components/ui/Toast";
import { StanceHeader } from "@/components/investing/StanceHeader";
import { QualityScorecard } from "@/components/investing/QualityScorecard";
import { ValuationBand } from "@/components/investing/ValuationBand";
import { BusinessChecklist } from "@/components/investing/BusinessChecklist";
import { MissingDataPanel } from "@/components/investing/MissingDataPanel";
import { HoldingControl } from "@/components/investing/HoldingControl";
import { SymbolPicker } from "@/components/investing/SymbolPicker";
import type { Thesis } from "@/types";

// A cold run scrapes, embeds and retrieves before it returns. The shared axios
// timeout is 45s, which this legitimately exceeds — so it is overridden per
// request rather than globally, where it would also loosen every trading call.
const ANALYZE_TIMEOUT_MS = 300_000;

export default function ThesisPage() {
  const params = useParams();
  const router = useRouter();
  const symbol = String(params.symbol ?? "").toUpperCase();

  const canTrade = useAuthStore((s) => s.canTrade);
  const mayAnalyse = canTrade();

  const [thesis, setThesis] = useState<Thesis | null>(null);
  const [loading, setLoading] = useState(true);
  const [analysing, setAnalysing] = useState(false);
  const [notAnalysed, setNotAnalysed] = useState(false);
  // Kept in state rather than only shown as a toast: "Screener has no page for
  // KPITECH" is the answer to the question the user asked, and it should still
  // be on screen after the toast fades.
  const [analyseError, setAnalyseError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get<Thesis>(`/api/v1/investing/${symbol}/thesis`);
      setThesis(data);
      setNotAnalysed(false);
    } catch (err: any) {
      if (err?.response?.status === 404) {
        setNotAnalysed(true);
        setThesis(null);
      } else {
        showToast(err?.response?.data?.detail ?? "Could not load the thesis", "error");
      }
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => { setAnalyseError(null); if (symbol) load(); }, [symbol, load]);

  async function analyse() {
    setAnalysing(true);
    setAnalyseError(null);
    try {
      const { data } = await api.post<Thesis>(
        `/api/v1/investing/${symbol}/analyze`, {}, { timeout: ANALYZE_TIMEOUT_MS }
      );
      setThesis(data);
      setNotAnalysed(false);
      showToast(`${symbol} analysed — ${data.quality.grade.replace(/_/g, " ")}`, "success");
    } catch (err: any) {
      // 404 means the ticker does not exist on Screener; 502 means the scrape
      // failed. Both arrive with a usable message, so it is shown rather than
      // replaced with a generic failure.
      const detail = err?.response?.data?.detail;
      const timedOut = err?.code === "ECONNABORTED";
      setAnalyseError(
        detail ??
        (timedOut
          ? "The analysis took longer than five minutes and the request gave up. It may still be running on the server — reload in a moment."
          : "Analysis failed.")
      );
      showToast(detail ?? "Analysis failed", "error");
    } finally {
      setAnalysing(false);
    }
  }

  // ── Loading ───────────────────────────────────────────────
  if (loading) {
    return (
      <div className="flex items-center gap-3 text-gray-500 py-20 justify-center">
        <Loader2 className="w-5 h-5 animate-spin" />
        <span className="text-sm">Loading {symbol}…</span>
      </div>
    );
  }

  // ── Never analysed ────────────────────────────────────────
  if (notAnalysed) {
    return (
      <div className="max-w-lg mx-auto text-center py-20">
        <Search className="w-8 h-8 text-gray-700 mx-auto mb-4" />
        <h1 className="text-lg font-semibold text-gray-100">No analysis stored for {symbol}</h1>
        <p className="text-sm text-gray-500 mt-2 leading-relaxed">
          Running it scrapes the financials, queries the company's filings and builds a
          discounted cash flow. The first run on a symbol is slow; later ones are not,
          because the fundamentals are cached for three days.
        </p>
        {analyseError && (
          <div className="mt-5 flex gap-2.5 text-left p-3 rounded-lg bg-amber-500/10 border border-amber-500/30">
            <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
            <p className="text-xs text-amber-200 leading-relaxed">{analyseError}</p>
          </div>
        )}

        {mayAnalyse ? (
          <button onClick={analyse} disabled={analysing} className="btn-primary mt-5 inline-flex items-center gap-2">
            {analysing ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            {analysing ? "Analysing — this can take a few minutes…" : `Analyse ${symbol}`}
          </button>
        ) : (
          <p className="text-xs text-gray-600 mt-5">Running an analysis requires the trader role.</p>
        )}

        {/* Reached by typing a URL, so the picker is offered here too — most
            arrivals at this screen with a bad symbol are a misspelling. */}
        <div className="mt-6 text-left">
          <p className="section-label mb-1.5">Look up a different symbol</p>
          <SymbolPicker onSelect={(s) => router.push(`/dashboard/investing/${s}`)} />
        </div>

        <button onClick={() => router.push("/dashboard/investing")} className="btn-ghost mt-4 block mx-auto">
          Back to watchlist
        </button>
      </div>
    );
  }

  if (!thesis) return null;

  return (
    <div className="space-y-4 max-w-6xl">

      {/* ── Page header ────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <button onClick={() => router.push("/dashboard/investing")}
                  className="btn-ghost !px-2" title="Back to watchlist">
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div>
            <h1 className="text-lg font-bold text-gray-100">{thesis.symbol}</h1>
            <p className="text-xs text-gray-500">
              Analysed {new Date(thesis.as_of).toLocaleString("en-IN")}
              {thesis.data_as_of && ` · financials as of ${thesis.data_as_of}`}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {mayAnalyse && (
            <HoldingControl symbol={thesis.symbol} owned={thesis.owned} onChange={load} />
          )}
          {mayAnalyse && (
            <button onClick={analyse} disabled={analysing}
                    className="btn-primary !py-1.5 inline-flex items-center gap-2">
              {analysing ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              {analysing ? "Analysing…" : "Re-run"}
            </button>
          )}
        </div>
      </div>

      {/* ── The verb, with the rule that produced it ───────── */}
      <StanceHeader thesis={thesis} />

      {/* ── Narrative ──────────────────────────────────────── */}
      {thesis.narrative && (
        <section className="card p-5">
          <p className="section-label mb-2">Summary</p>
          <p className="text-sm text-gray-300 leading-relaxed whitespace-pre-line">
            {thesis.narrative}
          </p>
        </section>
      )}

      {/* ── The three stages ───────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
        <ValuationBand thesis={thesis} />
        <QualityScorecard thesis={thesis} />
      </div>

      <BusinessChecklist thesis={thesis} />

      <MissingDataPanel
        symbol={thesis.symbol}
        items={thesis.missing_data ?? []}
        canEdit={mayAnalyse}
        onStored={load}
      />
    </div>
  );
}
