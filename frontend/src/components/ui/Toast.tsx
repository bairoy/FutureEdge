/**
 * frontend/src/components/ui/Toast.tsx
 *
 * Toast notification and global Error Modal system.
 * Call showToast("message", "success"|"error"|"info") from anywhere.
 * Place <ToastContainer /> once in the root layout.
 */
"use client";

import { useEffect } from "react";
import { CheckCircle, XCircle, AlertCircle, X, AlertOctagon } from "lucide-react";
import { create } from "zustand";

interface Toast {
  id: string;
  message: string;
  type: "success" | "error" | "info";
}

interface ToastState {
  toasts: Toast[];
  add: (m: string, t: Toast["type"]) => void;
  remove: (id: string) => void;
}

const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  add: (message, type) =>
    set((s) => ({
      toasts: [...s.toasts, { id: Date.now().toString(), message, type }],
    })),
  remove: (id) =>
    set((s) => ({
      toasts: s.toasts.filter((t) => t.id !== id),
    })),
}));

export function showToast(message: string, type: Toast["type"] = "info") {
  useToastStore.getState().add(message, type);
}

// User-friendly descriptions for known backend error codes
const ERROR_DESCRIPTIONS: Record<string, { title: string; desc: string }> = {
  MARKET_CLOSED: {
    title: "Market is Closed",
    desc: "National Stock Exchange (NSE) is currently closed. Intraday (MIS) orders can only be executed during market hours: Monday to Friday, 09:15 AM to 03:30 PM IST. Please run again during market hours or switch to 'Test Mock' portfolio mode.",
  },
  KILL_SWITCH_ACTIVE: {
    title: "Trading halted by Kill Switch",
    desc: "A risk administrator has activated the global Kill Switch (TRADING_HALT). All new automated trade execution cycles are temporarily blocked. Contact your administrator to restore status.",
  },
  POSITION_TOO_SMALL: {
    title: "Calculated Position Size too Small",
    desc: "The calculated share allocation is 0 shares. This occurs when the portfolio equity or Kelly fraction sizing is too small relative to the share price. Try increasing your Rupees size override.",
  },
  HITL_NOT_APPROVED: {
    title: "HITL Approval Missing",
    desc: "This trade requires manual risk approval. The execution was paused because the required manager review decision was not received or was rejected.",
  },
};

// ── Standard Toast Item (for success/info) ───────────────────
function ToastItem({ toast, onRemove }: { toast: Toast; onRemove: () => void }) {
  useEffect(() => {
    const t = setTimeout(onRemove, 4000);
    return () => clearTimeout(t);
  }, [onRemove]);

  const cfg = {
    success: { Icon: CheckCircle, color: "text-green-400", bg: "bg-green-500/10 border-green-500/20" },
    info: { Icon: AlertCircle, color: "text-blue-400", bg: "bg-blue-500/10  border-blue-500/20" },
    error: { Icon: XCircle, color: "text-red-400", bg: "bg-red-500/10   border-red-500/20" }, // Fallback, not used
  }[toast.type];

  return (
    <div className={`flex items-start gap-3 p-3.5 rounded-xl border shadow-xl animate-slide-up ${cfg.bg}`}>
      <cfg.Icon className={`w-4 h-4 mt-0.5 shrink-0 ${cfg.color}`} />
      <p className="text-sm text-gray-150 flex-1">{toast.message}</p>
      <button onClick={onRemove} className="text-gray-500 hover:text-gray-300 transition-colors">
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

// ── Centered Error Modal (for errors) ─────────────────────────
function ErrorToastModal({ toast, onRemove }: { toast: Toast; onRemove: () => void }) {
  // Resolve user-friendly message details if it's a known error code
  const code = toast.message.replace("Error: ", "").trim();
  const detail = ERROR_DESCRIPTIONS[code] ?? {
    title: "Execution Error",
    desc: toast.message,
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/80 backdrop-blur-md transition-opacity" onClick={onRemove} />

      {/* Modal Box */}
      <div className="relative z-10 w-full max-w-md overflow-hidden rounded-2xl border border-red-500/25 bg-gray-905 shadow-2xl animate-in fade-in zoom-in-95 duration-200 bg-gray-900">
        
        {/* Header decoration */}
        <div className="h-1.5 bg-red-500 w-full" />

        <div className="p-6">
          <button
            onClick={onRemove}
            className="absolute top-4 right-4 rounded-lg p-1 text-gray-500 hover:bg-gray-800 hover:text-gray-105 transition-all duration-200"
            title="Close"
          >
            <X className="w-4.5 h-4.5" />
          </button>

          <div className="flex flex-col items-center text-center mt-2">
            <div className="w-14 h-14 rounded-full bg-red-500/10 flex items-center justify-center text-red-400 mb-4 shadow-inner">
              <AlertOctagon className="w-7 h-7" />
            </div>
            
            <h3 className="text-base font-bold text-gray-100 uppercase tracking-wider">
              {detail.title}
            </h3>
            
            <div className="mt-3 text-xs text-gray-400 leading-relaxed max-w-sm font-medium">
              {detail.desc}
            </div>
          </div>

          <div className="mt-6 flex gap-2">
            <button
              onClick={onRemove}
              className="w-full py-2.5 bg-gray-800 hover:bg-gray-700 active:bg-gray-600 text-gray-200 font-semibold text-xs rounded-xl border border-gray-750 transition-all duration-200 uppercase tracking-wider border-gray-700"
            >
              Dismiss Error
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Global Toast & Error Modal Container ─────────────────────
export function ToastContainer() {
  const { toasts, remove } = useToastStore();

  const errorToasts = toasts.filter((t) => t.type === "error");
  const otherToasts = toasts.filter((t) => t.type !== "error");

  return (
    <>
      {/* Top right notification stack for success / info */}
      <div className="fixed top-16 right-4 z-50 flex flex-col gap-2 w-80">
        {otherToasts.map((t) => (
          <ToastItem key={t.id} toast={t} onRemove={() => remove(t.id)} />
        ))}
      </div>

      {/* Centered overlays for errors */}
      {errorToasts.map((t) => (
        <ErrorToastModal key={t.id} toast={t} onRemove={() => remove(t.id)} />
      ))}
    </>
  );
}