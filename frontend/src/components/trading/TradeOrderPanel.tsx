/**
 * frontend/src/components/trading/TradeOrderPanel.tsx
 *
 * Premium trading panel that lets users configure trade size overrides
 * before running an agent cycle.
 */

"use client";

import { useState, useEffect } from "react";
import { AlertCircle, Sliders, Wallet, Landmark } from "lucide-react";

interface TradeOrderPanelProps {
  onConfigChange: (config: {
    quantity: number | null;
    positionRupees: number | null;
    overrideKelly: boolean;
  }) => void;
  disabled?: boolean;
}

type SizeMode = "kelly" | "quantity" | "rupees";

export function TradeOrderPanel({ onConfigChange, disabled = false }: TradeOrderPanelProps) {
  const [mode, setMode] = useState<SizeMode>("kelly");
  const [quantity, setQuantity] = useState<number | "">("");
  const [positionRupees, setPositionRupees] = useState<number | "">("");
  const [overrideKelly, setOverrideKelly] = useState(false);

  // Trigger callback when configuration changes
  useEffect(() => {
    onConfigChange({
      quantity: mode === "quantity" && quantity !== "" ? Number(quantity) : null,
      positionRupees: mode === "rupees" && positionRupees !== "" ? Number(positionRupees) : null,
      overrideKelly: mode !== "kelly" && overrideKelly,
    });
  }, [mode, quantity, positionRupees, overrideKelly, onConfigChange]);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-2xl p-4 md:p-5 shadow-2xl transition-all duration-300 hover:border-gray-700/50">
      <div className="flex items-center gap-2.5 mb-4">
        <div className="w-8 h-8 rounded-lg bg-blue-500/10 flex items-center justify-center text-blue-400">
          <Sliders className="w-4 h-4" />
        </div>
        <div>
          <h2 className="text-sm font-bold text-gray-100 uppercase tracking-wider">Execution Configuration</h2>
          <p className="text-[10px] text-gray-500">Configure size sizing, risk control overrides, and constraints</p>
        </div>
      </div>

      {/* Sizing Mode Selection Tabs */}
      <div className="grid grid-cols-3 gap-1.5 p-1 bg-gray-950/60 rounded-xl border border-gray-800/80 mb-4">
        {(["kelly", "quantity", "rupees"] as SizeMode[]).map((m) => (
          <button
            key={m}
            type="button"
            disabled={disabled}
            onClick={() => setMode(m)}
            className={`
              py-2 text-[11px] md:text-xs font-semibold rounded-lg transition-all duration-200 uppercase tracking-wider
              ${mode === m
                ? "bg-blue-600 text-white shadow-lg shadow-blue-500/15"
                : "text-gray-400 hover:text-gray-200 hover:bg-gray-800/40"
              }
              disabled:opacity-50 disabled:pointer-events-none
            `}
          >
            {m === "kelly" && "Kelly Sizing"}
            {m === "quantity" && "Fixed Shares"}
            {m === "rupees" && "INR Amount"}
          </button>
        ))}
      </div>

      {/* Dynamic Fields based on Sizing Mode */}
      <div className="space-y-4">
        {mode === "kelly" && (
          <div className="p-3.5 rounded-xl bg-gray-950/40 border border-gray-800/50 text-xs text-gray-400 leading-relaxed flex gap-3">
            <AlertCircle className="w-4 h-4 text-blue-400 shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold text-gray-200 mb-0.5">Automated Kelly Criterion Sizing</p>
              Mathematical positioning based on win/loss ratios. Starts at a default of <span className="text-blue-400 font-mono">2%</span> and scales dynamically with historical performance. Requires minimal manual input.
            </div>
          </div>
        )}

        {mode === "quantity" && (
          <div className="space-y-2">
            <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider block">Quantity (Shares)</label>
            <div className="relative flex items-center bg-gray-950/60 border border-gray-850 rounded-xl px-3.5 py-2.5 focus-within:border-blue-500/50 transition-all duration-200">
              <Landmark className="w-4 h-4 text-gray-500 mr-2.5" />
              <input
                type="number"
                min="1"
                step="1"
                disabled={disabled}
                placeholder="Enter exact number of shares (e.g. 10)"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value === "" ? "" : Math.max(1, parseInt(e.target.value)))}
                className="bg-transparent border-none outline-none text-sm text-gray-100 w-full font-mono placeholder:text-gray-650"
              />
            </div>
          </div>
        )}

        {mode === "rupees" && (
          <div className="space-y-2">
            <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider block">Position Size (INR)</label>
            <div className="relative flex items-center bg-gray-950/60 border border-gray-850 rounded-xl px-3.5 py-2.5 focus-within:border-blue-500/50 transition-all duration-200">
              <span className="text-sm font-semibold text-gray-500 mr-2.5">₹</span>
              <input
                type="number"
                min="1"
                disabled={disabled}
                placeholder="Enter Rupees amount (e.g. 15000)"
                value={positionRupees}
                onChange={(e) => setPositionRupees(e.target.value === "" ? "" : Math.max(1, parseFloat(e.target.value)))}
                className="bg-transparent border-none outline-none text-sm text-gray-100 w-full font-mono placeholder:text-gray-650"
              />
            </div>
          </div>
        )}

        {/* Kelly override checkbox */}
        {mode !== "kelly" && (
          <label className="flex items-center gap-3 p-3 rounded-xl border border-gray-800 bg-gray-950/30 hover:bg-gray-950/50 cursor-pointer select-none transition-colors">
            <input
              type="checkbox"
              disabled={disabled}
              checked={overrideKelly}
              onChange={(e) => setOverrideKelly(e.target.checked)}
              className="w-4 h-4 rounded border-gray-800 bg-gray-900 text-blue-500 focus:ring-blue-500 focus:ring-offset-gray-900 focus:ring-2 accent-blue-500"
            />
            <div className="text-xs">
              <span className="font-semibold text-gray-250 block">Override Risk Kelly Fraction</span>
              <span className="text-[10px] text-gray-500">Enable to place order bypassing standard Kelly portfolio limits.</span>
            </div>
          </label>
        )}
      </div>
    </div>
  );
}
