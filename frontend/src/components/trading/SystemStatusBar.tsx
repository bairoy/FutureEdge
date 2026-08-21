/**
 * frontend/src/components/trading/SystemStatusBar.tsx
 *
 * Header strip that answers two questions at a glance:
 *
 *   WHERE ARE PRICES COMING FROM?   (feed  — read-only, no money at risk)
 *   WHERE DO ORDERS GO?             (broker — real money when set to Zerodha)
 *
 * WHY THESE ARE SEPARATE CONTROLS:
 * ─────────────────────────────────────────────────────────────
 * The previous header had a single "Enable Paper Trading" toggle wired only
 * to the broker, and hid the Connect Zerodha button unless the broker was
 * already Zerodha. That combination made the most useful configuration —
 * live Zerodha feed + mock broker — impossible to reach from the UI, and
 * gave no way to tell whether on-screen prices were realtime NSE ticks or
 * 15-minute-delayed Yahoo data.
 *
 * So: the feed chip is pure status (the backend reports what is actually
 * serving prices, not what is configured), the broker chip is the switch,
 * and Connect Zerodha stands on its own because it is a prerequisite for
 * the live feed regardless of which broker is selected.
 *
 * SAFETY:
 * ─────────────────────────────────────────────────────────────
 * Switching the broker to Zerodha means live NSE orders against real
 * capital, so it requires explicit confirmation and is styled red. Every
 * other control here is non-destructive.
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Radio, Wallet, AlertTriangle, Link2 } from "lucide-react";

import api from "@/lib/api";
import { showToast } from "@/components/ui/Toast";
import type { SystemStatus } from "@/types";

const POLL_INTERVAL_MS = 15_000;

export function SystemStatusBar({ canSwitchBroker }: { canSwitchBroker: boolean }) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [confirmLive, setConfirmLive] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const { data } = await api.get<SystemStatus>("/api/v1/system/status");
      setStatus(data);
    } catch {
      // Non-fatal: the strip simply keeps its last known state rather than
      // flashing an error into the header on a single dropped poll.
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  async function connectZerodha() {
    setConnecting(true);
    try {
      const { data } = await api.get("/auth/zerodha/login-url");
      if (data.login_url) {
        window.location.href = data.login_url;
      } else {
        showToast("Zerodha API key not configured on the backend", "error");
      }
    } catch {
      showToast("Could not start Zerodha login", "error");
    } finally {
      setConnecting(false);
    }
  }

  async function setBroker(mode: "mock" | "zerodha") {
    setSwitching(true);
    try {
      await api.post("/auth/broker/select", { broker: mode });
      // Kept in sync so a page reload does not silently flip the broker back.
      localStorage.setItem("fe-paper-trade", String(mode === "mock"));
      showToast(
        mode === "mock"
          ? "Paper broker active — orders are simulated"
          : "LIVE broker active — orders hit your real Zerodha account",
        mode === "mock" ? "success" : "error",
      );
      await refresh();
    } catch {
      showToast("Failed to switch broker", "error");
    } finally {
      setSwitching(false);
      setConfirmLive(false);
    }
  }

  if (!status) {
    return (
      <div className="flex items-center gap-2 px-3 py-1.5 text-xs text-gray-500">
        <RefreshCw className="w-3 h-3 animate-spin" />
        Checking status…
      </div>
    );
  }

  const { feed, broker, zerodha } = status;

  return (
    <>
      <div className="flex items-center gap-2">

        {/* ── FEED (status only) ──────────────────────────── */}
        <div
          title={feed.detail}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-semibold cursor-help
            ${feed.is_live
              ? "border-green-500/30 bg-green-500/10 text-green-400"
              : "border-amber-500/30 bg-amber-500/10 text-amber-400"}`}
        >
          <Radio className="w-3.5 h-3.5 shrink-0" />
          <span className="text-gray-500 font-medium uppercase tracking-wide">Feed</span>
          <span className="flex items-center gap-1.5">
            <span
              className={`w-1.5 h-1.5 rounded-full shrink-0
                ${feed.is_live ? "bg-green-500 animate-pulse" : "bg-amber-500"}`}
            />
            {feed.is_live ? "Zerodha Live" : "Yahoo · delayed"}
          </span>
        </div>

        {/* ── BROKER (the switch) ─────────────────────────── */}
        <div
          title={broker.detail}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-semibold
            ${broker.is_live
              ? "border-red-500/40 bg-red-500/10 text-red-400"
              : "border-blue-500/30 bg-blue-500/10 text-blue-400"}`}
        >
          <Wallet className="w-3.5 h-3.5 shrink-0" />
          <span className="text-gray-500 font-medium uppercase tracking-wide">Broker</span>

          {canSwitchBroker ? (
            <div className="flex items-center rounded-md bg-gray-950/60 p-0.5">
              <button
                type="button"
                disabled={switching}
                onClick={() => broker.is_live && setBroker("mock")}
                className={`px-2 py-0.5 rounded text-[11px] font-bold transition-colors
                  ${broker.is_paper
                    ? "bg-blue-600 text-white"
                    : "text-gray-500 hover:text-gray-300"}`}
              >
                PAPER
              </button>
              <button
                type="button"
                disabled={switching}
                onClick={() => broker.is_paper && setConfirmLive(true)}
                className={`px-2 py-0.5 rounded text-[11px] font-bold transition-colors
                  ${broker.is_live
                    ? "bg-red-600 text-white"
                    : "text-gray-500 hover:text-gray-300"}`}
              >
                LIVE
              </button>
            </div>
          ) : (
            <span>{broker.is_live ? "Zerodha · LIVE" : "Paper (Mock)"}</span>
          )}
        </div>

        {/* ── ZERODHA CONNECTION ──────────────────────────── */}
        {/* Always rendered when credentials exist — connecting is what makes
            the live feed possible, independent of the selected broker. */}
        {zerodha.configured && (
          <button
            onClick={connectZerodha}
            disabled={connecting}
            title={
              zerodha.connected
                ? "Zerodha session active. Tokens expire 6 AM IST daily — click to re-authenticate."
                : "Connect Zerodha to enable the live NSE tick feed."
            }
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-semibold transition-all
              ${zerodha.connected
                ? "border-gray-700 bg-gray-800/50 text-gray-400 hover:text-gray-200 hover:border-gray-600"
                : "border-amber-500/40 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 animate-pulse"}`}
          >
            {connecting
              ? <RefreshCw className="w-3.5 h-3.5 animate-spin shrink-0" />
              : <Link2 className="w-3.5 h-3.5 shrink-0" />}
            {zerodha.connected ? "Zerodha ✓" : "Connect Zerodha"}
          </button>
        )}
      </div>

      {/* ── LIVE-BROKER CONFIRMATION ──────────────────────── */}
      {confirmLive && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="w-full max-w-md rounded-xl border border-red-500/40 bg-gray-900 p-6">
            <div className="flex items-start gap-3">
              <AlertTriangle className="w-6 h-6 text-red-400 shrink-0 mt-0.5" />
              <div>
                <h2 className="text-lg font-bold text-gray-100">Switch to the live broker?</h2>
                <p className="mt-2 text-sm text-gray-400 leading-relaxed">
                  Orders will be placed on your <strong className="text-red-400">real
                  Zerodha account</strong> using real capital. Approved trades execute
                  immediately against NSE.
                </p>
                <p className="mt-2 text-sm text-gray-500">
                  This does not change your data feed — only where orders go.
                </p>
              </div>
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <button
                onClick={() => setConfirmLive(false)}
                className="px-4 py-2 rounded-lg text-sm font-semibold text-gray-300 hover:bg-gray-800"
              >
                Stay on paper
              </button>
              <button
                onClick={() => setBroker("zerodha")}
                disabled={switching}
                className="px-4 py-2 rounded-lg bg-red-600 text-sm font-bold text-white hover:bg-red-500 disabled:opacity-50"
              >
                {switching ? "Switching…" : "Yes, go live"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
