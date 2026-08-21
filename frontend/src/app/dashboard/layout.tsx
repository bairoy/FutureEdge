/**
 * frontend/src/app/dashboard/layout.tsx
 *
 * Dashboard shell — wraps every dashboard page with:
 * ─────────────────────────────────────────────────────────
 * • Sidebar  with role-filtered navigation links
 * • Header   with breadcrumb and kill-switch button
 * • Auth guard — restores session from cookie on page load,
 *               redirects to /login if session invalid
 * • WebSocket connection (started once, shared by all pages
 *   via the Zustand store)
 * • Kill switch sync (one REST call on mount)
 * • HITL modal overlay (appears when store.hitlPending != null)
 *
 * AUTH FLOW ON PAGE LOAD:
 * ─────────────────────────────────────────────────────────
 * 1. useEffect checks for the "fe_refresh" cookie
 * 2. If found → POST /auth/refresh → new access_token in memory
 * 3. GET /auth/me → loads UserProfile into Zustand
 * 4. If anything fails → clearTokens → redirect to /login
 */

"use client";

import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import {
  LayoutDashboard, TrendingUp, Users, Activity,
  LogOut, BarChart2, ExternalLink, LineChart, Landmark
} from "lucide-react";

import api, { tokenStore } from "@/lib/api";
import { useAuthStore, useTradingStore } from "@/store";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useKillSwitch } from "@/hooks/useKillSwitch";
import { KillSwitchButton } from "@/components/trading/KillSwitchButton";
import { SystemStatusBar } from "@/components/trading/SystemStatusBar";
import { HITLModal } from "@/components/trading/HITLModal";
import { showToast } from "@/components/ui/Toast";
import type { UserProfile } from "@/types";

// ─── NAV CONFIG ──────────────────────────────────────────────
const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, minRole: "viewer" },
  { href: "/dashboard/trades", label: "Trades", icon: TrendingUp, minRole: "viewer" },
  // Investing is advisory and places no orders, so it is readable by anyone
  // who can see the dashboard. Running an analysis is gated at trader inside
  // the page, matching the backend.
  { href: "/dashboard/investing", label: "Investing", icon: Landmark, minRole: "viewer" },
  { href: "/dashboard/backtest", label: "Backtest", icon: LineChart, minRole: "trader" },
  { href: "/dashboard/users", label: "Users", icon: Users, minRole: "admin" },
];

const ROLE_ORDER = ["viewer", "trader", "risk_manager", "admin"];
const hasRole = (u: string, m: string) => ROLE_ORDER.indexOf(u) >= ROLE_ORDER.indexOf(m);

const ROLE_COLOR: Record<string, string> = {
  admin: "text-purple-400", risk_manager: "text-blue-400",
  trader: "text-green-400", viewer: "text-gray-400",
};

// A module-level promise to deduplicate concurrent session restore calls
// (e.g. from React Strict Mode double-mounts in development)
let restorePromise: Promise<UserProfile | null> | null = null;

