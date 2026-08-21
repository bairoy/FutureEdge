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
 *
 * WHY THE `ready` STATE EXISTS:
 * ─────────────────────────────────────────────────────────
 * The library is imported dynamically, so the series doesn't exist for the
 * first render or two. Refs don't trigger renders — so without a state flag
 * the data effect would never re-run once the series appeared, and an
 * already-populated `ticks` array would render a permanently empty chart.
 *
 * WHY TIMES ARE SHIFTED (see IST_OFFSET_SECONDS):
 * ─────────────────────────────────────────────────────────
 * lightweight-charts v4 renders timestamps in UTC and exposes no timezone
 * option, so an NSE bar at 14:40 IST would label as 09:10 on the axis.
 */

"use client";

import { useEffect, useRef, useState } from "react";
import type { TickPoint } from "@/store";

interface PriceChartProps {
  ticks: TickPoint[];
  height?: number;
}

/**
 * NSE trades in IST. The library has no timezone setting, so we shift into IST
 * at the presentation boundary only — `TickPoint.time` stays a true UTC epoch
 * everywhere else in the app. Do NOT push this offset back into the store or
 * the API; it exists purely so the axis and crosshair read as market time.
 */
const IST_OFFSET_SECONDS = 5 * 3600 + 30 * 60;

type ChartBar = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
};

const toBar = (t: TickPoint): ChartBar => ({
  time:  t.time + IST_OFFSET_SECONDS,
  open:  t.open,
  high:  t.high,
  low:   t.low,
  close: t.close,
});

/**
 * lightweight-charts throws on unordered or duplicate timestamps, so sort and
 * dedupe before handing it data.
 *
 * On a duplicate timestamp the LAST bar wins: two ticks inside the same second
 * means the later one carries the fresher price.
 */
const prepareData = (data: TickPoint[]): ChartBar[] => {
  const sorted = [...data].sort((a, b) => a.time - b.time);
  const bars: ChartBar[] = [];

  for (const tick of sorted) {
    const bar = toBar(tick);
    if (bars.length > 0 && bars[bars.length - 1].time === bar.time) {
      bars[bars.length - 1] = bar;
    } else {
      bars.push(bar);
    }
  }

  return bars;
};

export function PriceChart({ ticks, height = 300 }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const seriesRef = useRef<any>(null);

  // Raw (un-shifted) time of the newest bar handed to the series, so we can
  // tell an append from a reset without converting back and forth.
  const lastTimeRef = useRef<number | null>(null);

  const [ready, setReady] = useState(false);

  // ── Create chart once on mount ──────────────────────────────
  useEffect(() => {
    let cancelled = false;
    let teardown: (() => void) | null = null;

    // Dynamic import avoids SSR errors (canvas API doesn't exist on server)
    import("lightweight-charts").then(({ createChart, ColorType }) => {
      if (cancelled || !containerRef.current || chartRef.current) return;

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

      chartRef.current = chart;
      seriesRef.current = series;

      // Resize chart when container size changes
      const ro = new ResizeObserver(() => {
        if (containerRef.current)
          chart.applyOptions({ width: containerRef.current.clientWidth });
      });
      ro.observe(containerRef.current);

      teardown = () => {
        ro.disconnect();
        chart.remove();
        chartRef.current = null;
        seriesRef.current = null;
        lastTimeRef.current = null;
      };

      // Refs alone can't wake the data effect — this is what does it.
      setReady(true);
    });

    // Returned from useEffect ITSELF. Returning it from inside .then() (as this
    // used to) makes it the promise's resolution value, which React never sees
    // — so every unmount leaked a chart instance and a live ResizeObserver.
    return () => {
      cancelled = true;
      teardown?.();
    };
    // Created once. Height is applied via options below, not by rebuilding.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Apply height changes without rebuilding the chart ───────
  // The old code listed `height` in the creation effect's deps but bailed early
  // on `chartRef.current`, so a height change resized the wrapper div while the
  // canvas kept its original height.
  useEffect(() => {
    if (!ready || !chartRef.current) return;
    chartRef.current.applyOptions({ height });
  }, [ready, height]);

  // ── Update chart on each new tick (O(1) operation) ──────────
  useEffect(() => {
    const series = seriesRef.current;
    if (!ready || !series || ticks.length === 0) return;

    const lastTick = ticks[ticks.length - 1];

    // Append when the newest tick is at or after the last one we drew;
    // anything else (symbol switch, history reload) needs a full reset.
    const isAppend =
      lastTimeRef.current !== null &&
      ticks.length > 1 &&
      lastTick.time >= lastTimeRef.current;

    if (isAppend) {
      try {
        series.update(toBar(lastTick));
        lastTimeRef.current = lastTick.time;
        return;
      } catch (err) {
        // update() rejects out-of-order data — fall through to a full reset.
        console.warn("[PriceChart] Append failed, resetting data:", err);
      }
    }

    const bars = prepareData(ticks);
    series.setData(bars);

    if (bars.length > 0) {
      lastTimeRef.current = bars[bars.length - 1].time - IST_OFFSET_SECONDS;
      chartRef.current?.timeScale().fitContent();
    } else {
      lastTimeRef.current = null;
    }
  }, [ticks, ready]);

  return (
    <div className="relative w-full" style={{ height }}>
      {ticks.length === 0 && (
        <div className="absolute inset-0 flex flex-col items-center justify-center text-gray-500 text-sm gap-2 bg-gray-950/60 backdrop-blur-sm z-10 border border-gray-800 rounded-xl">
          <div className="w-8 h-8 border-2 border-gray-700 border-t-blue-500 rounded-full animate-spin" />
          <span className="font-medium text-gray-300">Loading market history...</span>
          <span className="text-xs text-gray-500">Fetching last 500 candles via yfinance</span>
        </div>
      )}
      <div ref={containerRef} className="w-full h-full" />
    </div>
  );
}
