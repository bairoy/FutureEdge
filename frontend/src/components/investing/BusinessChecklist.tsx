/**
 * frontend/src/components/investing/BusinessChecklist.tsx
 *
 * Stage 1 — the 18 qualitative questions, answered from the company's own
 * filings, with citations.
 *
 * WHY THE GATE SAYS "NOTHING FOUND" AND NOT "CLEAN":
 * ────────────────────────────────────────────────────
 * CLEAR means the searches this system ran turned up nothing. It does not mean
 * the promoters are clean — a Tier-1 regulatory check with confirmed identity
 * matching is not built yet, and a false all-clear is invisible exactly when it
 * matters most. The wording here is deliberate and should not be "improved"
 * into something more reassuring.
 *
 * UNANSWERED QUESTIONS ARE SHOWN, NOT FILTERED:
 * ───────────────────────────────────────────────
 * Coverage is partial by design — some questions need sources the system does
 * not yet fetch. Hiding the unanswered ones would make partial coverage look
 * complete, which is the same failure the missing-data panel exists to prevent.
 */

"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Globe, Quote, ShieldQuestion } from "lucide-react";
import type { BusinessAnswer, Thesis } from "@/types";

// Filings are audited and the open web is not. The badge is not decoration —
// it is the difference between a fact the company signed and a page a model
// found, and the reader has to see which one they are reading.
const SOURCE_BADGE: Record<string, { label: string; cls: string }> = {
  DOCUMENTS:    { label: "from filings",     cls: "text-blue-400/80" },
  WEB:          { label: "from web search",  cls: "text-amber-400/80" },
  SHAREHOLDING: { label: "from shareholding data", cls: "text-blue-400/80" },
};

const STATUS_STYLE: Record<string, { dot: string; label: string }> = {
  ANSWERED:       { dot: "bg-green-400",  label: "answered" },
  NOT_FOUND:      { dot: "bg-gray-600",   label: "not found in the documents" },
  NEEDS_EXTERNAL: { dot: "bg-amber-400",  label: "needs a source outside the filings" },
};

export function BusinessChecklist({ thesis }: { thesis: Thesis }) {
  const [expanded, setExpanded] = useState(false);
  const answers: BusinessAnswer[] = thesis.business?.answers ?? [];
  const gate = thesis.business?.gate ?? "CLEAR";
  const answered = answers.filter((a) => a.status === "ANSWERED").length;
  const fromWeb = answers.filter((a) => a.status === "ANSWERED" && a.source === "WEB").length;

  const shown = expanded ? answers : answers.slice(0, 6);

  return (
    <section className="card p-5">
      <div className="flex items-start justify-between gap-4 mb-4">
        <div>
          <h2 className="text-sm font-semibold text-gray-100">Business</h2>
          <p className="text-xs text-gray-500 mt-0.5">Stage 1 — 18 qualitative questions</p>
        </div>
        <div className="text-right">
          <p className="text-lg font-bold text-gray-100">{answered}<span className="text-gray-600 text-sm">/{answers.length || 18}</span></p>
          <p className="text-xs text-gray-500">
            {fromWeb > 0 ? `${answered - fromWeb} from filings · ${fromWeb} from web` : "answered from filings"}
          </p>
        </div>
      </div>

      {/* The gate, stated honestly. */}
      <div className="flex gap-2 mb-4 p-3 rounded-lg bg-gray-800/40 border border-gray-700">
        <ShieldQuestion className="w-4 h-4 text-gray-500 shrink-0 mt-0.5" />
        <p className="text-xs text-gray-400 leading-relaxed">
          Promoter gate: <strong className="text-gray-200">{gate}</strong> —{" "}
          {gate === "CLEAR"
            ? "the searches run here found nothing. That is not the same as clean: regulatory checks with confirmed identity matching are not wired up yet."
            : "findings are surfaced for a person to read rather than acted on automatically."}
        </p>
      </div>

      {answers.length === 0 ? (
        <p className="text-xs text-gray-600">Stage 1 did not run for this analysis.</p>
      ) : (
        <>
          <ul className="divide-y divide-gray-800">
            {shown.map((a) => {
              const s = STATUS_STYLE[a.status] ?? STATUS_STYLE.NOT_FOUND;
              return (
                <li key={a.n} className="py-3">
                  <div className="flex gap-2.5">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 mt-1.5 ${s.dot}`} />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-gray-200">
                        <span className="text-gray-600 mr-1.5">{a.n}.</span>{a.question}
                      </p>
                      <p className="text-xs text-gray-400 mt-1 leading-relaxed whitespace-pre-line">
                        {a.answer || <span className="text-gray-600 italic">{s.label}</span>}
                      </p>

                      {/* Judgement is labelled as judgement — it did not come
                          out of a filing, and should not read as if it did. */}
                      <div className="flex flex-wrap items-center gap-x-3 mt-1.5">
                        {a.source && SOURCE_BADGE[a.source] && a.status === "ANSWERED" && (
                          <span className={`text-[11px] ${SOURCE_BADGE[a.source].cls}`}>
                            {SOURCE_BADGE[a.source].label}
                          </span>
                        )}
                        {a.is_opinion && (
                          <span className="text-[11px] text-amber-400/80">
                            interpretation, not a quoted fact
                          </span>
                        )}
                      </div>

                      {/* Web sources are links, so the reader can weigh a
                          regulator's page against a content farm themselves —
                          which is why the domain leads the label. */}
                      {a.sources && a.sources.length > 0 ? (
                        <ul className="mt-1.5 space-y-0.5">
                          {a.sources.map((src, i) => (
                            <li key={i} className="flex gap-1.5 text-[11px] text-gray-600">
                              <Globe className="w-3 h-3 shrink-0 mt-0.5" />
                              <a href={src.url} target="_blank" rel="noopener noreferrer"
                                 className="truncate hover:text-blue-400 transition-colors">
                                <span className="text-gray-500">{src.domain}</span>
                                {src.title && ` — ${src.title}`}
                              </a>
                            </li>
                          ))}
                        </ul>
                      ) : a.citations?.length > 0 ? (
                        <ul className="mt-1.5 space-y-0.5">
                          {a.citations.map((c, i) => (
                            <li key={i} className="flex gap-1.5 text-[11px] text-gray-600">
                              <Quote className="w-3 h-3 shrink-0 mt-0.5" />
                              <span className="truncate">{c}</span>
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>

          {answers.length > 6 && (
            <button onClick={() => setExpanded((v) => !v)}
              className="btn-ghost mt-3 flex items-center gap-1.5 !px-0">
              {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
              {expanded ? "Show fewer" : `Show all ${answers.length} questions`}
            </button>
          )}
        </>
      )}
    </section>
  );
}