// ─── LAYOUT ──────────────────────────────────────────────────
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();

  const { user, setUser, setLoading, isLoading } = useAuthStore();
  const hitlPending = useTradingStore((s) => s.hitlPending);
  const setPaperTrade = useTradingStore((s) => s.setPaperTrade);

  // Announce a completed Zerodha OAuth round trip.
  //
  // This used to ALSO push the broker mode to the backend on every mount,
  // reading localStorage("fe-paper-trade") and POSTing /auth/broker/select.
  // That silently enabled the LIVE broker whenever a stale `false` was left
  // in localStorage — a page refresh could arm real-money trading with no
  // confirmation, and it rewrote ACTIVE_BROKER in .env as a side effect.
  // The backend is now the single source of truth for broker mode;
  // SystemStatusBar reports it and only changes it on explicit user action.
  useEffect(() => {
    if (isLoading || typeof window === "undefined") return;

    const params = new URLSearchParams(window.location.search);
    if (params.get("zerodha") === "success") {
      showToast("Zerodha connected — live feed enabled", "success");
      window.history.replaceState({}, "", window.location.pathname);
    }

    // Keep the local store in step with the persisted preference for any
    // component still reading it, without pushing it to the server.
    setPaperTrade(localStorage.getItem("fe-paper-trade") !== "false");
  }, [isLoading, setPaperTrade]);

  // Start WebSocket + sync kill switch state
  useWebSocket();
  useKillSwitch();

  // Session restore on mount
  useEffect(() => {
    const hasToken = !!tokenStore.getAccessToken();
    if (user && hasToken) {
      setLoading(false);
      return;
    }

    const rt = tokenStore.getRefreshToken();
    if (!rt) {
      setLoading(false);
      // Only redirect if we don't even have a persisted user
      if (!user) router.replace("/login");
      return;
    }

    if (!restorePromise) {
      restorePromise = (async () => {
        const MAX_RETRIES = 2;
        const DELAYS = [500, 1500]; // ms between retries

        for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
          try {
            // 1. Get a fresh access token using the refresh cookie
            const { data: tokens } = await api.post("/auth/refresh", { refresh_token: rt });
            tokenStore.setTokens(tokens.access_token, tokens.refresh_token);

            // 2. Load the actual user object (syncs any role changes etc)
            const { data: profile } = await api.get<UserProfile>("/auth/me");
            return profile;
          } catch (err: any) {
            // Retry on 5xx/network errors (e.g. ECONNRESET during backend hot-reload)
            // but NOT on 401/403 (invalid token → must re-login)
            const status = err?.response?.status;
            const isRetryable = !status || status >= 500;

            if (isRetryable && attempt < MAX_RETRIES) {
              console.warn(
                `[Auth] Session restore attempt ${attempt + 1} failed (${status ?? "network"}), ` +
                `retrying in ${DELAYS[attempt]}ms…`
              );
              await new Promise((r) => setTimeout(r, DELAYS[attempt]));
              continue;
            }

            console.error("[Auth] Session restore failed:", err);
            tokenStore.clearTokens();
            return null;
          }
        }
        return null;
      })();
    }

    (async () => {
      try {
        const profile = await restorePromise;
        if (profile) {
          setUser(profile);
        } else {
          setUser(null);
          router.replace("/login");
        }
      } finally {
        setLoading(false);
        // Clear the promise so that future mounts can restore session if needed
        restorePromise = null;
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function logout() {
    try {
      const rt = tokenStore.getRefreshToken();
      if (rt) await api.post("/auth/logout", { refresh_token: rt });
    } finally {
      tokenStore.clearTokens();
      setUser(null);
      router.replace("/login");
    }
  }

  // Loading skeleton
  if (isLoading) return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center">
      <div className="flex items-center gap-3 text-gray-500">
        <div className="w-5 h-5 border-2 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
        <span className="text-sm">Loading…</span>
      </div>
    </div>
  );

  if (!user) return null;

  return (
    <div className="min-h-screen bg-gray-950 flex">

      {/* ── SIDEBAR ─────────────────────────────────────────── */}
      <aside className="w-56 shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col">

        {/* Logo */}
        <div className="h-14 flex items-center px-4 border-b border-gray-800">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-blue-600 flex items-center justify-center">
              <Activity className="w-3.5 h-3.5 text-white" />
            </div>
            <span className="font-bold text-gray-100">FutureEdge</span>
          </div>
        </div>

        {/* Nav links */}
        <nav className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto">
          <p className="section-label px-2 mb-2">Navigation</p>

          {NAV.filter((n) => hasRole(user.role, n.minRole)).map((item) => {
            const active = pathname === item.href || pathname.startsWith(item.href + "/");
            return (
              <Link key={item.href} href={item.href}
                className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium
                  transition-colors ${active
                    ? "bg-blue-600/15 text-blue-400"
                    : "text-gray-400 hover:text-gray-100 hover:bg-gray-800"
                  }`}>
                <item.icon className="w-4 h-4 shrink-0" />
                {item.label}
              </Link>
            );
          })}

          <a href="http://localhost:8000/docs" target="_blank" rel="noopener noreferrer"
            className="flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium
              text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors">
            <BarChart2 className="w-4 h-4 shrink-0" />
            API Docs
            <ExternalLink className="w-3 h-3 ml-auto opacity-50" />
          </a>
        </nav>

        {/* User card */}
        <div className="p-2 border-t border-gray-800">
          <div className="flex items-center gap-2.5 px-2 py-2 rounded-lg">
            <div className="w-8 h-8 rounded-full bg-gray-700 flex items-center justify-center shrink-0">
              <span className="text-xs font-bold text-gray-300">
                {user.full_name.charAt(0).toUpperCase()}
              </span>
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-gray-100 truncate">{user.full_name}</p>
              <p className={`text-xs capitalize ${ROLE_COLOR[user.role] ?? "text-gray-500"}`}>
                {user.role.replace("_", " ")}
              </p>
            </div>
            <button onClick={logout} title="Sign out"
              className="p-1.5 rounded text-gray-500 hover:text-gray-100 hover:bg-gray-700 transition-colors">
              <LogOut className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </aside>

      {/* ── MAIN AREA ───────────────────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

        {/* Header */}
        <header className="h-14 shrink-0 bg-gray-900 border-b border-gray-800
                           flex items-center justify-between px-5">
          <span className="text-sm font-medium text-gray-100 capitalize">
            {pathname.split("/").filter(Boolean).pop()?.replace("-", " ") ?? "Dashboard"}
          </span>
          <div className="flex items-center gap-3">
            {/* Feed and broker are reported and controlled separately —
                see components/trading/SystemStatusBar.tsx for why. */}
            <SystemStatusBar canSwitchBroker={hasRole(user.role, "risk_manager")} />
            {hasRole(user.role, "risk_manager") && <KillSwitchButton />}
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-auto p-5">
          {children}
        </main>
      </div>

      {/* HITL modal — on top of everything */}
      {hitlPending && <HITLModal />}
    </div>
  );
}