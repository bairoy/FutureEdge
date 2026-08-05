/**
 * frontend/src/lib/api.ts
 *
 * Centralised axios instance with automatic JWT token refresh.
 *
 * HOW TOKEN REFRESH WORKS:
 * ─────────────────────────
 * Access tokens expire after 30 minutes (configurable in backend).
 * When any API call returns 401, this interceptor:
 *   1. Calls POST /auth/refresh with the stored refresh_token
 *   2. Stores the new access_token in memory
 *   3. Retries the original failed request with the new token
 *   4. If refresh also fails → clears all tokens → redirects to /login
 *
 * TOKEN STORAGE SECURITY:
 * ──────────────────────────
 * access_token  → JS variable (memory only, lost on page refresh, XSS-safe)
 * refresh_token → browser cookie (survives refresh, readable by JS)
 *
 * On page load, the dashboard layout calls /auth/refresh using the
 * cookie to get a new access_token back into memory.
 */

import axios, { AxiosError, InternalAxiosRequestConfig } from "axios";
import Cookies from "js-cookie";

// ─── TOKEN STORAGE ────────────────────────────────────────────

// Access token lives only in memory — never in localStorage
let _accessToken: string | null = null;

const REFRESH_COOKIE_NAME = "fe_refresh"; // 'fe' = FutureEdge

export const tokenStore = {
  setTokens(access: string, refresh: string) {
    _accessToken = access;
    // Cookie: 7-day expiry, SameSite=Lax allows cookie on page reload/navigation,
    // path="/" ensures availability across all routes
    Cookies.set(REFRESH_COOKIE_NAME, refresh, { expires: 7, sameSite: "lax", path: "/" });
  },

  getAccessToken: (): string | null => _accessToken,

  getRefreshToken: (): string | null => Cookies.get(REFRESH_COOKIE_NAME) ?? null,

  clearTokens() {
    _accessToken = null;
    Cookies.remove(REFRESH_COOKIE_NAME, { path: "/" });
  },
};

// ─── AXIOS INSTANCE ───────────────────────────────────────────

// baseURL is empty — Next.js rewrites handle proxying to backend
const api = axios.create({
  baseURL: "",
  headers: { "Content-Type": "application/json" },
  timeout: 45000,
});

// ─── REQUEST INTERCEPTOR — attach access token ────────────────

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = tokenStore.getAccessToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// ─── RESPONSE INTERCEPTOR — auto-refresh on 401 ───────────────

let isRefreshing = false;
let pendingQueue: Array<{ resolve: (v: unknown) => void; reject: (e: unknown) => void }> = [];

const flushQueue = (error: Error | null, token: string | null) => {
  pendingQueue.forEach(({ resolve, reject }) => (error ? reject(error) : resolve(token)));
  pendingQueue = [];
};

api.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const original = error.config as InternalAxiosRequestConfig & { _retry?: boolean };

    // Only handle 401 once per request, and not on the refresh call itself
    if (
      error.response?.status !== 401 ||
      original._retry ||
      original.url?.includes("/auth/refresh")
    ) {
      return Promise.reject(error);
    }

    original._retry = true;

    if (isRefreshing) {
      // Queue this request while a refresh is already in-flight
      return new Promise((resolve, reject) => {
        pendingQueue.push({ resolve, reject });
      }).then((token) => {
        original.headers.Authorization = `Bearer ${token}`;
        return api(original);
      });
    }

    isRefreshing = true;

    try {
      const rt = tokenStore.getRefreshToken();
      if (!rt) throw new Error("No refresh token");

      const { data } = await axios.post("/auth/refresh", { refresh_token: rt });
      tokenStore.setTokens(data.access_token, data.refresh_token);
      flushQueue(null, data.access_token);
      original.headers.Authorization = `Bearer ${data.access_token}`;
      return api(original);
    } catch (e) {
      flushQueue(e as Error, null);
      tokenStore.clearTokens();
      if (typeof window !== "undefined") window.location.href = "/login";
      return Promise.reject(e);
    } finally {
      isRefreshing = false;
    }
  }
);

export default api;