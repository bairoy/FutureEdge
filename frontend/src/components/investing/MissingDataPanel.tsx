/**
 * frontend/src/components/investing/MissingDataPanel.tsx
 *
 * Everything a stage could not compute, and the affordance to supply it.
 *
 * WHY THIS PANEL EXISTS AT ALL:
 * ───────────────────────────────
 * A scorecard that silently drops four of ten checks looks identical to one
 * that passed all ten. The backend refuses to drop them — each becomes a
 * MissingDatum with a reason — and this panel is where that refusal becomes
 * visible to a person. It is not an error list; it is the analysis telling you
 * exactly which figures it still needs.
 *
 * WHY PROVENANCE IS REQUIRED ON THE FORM:
 * ─────────────────────────────────────────
 * Hand-entered values are the HIGHEST-trust tier in this system — a person
 * read them off a primary source. That is precisely why the source note is
 * mandatory here: without it one typo becomes an unattributable permanent
 * verdict that outranks everything the scraper produced. The API accepts a
 * null note; this form does not.
 *
 * WHY SUBMITTING DOES NOT RESUME A WORKFLOW:
 * ────────────────────────────────────────────
 * The tempting design pauses the graph and resumes on input. But resume is a
 * money-moving path with a race-condition history, gated at risk_manager.
 * Instead the value is stored and the user re-runs — cheap, because the scrape
 * is cached, and idempotent, because there is no checkpoint to resume.
 */

"use client";

import { useState } from "react";
import { FileQuestion, Plus, X } from "lucide-react";
import api from "@/lib/api";
import { showToast } from "@/components/ui/Toast";
import type { MissingDatum } from "@/types";

const STAGE_LABEL: Record<string, string> = {
  BUSINESS: "Stage 1 · business",
  FINANCIAL: "Stage 2 · financials",
  VALUATION: "Stage 3 · valuation",
};

interface Props {
  symbol: string;
  items: MissingDatum[];
  /** Supplying a figure requires the trader role, same as running an analysis. */
  canEdit: boolean;
  /** Called after a value is stored, so the page can offer a re-run. */
  onStored: () => void;
}

export function MissingDataPanel({ symbol, items, canEdit, onStored }: Props) {
  const [openField, setOpenField] = useState<string | null>(null);

  if (!items?.length) {
    return (
      <section className="card p-5">
        <h2 className="text-sm font-semibold text-gray-100">Missing data</h2>
        <p className="text-xs text-gray-500 mt-2">
          Nothing was left uncomputed — every check in this run had the figures it needed.
        </p>
      </section>
    );
  }

  return (
    <section className="card p-5">
      <div className="flex items-start justify-between gap-4 mb-1">
        <h2 className="text-sm font-semibold text-gray-100">Missing data</h2>
        <span className="text-xs text-gray-500">{items.length} item{items.length === 1 ? "" : "s"}</span>
      </div>
      <p className="text-xs text-gray-500 mb-4">
        Reported rather than silently dropped. Supplying any of these and re-running
        raises the completeness the grade above is computed from.
      </p>

      <ul className="space-y-2">
        {items.map((m, i) => {
          const key = `${m.stage}:${m.field}:${i}`;
          const isOpen = openField === key;
          return (
            <li key={key} className="rounded-lg bg-gray-800/40 border border-gray-700">
              <div className="flex items-start gap-3 px-3 py-2.5">
                <FileQuestion className="w-4 h-4 text-gray-500 shrink-0 mt-0.5" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <code className="text-xs text-gray-200">{m.field}</code>
                    <span className="text-[11px] text-gray-600">{STAGE_LABEL[m.stage] ?? m.stage}</span>
                    {m.period && <span className="text-[11px] text-gray-600">· {m.period}</span>}
                  </div>
                  <p className="text-xs text-gray-500 mt-0.5 leading-relaxed">{m.reason}</p>
                </div>
                {canEdit && (
                  <button onClick={() => setOpenField(isOpen ? null : key)}
                    className="btn-ghost shrink-0 !px-2 !py-1 flex items-center gap-1 text-xs">
                    {isOpen ? <X className="w-3.5 h-3.5" /> : <Plus className="w-3.5 h-3.5" />}
                    {isOpen ? "Cancel" : "Provide this"}
                  </button>
                )}
              </div>

              {isOpen && (
                <ManualInputForm
                  symbol={symbol}
                  field={m.field}
                  defaultPeriod={m.period ?? ""}
                  onDone={() => { setOpenField(null); onStored(); }}
                />
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function ManualInputForm({
  symbol, field, defaultPeriod, onDone,
}: { symbol: string; field: string; defaultPeriod: string; onDone: () => void }) {
  const [period, setPeriod] = useState(defaultPeriod);
  const [value, setValue] = useState("");
  const [source, setSource] = useState("");
  const [saving, setSaving] = useState(false);

  const numeric = value.trim() !== "" && !Number.isNaN(Number(value));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!period.trim() || !value.trim() || !source.trim()) return;

    setSaving(true);
    try {
      await api.post("/api/v1/investing/manual-input", {
        symbol,
        period: period.trim(),
        field_name: field,
        // A figure read off a statement is a number; anything else (an auditor
        // name, a segment description) is text. Both are legitimate answers.
        value_numeric: numeric ? Number(value) : null,
        value_text: numeric ? null : value.trim(),
        source_note: source.trim(),
      });
      showToast(`Stored ${field} for ${symbol} — re-run to use it`, "success");
      onDone();
    } catch (err: any) {
      showToast(err?.response?.data?.detail ?? "Could not store the value", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="px-3 pb-3 pt-1 border-t border-gray-700/60 space-y-2">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <div>
          <label className="section-label">Period</label>
          <input className="input mt-1" value={period} onChange={(e) => setPeriod(e.target.value)}
                 placeholder="Mar 2026" required />
          <p className="text-[11px] text-gray-600 mt-1">Match the statement column label exactly.</p>
        </div>
        <div>
          <label className="section-label">Value</label>
          <input className="input mt-1" value={value} onChange={(e) => setValue(e.target.value)}
                 placeholder="e.g. 4821.5" required />
          <p className="text-[11px] text-gray-600 mt-1">
            {value.trim() === "" ? "Numbers are stored as numbers, anything else as text."
              : numeric ? "Stored as a number." : "Stored as text."}
          </p>
        </div>
      </div>
      <div>
        <label className="section-label">Source</label>
        <input className="input mt-1" value={source} onChange={(e) => setSource(e.target.value)}
               placeholder="Annual Report FY26, p.142" required />
        <p className="text-[11px] text-gray-600 mt-1">
          Required. A hand-entered figure outranks the scraper, so it has to be attributable.
        </p>
      </div>
      <div className="flex justify-end">
        <button type="submit" className="btn-primary !py-1.5" disabled={saving}>
          {saving ? "Saving…" : "Store value"}
        </button>
      </div>
    </form>
  );
}
