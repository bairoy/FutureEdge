/**
 * frontend/src/components/ui/Toast.tsx
 *
 * Toast notification system.
 * Call showToast("message", "success"|"error"|"info") from anywhere.
 * Place <ToastContainer /> once in the root layout.
 * Toasts auto-dismiss after 4 seconds.
 */
"use client";

import { useEffect } from "react";
import { CheckCircle, XCircle, AlertCircle, X } from "lucide-react";
import { create } from "zustand";

interface Toast { id: string; message: string; type: "success" | "error" | "info"; }

interface ToastState {
  toasts: Toast[];
  add: (m: string, t: Toast["type"]) => void;
  remove: (id: string) => void;
}

const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  add: (message, type) => set((s) => ({ toasts: [...s.toasts, { id: Date.now().toString(), message, type }] })),
  remove: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

export function showToast(message: string, type: Toast["type"] = "info") {
  useToastStore.getState().add(message, type);
}

function ToastItem({ toast, onRemove }: { toast: Toast; onRemove: () => void }) {
  useEffect(() => { const t = setTimeout(onRemove, 4000); return () => clearTimeout(t); }, [onRemove]);

  const cfg = {
    success: { Icon: CheckCircle, color: "text-green-400", bg: "bg-green-500/10 border-green-500/20" },
    error: { Icon: XCircle, color: "text-red-400", bg: "bg-red-500/10   border-red-500/20" },
    info: { Icon: AlertCircle, color: "text-blue-400", bg: "bg-blue-500/10  border-blue-500/20" },
  }[toast.type];

  return (
    <div className={`flex items-start gap-3 p-3.5 rounded-xl border shadow-xl animate-slide-up ${cfg.bg}`}>
      <cfg.Icon className={`w-4 h-4 mt-0.5 shrink-0 ${cfg.color}`} />
      <p className="text-sm text-gray-100 flex-1">{toast.message}</p>
      <button onClick={onRemove} className="text-gray-500 hover:text-gray-300 transition-colors">
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

export function ToastContainer() {
  const { toasts, remove } = useToastStore();
  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 w-80">
      {toasts.map((t) => <ToastItem key={t.id} toast={t} onRemove={() => remove(t.id)} />)}
    </div>
  );
}