/**
 * frontend/src/app/dashboard/backtest/page.tsx
 *
 * Premium historical strategy backtesting page for FutureEdge.
 * Allows users to simulate technical consensus strategies on yfinance NSE data.
 */

"use client";

import { useState } from "react";
import { 
  LineChart, AreaChart, Area, XAxis, YAxis, 
  CartesianGrid, Tooltip, ResponsiveContainer 
} from "recharts";
import { 
  TrendingUp, TrendingDown, RefreshCw, Play, 
  DollarSign, Percent, BarChart, ShieldAlert, Award
} from "lucide-react";

import api from "@/lib/api";
import { showToast } from "@/components/ui/Toast";
import { StatCard } from "@/components/dashboard/StatCard";

interface BacktestTrade {
  symbol: string;
  direction: string;
  entry_time: string;
  exit_time: string;
  entry_price: number;
  exit_price: number;
  shares: number;
  pnl: number;
  pnl_pct: number;
  exit_reason: string;
}

interface EquityPoint {
  time: string;
  equity: number;
}

interface BacktestResponse {
  symbol: string;
  period: string;
  interval: string;
  metrics: {
    total_trades: number;
    win_rate: number;
    total_pnl: number;
    profit_factor: number;
    max_drawdown_pct: number;
    sharpe_ratio: number;
    initial_capital: number;
    final_capital: number;
  };
  trades: BacktestTrade[];
  equity_curve: EquityPoint[];
}

