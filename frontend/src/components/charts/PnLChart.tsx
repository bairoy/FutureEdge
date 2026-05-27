/**
 * frontend/src/components/charts/PnLChart.tsx
 *
 * Cumulative PnL area chart built with Recharts.
 *
 * WHY RECHARTS HERE (not lightweight-charts)?
 * ─────────────────────────────────────────────
 * lightweight-charts is optimised for real-time OHLCV data.
 * Recharts is better for analytical charts over a fixed dataset
 * because it handles React data arrays natively, and provides
 * built-in axes, tooltips, and gradient fills with simple JSX.
 *
 * WHAT IT SHOWS:
 * ─────────────────────────────────────────────────────────
 * X axis : trade number (1, 2, 3 …)
 * Y axis : running total PnL in Rupees
 * Line   : green when portfolio is up, red when down
 * Tooltip: exact cumulative PnL and per-trade PnL on hover
 *
 * DATA: only closed trades with a realized_pnl value are included.
 */

"use client";

import {
  AreaChart, Area, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import type { Trade } from "@/types";

interface PnLChartProps {
  trades: Trade[];
  height?: number;
}

export function PnLChart({ trades, height = 240 }: PnLChartProps) {
  // Only closed trades with a realized PnL
  const closed = trades
    .filter((t) => t.realized_pnl != null)
    .sort((a, b) => new Date(a.opened_at).getTime() - new Date(b.opened_at).getTime());

  if (closed.length === 0) {
    return (
      <div style={{ height }} className="flex items-center justify-center text-gray-600 text-sm">
        No closed trades yet
      </div>
    );
  }

  // Build cumulative series
  let cum = 0;
  const data = closed.map((t, i) => {
    cum += t.realized_pnl ?? 0;
    return {
      trade: i + 1,
      cum: Math.round(cum * 100) / 100,
      pnl: Math.round((t.realized_pnl ?? 0) * 100) / 100,
    };
  });

  const lastCum = data[data.length - 1]?.cum ?? 0;
  const lineClr = lastCum >= 0 ? "#22c55e" : "#ef4444";

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 5, right: 5, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={lineClr} stopOpacity={0.2} />
            <stop offset="95%" stopColor={lineClr} stopOpacity={0} />
          </linearGradient>
        </defs>

        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />

        <XAxis
          dataKey="trade"
          tick={{ fill: "#6b7280", fontSize: 10 }}
          axisLine={{ stroke: "#374151" }}
          tickLine={false}
        />

        <YAxis
          tick={{ fill: "#6b7280", fontSize: 10 }}
          axisLine={{ stroke: "#374151" }}
          tickLine={false}
          tickFormatter={(v: number) => `₹${v >= 0 ? "+" : ""}${v}`}
        />

        <Tooltip
          contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: "8px", fontSize: "12px" }}
          formatter={(v: number, name: string) => [
            `₹${v >= 0 ? "+" : ""}${v.toFixed(2)}`,
            name === "cum" ? "Cumulative PnL" : "Trade PnL",
          ]}
          labelFormatter={(l: number) => `Trade #${l}`}
        />

        <Area
          type="monotone"
          dataKey="cum"
          stroke={lineClr}
          strokeWidth={2}
          fill="url(#pnlGrad)"
          dot={false}
          activeDot={{ r: 4, fill: lineClr }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}