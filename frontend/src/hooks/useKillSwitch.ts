/**
 * frontend/src/hooks/useKillSwitch.ts
 *
 * Fetches the kill switch state once on mount via REST.
 *
 * WHY NEEDED?
 * The WebSocket only receives kill_switch CHANGE events.
 * If the system was already halted before we connected,
 * we'd never receive an event and would show "ACTIVE" incorrectly.
 *
 * This one-time REST call syncs the initial state.
 * The WebSocket keeps it up-to-date afterwards.
 */

"use client";

import { useEffect } from "react";
import api from "@/lib/api";
import { useTradingStore, useAuthStore } from "@/store";
import type { KillSwitchStatus } from "@/types";

export function useKillSwitch() {
  const setKillSwitch = useTradingStore((s) => s.setKillSwitch);
  const user = useAuthStore((s) => s.user);
  const isLoading = useAuthStore((s) => s.isLoading);

  useEffect(() => {
    if (isLoading || !user) return;

    api.get<KillSwitchStatus>("/api/v1/kill-switch/status")
      .then(({ data }) => setKillSwitch(data))
      .catch(() => setKillSwitch({ halted: false, status: "ACTIVE" }));
  }, [user, isLoading, setKillSwitch]);
}