export default function BacktestPage() {
  const [symbol, setSymbol] = useState("RELIANCE");
  const [period, setPeriod] = useState("1mo");
  const [interval, setIntervalVal] = useState("15m");
  const [capital, setCapital] = useState("100000");
  const [stopLoss, setStopLoss] = useState("1.5");
  const [takeProfit, setTakeProfit] = useState("3.0");
  const [sizePct, setSizePct] = useState("10");

  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<BacktestResponse | null>(null);

  async function handleRunBacktest(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setResult(null);

    try {
      const { data } = await api.post<BacktestResponse>("/api/v1/backtest", {
        symbol,
        period,
        interval,
        initial_capital: parseFloat(capital),
        stop_loss_pct: parseFloat(stopLoss),
        take_profit_pct: parseFloat(takeProfit),
        size_pct: parseFloat(sizePct),
      });

      setResult(data);
      showToast("Backtest simulation completed successfully!", "success");
    } catch (err: any) {
      console.error(err);
      showToast(err?.response?.data?.detail ?? "Backtest execution failed", "error");
    } finally {
      setLoading(false);
    }
  }

  const pnlColor = result && result.metrics.total_pnl >= 0 ? "text-green-400" : "text-red-400";
  const ddColor = result && result.metrics.max_drawdown_pct > 10 ? "text-red-400" : "text-green-400";
  const lineClr = result && result.metrics.total_pnl >= 0 ? "#22c55e" : "#ef4444";

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between border-b border-gray-800/60 pb-3">
        <div>
          <h1 className="text-xl font-bold text-gray-100">Historical Strategy Backtester</h1>
          <p className="text-xs text-gray-500 mt-0.5">Optimize entry scoring, stop-losses, and profit targets using free historical NSE market data.</p>
        </div>
      </div>

      {/* --- FORM CONFIGURATIONS --- */}
      <form onSubmit={handleRunBacktest} className="card p-5 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 bg-gray-900/60 border border-gray-800/40 backdrop-blur-md">
        <div>
          <label className="block text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5">Asset / Symbol</label>
          <select 
            value={symbol} 
            onChange={(e) => setSymbol(e.target.value)} 
            className="w-full bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-blue-500"
          >
            <option value="RELIANCE">RELIANCE (Reliance Industries)</option>
            <option value="INFY">INFY (Infosys)</option>
            <option value="TCS">TCS (Tata Consultancy)</option>
            <option value="NIFTY 50">NIFTY 50 Index</option>
            <option value="NIFTY BANK">NIFTY BANK Index</option>
          </select>
        </div>

        <div>
          <label className="block text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5">Simulation Period</label>
          <select 
            value={period} 
            onChange={(e) => setPeriod(e.target.value)} 
            className="w-full bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-blue-500"
          >
            <option value="1mo">1 Month</option>
            <option value="3mo">3 Months</option>
            <option value="6mo">6 Months</option>
            <option value="1y">1 Year</option>
          </select>
        </div>

        <div>
          <label className="block text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5">Timeframe / Interval</label>
          <select 
            value={interval} 
            onChange={(e) => setIntervalVal(e.target.value)} 
            className="w-full bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-blue-500"
          >
            <option value="5m">5 Minutes</option>
            <option value="15m">15 Minutes</option>
            <option value="30m">30 Minutes</option>
            <option value="1h">1 Hour</option>
            <option value="1d">1 Day</option>
          </select>
        </div>

        <div>
          <label className="block text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5">Initial Capital (INR)</label>
          <input 
            type="number" 
            value={capital} 
            onChange={(e) => setCapital(e.target.value)} 
            className="w-full bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-blue-500"
          />
        </div>

        <div>
          <label className="block text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5">Stop Loss (%)</label>
          <input 
            type="number" 
            step="0.1" 
            value={stopLoss} 
            onChange={(e) => setStopLoss(e.target.value)} 
            className="w-full bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-blue-500"
          />
        </div>

        <div>
          <label className="block text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5">Take Profit (%)</label>
          <input 
            type="number" 
            step="0.1" 
            value={takeProfit} 
            onChange={(e) => setTakeProfit(e.target.value)} 
            className="w-full bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-blue-500"
          />
        </div>

        <div>
          <label className="block text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5">Position Size (% of Capital)</label>
          <input 
            type="number" 
            value={sizePct} 
            onChange={(e) => setSizePct(e.target.value)} 
            className="w-full bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-blue-500"
          />
        </div>

        <div className="flex items-end">
          <button 
            type="submit" 
            disabled={loading} 
            className="w-full btn-primary flex items-center justify-center gap-2 text-xs font-semibold py-2.5"
          >
            {loading ? (
              <><RefreshCw className="w-3.5 h-3.5 animate-spin" />Simulating...</>
            ) : (
              <><Play className="w-3.5 h-3.5" />Run Backtest</>
            )}
          </button>
        </div>
      </form>

      {/* --- RESULTS DASHBOARD --- */}
      {result && (
        <div className="space-y-6 animate-fade-in">
          {/* Stats Row */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard 
              label="Net P&L" 
              value={`₹${result.metrics.total_pnl.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`}
              valueClass={pnlColor}
              sub={`${((result.metrics.final_capital - result.metrics.initial_capital) / result.metrics.initial_capital * 100).toFixed(2)}% return`}
            />
            <StatCard 
              label="Win Rate" 
              value={`${result.metrics.win_rate}%`}
              valueClass="text-blue-400"
              sub={`${result.metrics.total_trades} total trades`}
            />
            <StatCard 
              label="Max Drawdown" 
              value={`${result.metrics.max_drawdown_pct}%`}
              valueClass={ddColor}
              sub="Peak to trough drop"
            />
            <StatCard 
              label="Sharpe / Profit Factor" 
              value={`${result.metrics.sharpe_ratio.toFixed(2)} / ${result.metrics.profit_factor.toFixed(2)}`}
              valueClass="text-purple-400"
              sub="Risk-adjusted return ratio"
            />
          </div>

          {/* Equity Chart & Details */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Chart */}
            <div className="lg:col-span-2 card p-5 bg-gray-900/60 border border-gray-800/40">
              <h3 className="font-semibold text-sm text-gray-200 mb-4">Historical Equity Curve</h3>
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={result.equity_curve} margin={{ top: 5, right: 5, left: 10, bottom: 5 }}>
                    <defs>
                      <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor={lineClr} stopOpacity={0.2} />
                        <stop offset="95%" stopColor={lineClr} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                    <XAxis 
                      dataKey="time" 
                      tick={{ fill: "#6b7280", fontSize: 9 }}
                      axisLine={{ stroke: "#374151" }}
                      tickFormatter={(t) => t.split("T")[0]}
                    />
                    <YAxis 
                      tick={{ fill: "#6b7280", fontSize: 9 }}
                      axisLine={{ stroke: "#374151" }}
                      domain={["dataMin - 1000", "dataMax + 1000"]}
                      tickFormatter={(v) => `₹${v.toLocaleString("en-IN")}`}
                    />
                    <Tooltip 
                      contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: "8px", fontSize: "11px" }}
                      labelFormatter={(t) => `Time: ${new Date(t).toLocaleString("en-IN")}`}
                      formatter={(v: number) => [`₹${v.toLocaleString("en-IN")}`, "Portfolio Equity"]}
                    />
                    <Area 
                      type="monotone" 
                      dataKey="equity" 
                      stroke={lineClr} 
                      strokeWidth={2}
                      fill="url(#eqGrad)"
                      dot={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Performance Summary */}
            <div className="card p-5 bg-gray-900/60 border border-gray-800/40 flex flex-col justify-between">
              <div>
                <h3 className="font-semibold text-sm text-gray-200 mb-4">Backtest Parameters</h3>
                <div className="space-y-3.5 text-xs">
                  <div className="flex justify-between items-center py-1.5 border-b border-gray-800/60">
                    <span className="text-gray-400">Initial Account Value</span>
                    <span className="font-semibold text-gray-200">₹{result.metrics.initial_capital.toLocaleString("en-IN")}</span>
                  </div>
                  <div className="flex justify-between items-center py-1.5 border-b border-gray-800/60">
                    <span className="text-gray-400">Final Account Value</span>
                    <span className="font-semibold text-gray-200">₹{result.metrics.final_capital.toLocaleString("en-IN")}</span>
                  </div>
                  <div className="flex justify-between items-center py-1.5 border-b border-gray-800/60">
                    <span className="text-gray-400">Asset Under Simulation</span>
                    <span className="font-semibold text-gray-200">{result.symbol} ({result.interval})</span>
                  </div>
                  <div className="flex justify-between items-center py-1.5 border-b border-gray-800/60">
                    <span className="text-gray-400">Exit Target / Stop-Loss</span>
                    <span className="font-semibold text-gray-200">+{takeProfit}% / -{stopLoss}%</span>
                  </div>
                  <div className="flex justify-between items-center py-1.5">
                    <span className="text-gray-400">Size Per Trade</span>
                    <span className="font-semibold text-gray-200">{sizePct}% of Capital</span>
                  </div>
                </div>
              </div>

              <div className="mt-6 p-3 bg-blue-500/10 border border-blue-500/20 rounded-xl text-[11px] text-blue-300 leading-relaxed">
                🚀 <strong>Consensus Rule Optimization</strong>: Fine-tune SL/TP limits to achieve a profit factor &gt; 1.5 and drawdown &lt; 5% before scheduling actual execution routines.
              </div>
            </div>
          </div>

          {/* Trade logs */}
          <div className="card p-5 bg-gray-900/60 border border-gray-800/40">
            <h3 className="font-semibold text-sm text-gray-200 mb-4">Detailed Trade Execution Logs</h3>
            <div className="overflow-x-auto">
              {result.trades.length > 0 ? (
                <table className="w-full text-[11px] text-left">
                  <thead>
                    <tr className="border-b border-gray-800 text-gray-500 uppercase tracking-wider">
                      <th className="py-2.5">Date/Time (Entry)</th>
                      <th className="py-2.5">Symbol</th>
                      <th className="py-2.5">Type</th>
                      <th className="py-2.5">Entry Price</th>
                      <th className="py-2.5">Exit Price</th>
                      <th className="py-2.5">Qty / Size</th>
                      <th className="py-2.5">Exit Reason</th>
                      <th className="py-2.5 text-right">PnL (INR)</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-800/40 text-gray-300">
                    {result.trades.map((trade, idx) => {
                      const isLong = trade.direction === "LONG";
                      const won = trade.pnl >= 0;
                      return (
                        <tr key={idx} className="hover:bg-gray-800/10 transition-colors">
                          <td className="py-2.5 text-gray-400">{new Date(trade.entry_time).toLocaleString("en-IN")}</td>
                          <td className="py-2.5 font-bold">{trade.symbol}</td>
                          <td className="py-2.5">
                            <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${isLong ? "bg-green-500/10 text-green-400" : "bg-red-500/10 text-red-400"}`}>
                              {isLong ? "BUY" : "SELL"}
                            </span>
                          </td>
                          <td className="py-2.5">₹{trade.entry_price.toFixed(2)}</td>
                          <td className="py-2.5">₹{trade.exit_price.toFixed(2)}</td>
                          <td className="py-2.5 text-gray-400">{trade.shares} shares</td>
                          <td className="py-2.5">
                            <span className="px-1.5 py-0.5 rounded bg-gray-800/60 text-gray-400 text-[10px]">
                              {trade.exit_reason}
                            </span>
                          </td>
                          <td className={`py-2.5 text-right font-bold ${won ? "text-green-400" : "text-red-400"}`}>
                            {won ? "+" : ""}₹{trade.pnl.toLocaleString("en-IN", { maximumFractionDigits: 2 })} ({trade.pnl_pct >= 0 ? "+" : ""}{trade.pnl_pct.toFixed(2)}%)
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : (
                <div className="text-center py-8 text-gray-500 text-xs">
                  No trades were triggered during the backtest window under current consensus criteria.
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* --- INITIAL STATE BANNER --- */}
      {!result && !loading && (
        <div className="card p-12 text-center border-dashed border-2 border-gray-800/60 flex flex-col items-center justify-center space-y-3.5 bg-gray-900/15">
          <div className="w-12 h-12 rounded-full bg-blue-500/10 flex items-center justify-center">
            <BarChart className="w-6 h-6 text-blue-500" />
          </div>
          <div className="max-w-md">
            <h3 className="font-semibold text-gray-200 text-sm">No Simulation Run</h3>
            <p className="text-xs text-gray-500 leading-relaxed mt-1">
              Configure parameters above (like ticker target, timeframe interval, and stop/loss margins) and run the model simulation to generate trade logs and an interactive equity curve.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
