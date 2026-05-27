/**
 * frontend/src/components/charts/PriceChart.tsx
 *
 * Live candlestick chart using TradingView's lightweight-charts library.
 *
 * WHY lightweight-charts?
 * ─────────────────────────────────────────────────────────
 * Built specifically for financial time-series data.
 * Canvas-based (not SVG) so it handles thousands of candles
 * at 60fps without any performance issues.
 *
 * HOW UPDATES WORK:
 * ─────────────────────────────────────────────────────────
 * The chart is created ONCE on mount (useEffect with empty deps).
 * New ticks from the WebSocket call series.update() which is O(1)
 * — it only redraws the last candle, not the whole chart.
 * This is why the chart stays smooth even at high tick rates.
 *
 * DATA FORMAT:
 * ─────────────────────────────────────────────────────────
 * lightweight-charts requires { time, open, high, low, close }
 * where time is a Unix timestamp in SECONDS (not milliseconds).
 * Our useWebSocket hook converts timestamps to seconds before
 * adding them to the store.
 */

"use client";

import { useEffect, useRef } from "react";
import type { TickPoint } from "@/store";

interface PriceChartProps {
  ticks: TickPoint[];
  height?: number;
}

export function PriceChart({ ticks, height = 300 }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const seriesRef = useRef<any>(null);

  // ── Create chart once on mount ──────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;

    // Dynamic import avoids SSR errors (canvas API doesn't exist on server)
    import("lightweight-charts").then(({ createChart, ColorType }) => {
      if (!containerRef.current || chartRef.current) return;

      const chart = createChart(containerRef.current, {
        width: containerRef.current.clientWidth,
        height,
        layout: {
          background: { type: ColorType.Solid, color: "transparent" },
          textColor: "#9ca3af",
        },
        grid: {
          vertLines: { color: "#1f2937" },
          horzLines: { color: "#1f2937" },
        },
        crosshair: {
          vertLine: { color: "#4b5563" },
          horzLine: { color: "#4b5563" },
        },
        rightPriceScale: { borderColor: "#374151" },
        timeScale: { borderColor: "#374151", timeVisible: true, secondsVisible: false },
      });

      const series = chart.addCandlestickSeries({
        upColor: "#22c55e",
        downColor: "#ef4444",
        borderUpColor: "#22c55e",
        borderDownColor: "#ef4444",
        wickUpColor: "#22c55e",
        wickDownColor: "#ef4444",
      });

      // Seed with existing ticks if any
      if (ticks.length > 0) {
        // Assertion failed: data must be asc ordered by time
        // We MUST sort and deduplicate to prevent chart crashes
        const sorted = [...ticks].sort((a, b) => a.time - b.time);
        
        // Deduplicate: same timestamp causes crashes in lightweight-charts
        const unique = sorted.filter((tick, idx) => {
          if (idx === 0) return true;
          return tick.time > sorted[idx-1].time;
        });

        series.setData(unique as any);
        chart.timeScale().fitContent();
      }

      chartRef.current = chart;
      seriesRef.current = series;

      // Resize chart when container size changes
      const ro = new ResizeObserver(() => {
        if (containerRef.current)
          chart.applyOptions({ width: containerRef.current.clientWidth });
      });
      ro.observe(containerRef.current);

      return () => { ro.disconnect(); chart.remove(); chartRef.current = null; seriesRef.current = null; };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [height]);

  // ── Update chart on each new tick (O(1) operation) ──────────
  useEffect(() => {
    if (!seriesRef.current || ticks.length === 0) return;
    try {
      const lastTick = ticks[ticks.length - 1];
      
      // Safety: lightweight-charts crashes if updating with old or same time
      // unless it's the exact same candle being updated (which is okay).
      // However, to be safe, we only update if we have a valid series.
      seriesRef.current.update(lastTick);
    } catch {
      // Chart not ready or invalid data — set all data as fallback
      const sorted = [...ticks].sort((a,b) => a.time - b.time);
      seriesRef.current.setData(sorted);
    }
  }, [ticks]);

  if (ticks.length === 0) {
    return (
      <div style={{ height }} className="flex flex-col items-center justify-center text-gray-600 text-sm gap-2">
        <div className="w-8 h-8 border-2 border-gray-700 rounded-full animate-pulse" />
        <span>Waiting for live ticks…</span>
        <span className="text-xs text-gray-700">Set ACTIVE_FEED=zerodha in .env for real data</span>
      </div>
    );
  }

  return <div ref={containerRef} style={{ height }} />;
}