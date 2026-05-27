/**
 * frontend/src/components/trading/KillSwitchButton.tsx
 *
 * Emergency kill switch shown in the dashboard header.
 *
 * ONLY VISIBLE TO: risk_manager and admin
 *
 * ACTIVE  → shows red "Halt Trading" button
 * HALTED  → shows green "Resume Trading" button (pulses to draw attention)
 *
 * ON CLICK:
 * ─────────────────────────────────────────────────────────
 * Halt   → POST /api/v1/kill-switch/halt
 *          Sets TRADING_HALT=1 in Redis.
 *          execution_agent checks this key before every order.
 *          All in-flight workflows are blocked at execution.
 *          Open positions are NOT auto-closed (separate action).
 *
 * Resume → POST /api/v1/kill-switch/resume
 *          Sets TRADING_HALT=0 in Redis.
 *          Next agent cycle can execute normally.
 *
 * The WebSocket broadcasts kill_switch events to all connected
 * dashboards so every logged-in user sees the status change
 * within ~100ms.
 */

"use client";

import { useState } from "react";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import api from "@/lib/api";
import { useTradingStore } from "@/store";

export function KillSwitchButton() {
  const { killSwitch, setKillSwitch } = useTradingStore();
  const [loading, setLoading] = useState(false);

  async function toggle() {
    if (!killSwitch.halted) {
      const ok = window.confirm(
        "⚠️ HALT ALL TRADING\n\n" +
        "This will block every new trade execution immediately.\n" +
        "Existing open positions will NOT be closed automatically.\n\n" +
        "Confirm?"
      );
      if (!ok) return;
    }

    setLoading(true);
    try {
      if (killSwitch.halted) {
        await api.post("/api/v1/kill-switch/resume");
        setKillSwitch({ halted: false, status: "ACTIVE" });
      } else {
        await api.post("/api/v1/kill-switch/halt", { reason: "Manual halt from dashboard" });
        setKillSwitch({ halted: true, status: "HALTED" });
      }
    } catch (err: any) {
      alert(err?.response?.data?.detail ?? "Failed to toggle kill switch");
    } finally {
      setLoading(false);
    }
  }

  return (
    <button
      onClick={toggle}
      disabled={loading}
      className={`
        flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium
        border transition-all duration-200 disabled:opacity-50
        ${killSwitch.halted
          ? "bg-green-500/10 border-green-500/30 text-green-400 hover:bg-green-500/20 animate-pulse-slow"
          : "bg-red-500/10   border-red-500/30   text-red-400   hover:bg-red-500/20"
        }
      `}
    >
      {killSwitch.halted
        ? <><ShieldCheck className="w-3.5 h-3.5" /> Resume Trading</>
        : <><ShieldAlert className="w-3.5 h-3.5" /> Halt Trading</>
      }
    </button>
  );
}