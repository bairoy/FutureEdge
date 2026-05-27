/**
 * frontend/src/app/dashboard/users/page.tsx
 *
 * User management page — visible and functional only for admin role.
 *
 * FEATURES:
 * ─────────────────────────────────────────────────────────
 * • List all users  — GET /users
 * • Create user     — POST /users  (email, password, name, role)
 * • Change role     — PUT /users/{id}/role
 * • Deactivate      — POST /users/{id}/deactivate
 * • Activate        — POST /users/{id}/activate
 *
 * ACCESS CONTROL:
 * ─────────────────────────────────────────────────────────
 * The sidebar hides this link from non-admins.
 * This page also does a client-side role check and renders
 * an access-denied screen if somehow reached by a non-admin.
 *
 * ROLE HIERARCHY (lowest → highest permissions):
 *   viewer → trader → risk_manager → admin
 */

"use client";

import { useState } from "react";
import useSWR from "swr";
import { format } from "date-fns";
import {
  UserPlus, CheckCircle, XCircle,
  ChevronDown, RefreshCw, Shield,
} from "lucide-react";
import api from "@/lib/api";
import { useAuthStore } from "@/store";
import type { UserProfile } from "@/types";

const fetcher = (url: string) => api.get(url).then((r) => r.data);

const ROLES = ["viewer", "trader", "risk_manager", "admin"] as const;

const ROLE_DESC: Record<string, string> = {
  viewer: "Read-only dashboard access",
  trader: "Can run agent workflow cycles",
  risk_manager: "Can approve/reject HITL + halt trading",
  admin: "Full access including user management",
};

const ROLE_BADGE: Record<string, string> = {
  admin: "text-purple-400 bg-purple-500/10 border border-purple-500/20",
  risk_manager: "text-blue-400   bg-blue-500/10   border border-blue-500/20",
  trader: "text-green-400  bg-green-500/10  border border-green-500/20",
  viewer: "text-gray-400   bg-gray-500/10   border border-gray-500/20",
};

