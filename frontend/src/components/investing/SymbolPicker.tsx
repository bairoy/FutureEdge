/**
 * frontend/src/components/investing/SymbolPicker.tsx
 *
 * Type-ahead symbol picker for investing mode.
 *
 * WHY THIS IS NOT THE DASHBOARD'S SymbolSearch:
 * ───────────────────────────────────────────────
 * That one offers a "Search for 'KPITECH' — Custom Symbol" row whenever the
 * query matches nothing, so a typo sails straight through. On the trading
 * dashboard that is survivable; here it is not. An unrecognised symbol starts a
 * multi-minute analysis that scrapes, retrieves and runs a DCF before failing —
 * and KPITECH really did hang the backend for exactly that reason.
 *
 * So this component has NO escape hatch. If nothing matches, it says so and
 * refuses to navigate, because "we could not find that symbol" is a better
 * answer than a spinner followed by an error.
 *
 * WHAT IT VALIDATES, AND WHAT IT DOES NOT:
 * ──────────────────────────────────────────
 * It matches against Zerodha's NSE instrument master, which is the right check
 * for "is this a real listed ticker". It is NOT a guarantee that Screener has a
 * fundamentals page for it — the analysis can still come back 404 for a symbol
 * that exists here. Catching the typos is the point; catching everything is not
 * possible from the client.
 */

"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, Search, SearchX } from "lucide-react";
import api from "@/lib/api";

interface Instrument {
  tradingsymbol: string;
  name: string;
  exchange: string;
}

interface Props {
  onSelect: (symbol: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
}

const MIN_QUERY = 2;   // the backend rejects anything shorter

export function SymbolPicker({ onSelect, placeholder, autoFocus }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Instrument[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);   // distinguishes "no matches" from "not yet typed"
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(0);

  const boxRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  // Close when the click lands outside.
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  // Debounced search. The cleanup cancels the in-flight timer on every
  // keystroke, so a fast typist issues one request rather than one per letter.
  useEffect(() => {
    const q = query.trim();
    if (q.length < MIN_QUERY) {
      setResults([]);
      setSearched(false);
      return;
    }

    let cancelled = false;
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        const { data } = await api.get(`/api/v1/instruments/search`, {
          params: { q, exchange: "NSE", limit: 8 },
        });
        if (cancelled) return;
        setResults(data.results ?? []);
        setHighlighted(0);
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) {
          setSearching(false);
          setSearched(true);
        }
      }
    }, 250);

    return () => { cancelled = true; clearTimeout(timer); };
  }, [query]);

  // Keep the highlighted row in view when arrowing past the fold.
  useEffect(() => {
    listRef.current?.children[highlighted]?.scrollIntoView({ block: "nearest" });
  }, [highlighted]);

  function choose(instrument: Instrument) {
    setQuery("");
    setOpen(false);
    setResults([]);
    setSearched(false);
    onSelect(instrument.tradingsymbol.toUpperCase());
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") { setOpen(false); return; }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlighted((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlighted((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      // Enter selects a suggestion. It deliberately does NOT submit a raw
      // query — that is the escape hatch this component exists to remove.
      if (results[highlighted]) choose(results[highlighted]);
    }
  }

  const q = query.trim();
  const noMatches = open && searched && !searching && q.length >= MIN_QUERY && results.length === 0;

  return (
    <div ref={boxRef} className="relative">
      <div className="relative">
        <Search className="w-4 h-4 text-gray-600 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
        <input
          className="input !pl-9 !pr-9"
          value={query}
          autoFocus={autoFocus}
          placeholder={placeholder ?? "Search a company or symbol — e.g. KPIT"}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          aria-autocomplete="list"
          aria-expanded={open}
        />
        {searching && (
          <Loader2 className="w-4 h-4 text-gray-600 animate-spin absolute right-3 top-1/2 -translate-y-1/2" />
        )}
      </div>

      {open && (results.length > 0 || noMatches) && (
        <div className="absolute z-40 top-full left-0 right-0 mt-1.5 card overflow-hidden shadow-2xl">
          {results.length > 0 ? (
            <ul ref={listRef} className="max-h-72 overflow-y-auto py-1">
              {results.map((r, i) => (
                <li key={`${r.exchange}:${r.tradingsymbol}`}>
                  <button
                    onMouseEnter={() => setHighlighted(i)}
                    onClick={() => choose(r)}
                    className={`w-full text-left px-3 py-2 flex items-baseline justify-between gap-3
                      ${i === highlighted ? "bg-gray-800" : "hover:bg-gray-800/50"}`}
                  >
                    <span className="text-sm font-medium text-gray-100">{r.tradingsymbol}</span>
                    <span className="text-xs text-gray-500 truncate">{r.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            /* The message the whole component is for. */
            <div className="px-3 py-3 flex gap-2.5">
              <SearchX className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
              <div>
                <p className="text-sm text-gray-200">
                  Symbol not found — nothing on NSE matches “{q.toUpperCase()}”.
                </p>
                <p className="text-xs text-gray-500 mt-1 leading-relaxed">
                  Try searching by company name instead of the ticker. Some are not spelled
                  the way you would expect — KPIT Technologies is <code className="text-gray-400">KPITTECH</code>, not KPITECH.
                </p>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
