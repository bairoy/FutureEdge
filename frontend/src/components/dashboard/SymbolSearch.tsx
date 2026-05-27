/**
 * frontend/src/components/dashboard/SymbolSearch.tsx
 *
 * A sleek, command-palette style search box for stocks and indices.
 * Updates the global currentSymbol in the store.
 */

"use client";

import { useState, useRef, useEffect } from "react";
import { Search, X, ChevronRight, Hash } from "lucide-react";
import { useTradingStore } from "@/store";

const COMMON_SYMBOLS = [
  { symbol: "NIFTY 50", type: "Index" },
  { symbol: "NIFTY BANK", type: "Index" },
  { symbol: "NIFTY FIN SERVICE", type: "Index" },
  { symbol: "RELIANCE", type: "Equity" },
  { symbol: "TCS", type: "Equity" },
  { symbol: "HDFCBANK", type: "Equity" },
  { symbol: "INFY", type: "Equity" },
];

export function SymbolSearch() {
  const { currentSymbol, setCurrentSymbol } = useTradingStore();
  const [query, setQuery] = useState("");
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Close on click outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) setIsOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Hotkey: press / to focus
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "/" && document.activeElement?.tagName !== "INPUT") {
        e.preventDefault();
        inputRef.current?.focus();
        setIsOpen(true);
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);

  const filtered = query.trim() === ""
    ? COMMON_SYMBOLS
    : COMMON_SYMBOLS.filter(s => 
        s.symbol.toLowerCase().includes(query.toLowerCase())
      );

  function select(s: string) {
    setCurrentSymbol(s.toUpperCase());
    setQuery("");
    setIsOpen(false);
    inputRef.current?.blur();
  }

  return (
    <div ref={containerRef} className="relative w-full max-w-sm">
      <div className={`
        group flex items-center gap-2.5 px-3 py-2 rounded-xl border transition-all duration-200
        ${isOpen 
          ? "bg-gray-800 border-blue-500/50 shadow-lg shadow-blue-500/5" 
          : "bg-gray-900 border-gray-800 hover:border-gray-700"
        }
      `}>
        <Search className={`w-4 h-4 transition-colors ${isOpen ? "text-blue-400" : "text-gray-500"}`} />
        
        <input
          ref={inputRef}
          type="text"
          value={isOpen ? query : currentSymbol}
          onChange={(e) => {
            setQuery(e.target.value);
            setIsOpen(true);
          }}
          onFocus={() => setIsOpen(true)}
          placeholder="Search symbol (e.g. RELIANCE)..."
          className="bg-transparent border-none outline-none text-sm text-gray-100 placeholder:text-gray-600 w-full font-medium"
          onKeyDown={(e) => {
            if (e.key === "Enter" && query.trim()) select(query);
            if (e.key === "Escape") setIsOpen(false);
          }}
        />

        {isOpen ? (
          <button onClick={() => setIsOpen(false)} className="text-gray-600 hover:text-gray-400">
            <X className="w-3.5 h-3.5" />
          </button>
        ) : (
          <div className="hidden sm:flex items-center gap-1 px-1.5 py-0.5 rounded border border-gray-800 text-[10px] text-gray-600 font-mono">
            <span>/</span>
          </div>
        )}
      </div>

      {/* ── Dropdown ────────────────────────────────────────── */}
      {isOpen && (
        <div className="absolute top-full left-0 right-0 mt-2 bg-gray-900 border border-gray-800 rounded-xl shadow-2xl z-50 overflow-hidden animate-in fade-in slide-in-from-top-1 duration-200">
          <div className="p-1.5">
            <p className="px-3 py-1.5 text-[10px] font-bold text-gray-500 uppercase tracking-widest">
              {query ? "Search Results" : "Common Symbols"}
            </p>
            
            <div className="space-y-0.5 mt-1">
              {filtered.map((item) => (
                <button
                  key={item.symbol}
                  onClick={() => select(item.symbol)}
                  className="w-full flex items-center justify-between px-3 py-2 rounded-lg hover:bg-gray-800 text-left transition-colors group/item"
                >
                  <div className="flex items-center gap-3">
                    <div className="w-7 h-7 rounded-lg bg-gray-800 flex items-center justify-center text-gray-400 group-hover/item:bg-blue-500/10 group-hover/item:text-blue-400 transition-colors">
                      <Hash className="w-3.5 h-3.5" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-gray-100">{item.symbol}</p>
                      <p className="text-[10px] text-gray-500">{item.type}</p>
                    </div>
                  </div>
                  <ChevronRight className="w-4 h-4 text-gray-700 opacity-0 group-hover/item:opacity-100 transition-all translate-x-[-4px] group-hover/item:translate-x-0" />
                </button>
              ))}

              {query && !filtered.some(f => f.symbol.toUpperCase() === query.toUpperCase()) && (
                <button
                  onClick={() => select(query)}
                  className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-blue-500/5 text-left border border-transparent hover:border-blue-500/20 transition-all"
                >
                  <div className="w-7 h-7 rounded-lg bg-blue-500/10 flex items-center justify-center text-blue-400">
                    <ChevronRight className="w-4 h-4" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-blue-400">Search for "{query.toUpperCase()}"</p>
                    <p className="text-[10px] text-blue-500/60 uppercase">Custom Symbol</p>
                  </div>
                </button>
              )}
            </div>
          </div>

          <div className="bg-gray-800/30 p-2.5 border-t border-gray-800 flex items-center justify-between text-[10px] text-gray-500">
            <div className="flex items-center gap-3">
              <span className="flex items-center gap-1"><kbd className="px-1 rounded bg-gray-800 border border-gray-700 font-mono">↑↓</kbd> Navigate</span>
              <span className="flex items-center gap-1"><kbd className="px-1 rounded bg-gray-800 border border-gray-700 font-mono">↵</kbd> Select</span>
            </div>
            <span className="flex items-center gap-1"><kbd className="px-1 rounded bg-gray-800 border border-gray-700 font-mono">esc</kbd> Close</span>
          </div>
        </div>
      )}
    </div>
  );
}