export default function UsersPage() {
  const { user: me, isAdmin } = useAuthStore();

  const { data: users, isLoading, mutate } = useSWR<UserProfile[]>(
    isAdmin() ? "/users" : null,
    fetcher
  );

  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ email: "", password: "", full_name: "", role: "viewer" });
  const [creating, setCreating] = useState(false);
  const [createErr, setCreateErr] = useState<string | null>(null);
  const [roleLoading, setRoleLoading] = useState<string | null>(null);

  // ── Access guard ──────────────────────────────────────────
  if (!isAdmin()) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <Shield className="w-10 h-10 text-gray-700 mx-auto mb-3" />
          <p className="text-gray-400 font-medium">Access Denied</p>
          <p className="text-gray-600 text-sm mt-1">Admin role required</p>
        </div>
      </div>
    );
  }

  // ── Create user ───────────────────────────────────────────
  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setCreateErr(null);
    try {
      await api.post("/users", form);
      setShowCreate(false);
      setForm({ email: "", password: "", full_name: "", role: "viewer" });
      mutate();
    } catch (err: any) {
      setCreateErr(err?.response?.data?.detail ?? "Failed to create user");
    } finally {
      setCreating(false);
    }
  }

  // ── Change role ───────────────────────────────────────────
  async function changeRole(userId: string, role: string) {
    setRoleLoading(userId);
    try {
      await api.put(`/users/${userId}/role`, { role });
      mutate();
    } catch (err: any) {
      alert(err?.response?.data?.detail ?? "Failed to change role");
    } finally {
      setRoleLoading(null);
    }
  }

  // ── Toggle active ─────────────────────────────────────────
  async function toggleActive(u: UserProfile) {
    try {
      await api.post(`/users/${u.id}/${u.is_active ? "deactivate" : "activate"}`);
      mutate();
    } catch (err: any) {
      alert(err?.response?.data?.detail ?? "Failed to update user");
    }
  }

  return (
    <div className="space-y-5">

      {/* ── Header ──────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-gray-100">User Management</h1>
          <p className="text-xs text-gray-500 mt-0.5">Manage accounts and role-based access</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => mutate()} className="btn-ghost flex items-center gap-2 text-sm">
            <RefreshCw className="w-4 h-4" />Refresh
          </button>
          <button onClick={() => setShowCreate(!showCreate)} className="btn-primary flex items-center gap-2 text-sm">
            <UserPlus className="w-4 h-4" />New User
          </button>
        </div>
      </div>

      {/* ── Create form ─────────────────────────────────────── */}
      {showCreate && (
        <div className="card p-5 animate-slide-up">
          <h2 className="font-semibold text-gray-100 mb-4">Create New User</h2>

          {createErr && (
            <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
              {createErr}
            </div>
          )}

          <form onSubmit={handleCreate} className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-gray-400 mb-1.5">Full Name</label>
              <input value={form.full_name}
                onChange={(e) => setForm({ ...form, full_name: e.target.value })}
                placeholder="Baiju Yadav" className="input" required />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1.5">Email</label>
              <input type="email" value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                placeholder="baiju@example.com" className="input" required />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1.5">Password</label>
              <input type="password" value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                placeholder="Min 8 characters" className="input" required minLength={8} />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1.5">Role</label>
              <select value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
                className="input">
                {ROLES.map((r) => (
                  <option key={r} value={r}>{r.replace("_", " ")} — {ROLE_DESC[r]}</option>
                ))}
              </select>
            </div>
            <div className="sm:col-span-2 flex justify-end gap-3">
              <button type="button" onClick={() => setShowCreate(false)} className="btn-ghost">Cancel</button>
              <button type="submit" disabled={creating} className="btn-primary">
                {creating ? "Creating…" : "Create User"}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* ── Users table ─────────────────────────────────────── */}
      <div className="card overflow-hidden">
        {isLoading ? (
          <div className="p-10 text-center text-gray-500 text-sm">Loading users…</div>
        ) : !users || users.length === 0 ? (
          <div className="p-10 text-center text-gray-500 text-sm">No users found.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                {["User", "Role", "Status", "Last Login", "Joined", "Actions"].map((h) => (
                  <th key={h} className="px-4 py-3 text-left section-label">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {users.map((u) => (
                <tr key={u.id} className="hover:bg-gray-800/20 transition-colors">

                  {/* User info */}
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-full bg-gray-700 flex items-center justify-center shrink-0">
                        <span className="text-xs font-bold text-gray-300">
                          {u.full_name.charAt(0).toUpperCase()}
                        </span>
                      </div>
                      <div>
                        <p className="font-medium text-gray-100 flex items-center gap-1.5">
                          {u.full_name}
                          {u.id === me?.id && (
                            <span className="text-xs text-gray-600">(you)</span>
                          )}
                        </p>
                        <p className="text-xs text-gray-500">{u.email}</p>
                      </div>
                    </div>
                  </td>

                  {/* Role — dropdown for others, badge for self */}
                  <td className="px-4 py-3">
                    {u.id === me?.id ? (
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${ROLE_BADGE[u.role]}`}>
                        {u.role.replace("_", " ")}
                      </span>
                    ) : (
                      <div className="relative">
                        <select
                          value={u.role}
                          disabled={roleLoading === u.id}
                          onChange={(e) => changeRole(u.id, e.target.value)}
                          className={`text-xs font-medium px-2 py-1 pr-6 rounded appearance-none
                            cursor-pointer bg-transparent border focus:outline-none focus:ring-1
                            focus:ring-blue-500 ${ROLE_BADGE[u.role]}`}>
                          {ROLES.map((r) => (
                            <option key={r} value={r} className="bg-gray-900 text-gray-100">
                              {r.replace("_", " ")}
                            </option>
                          ))}
                        </select>
                        <ChevronDown className="absolute right-1 top-1/2 -translate-y-1/2 w-3 h-3 pointer-events-none opacity-50" />
                      </div>
                    )}
                  </td>

                  {/* Status */}
                  <td className="px-4 py-3">
                    {u.is_active
                      ? <span className="flex items-center gap-1.5 text-green-400 text-xs"><CheckCircle className="w-3.5 h-3.5" />Active</span>
                      : <span className="flex items-center gap-1.5 text-red-400   text-xs"><XCircle className="w-3.5 h-3.5" />Inactive</span>
                    }
                  </td>

                  {/* Last login */}
                  <td className="px-4 py-3 text-gray-500 text-xs">
                    {u.last_login_at ? format(new Date(u.last_login_at), "dd MMM, HH:mm") : "Never"}
                  </td>

                  {/* Joined */}
                  <td className="px-4 py-3 text-gray-500 text-xs">
                    {format(new Date(u.created_at), "dd MMM yyyy")}
                  </td>

                  {/* Actions */}
                  <td className="px-4 py-3">
                    {u.id !== me?.id && (
                      <button onClick={() => toggleActive(u)}
                        className={`text-xs px-2.5 py-1 rounded-lg border transition-colors ${u.is_active
                            ? "border-red-500/30   text-red-400   hover:bg-red-500/10"
                            : "border-green-500/30 text-green-400 hover:bg-green-500/10"
                          }`}>
                        {u.is_active ? "Deactivate" : "Activate"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Role legend ─────────────────────────────────────── */}
      <div className="card p-4">
        <p className="section-label mb-3">Role Permissions</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {ROLES.map((role) => (
            <div key={role} className="flex items-start gap-2.5">
              <span className={`mt-0.5 px-2 py-0.5 rounded text-xs font-medium shrink-0 ${ROLE_BADGE[role]}`}>
                {role.replace("_", " ")}
              </span>
              <span className="text-xs text-gray-500">{ROLE_DESC[role]}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}