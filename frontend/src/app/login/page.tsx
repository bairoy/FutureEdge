/**
 * frontend/src/app/login/page.tsx
 *
 * Login page — POST /auth/login → store tokens → redirect to /dashboard.
 */
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { TrendingUp, Mail, Lock, AlertCircle } from "lucide-react";
import api, { tokenStore } from "@/lib/api";
import { useAuthStore } from "@/store";
import type { TokenResponse, UserProfile } from "@/types";

export default function LoginPage() {
  const router = useRouter();
  const setUser = useAuthStore((s) => s.setUser);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const { data: tokens } = await api.post<TokenResponse>("/auth/login", { email, password });
      tokenStore.setTokens(tokens.access_token, tokens.refresh_token);
      const { data: profile } = await api.get<UserProfile>("/auth/me");
      setUser(profile);
      router.push("/dashboard");
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Invalid email or password.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center p-4">
      <div className="w-full max-w-md">

        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-blue-600/10 border border-blue-500/20 mb-4">
            <TrendingUp className="w-7 h-7 text-blue-400" />
          </div>
          <h1 className="text-2xl font-bold text-gray-100">FutureEdge</h1>
          <p className="text-gray-500 mt-1 text-sm">AI Trading System — NSE/BSE via Zerodha</p>
        </div>

        <div className="card p-8">
          <h2 className="text-lg font-semibold text-gray-100 mb-6">Sign in</h2>

          {error && (
            <div className="flex items-start gap-2.5 p-3 mb-5 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm animate-slide-up">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm text-gray-400 mb-1.5">Email</label>
              <div className="relative">
                <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com" className="input pl-10" required autoComplete="email" />
              </div>
            </div>

            <div>
              <label className="block text-sm text-gray-400 mb-1.5">Password</label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••" className="input pl-10" required autoComplete="current-password" />
              </div>
            </div>

            <button type="submit" disabled={loading || !email || !password} className="btn-primary w-full mt-1">
              {loading
                ? <span className="flex items-center justify-center gap-2">
                  <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Signing in...
                </span>
                : "Sign in"
              }
            </button>
          </form>

          <p className="text-center text-xs text-gray-600 mt-5">
            Contact your administrator to create an account.
          </p>
        </div>
      </div>
    </div>
  );
}