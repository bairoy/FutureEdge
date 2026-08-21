/**
 * frontend/src/components/investing/HoldingControl.tsx
 *
 * Record or remove a long-term position.
 *
 * THIS IS A RECORD OF INTENT, NOT A BROKER RECONCILIATION:
 * ──────────────────────────────────────────────────────────
 * Investing mode never touches the broker. You bought the shares yourself; this
 * tells the system you did. It matters because ownership is an input to the
 * stance matrix — BUY and ADD, WATCH and HOLD are the same numbers answered
 * differently depending only on whether the position already exists. Without
 * this register half the matrix is unreachable.
 */

"use client";

import { useState } from "react";
import { Trash2, Wallet } from "lucide-react";
import api from "@/lib/api";
import { showToast } from "@/components/ui/Toast";
import type { Thesis } from "@/types";

interface Props {
  symbol: string;
  owned: Thesis["owned"];
  onChange: () => void;
}

export function HoldingControl({ symbol, owned, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [qty, setQty] = useState(owned ? String(owned.quantity) : "");
  const [price, setPrice] = useState(owned ? String(owned.avg_buy_price) : "");
  const [buyDate, setBuyDate] = useState(owned?.buy_date ?? new Date().toISOString().slice(0, 10));
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const { data } = await api.post("/api/v1/investing/holdings", {
        symbol,
        quantity: Number(qty),
        avg_buy_price: Number(price),
        buy_date: buyDate,
        notes: notes.trim() || null,
      });
      // The backend warns rather than refuses when a symbol is on both the
      // trading and investing lists — a long-term holding plus an intraday
      // short can be treated by the broker as a delivery sell. Surfacing it
      // is the entire value of the warning.
      if (data?.warning) showToast(data.warning, "error");
      else showToast(`${symbol} recorded — the stance re-derives from it`, "success");
      setOpen(false);
      onChange();
    } catch (err: any) {
      showToast(err?.response?.data?.detail ?? "Could not record the holding", "error");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.delete(`/api/v1/investing/holdings/${symbol}`);
      showToast(`${symbol} removed from the register`, "info");
      onChange();
    } catch (err: any) {
      showToast(err?.response?.data?.detail ?? "Could not remove the holding", "error");
    } finally {
      setBusy(false);
    }
  }

  if (owned && !open) {
    return (
      <div className="flex items-center gap-2">
        <button onClick={() => setOpen(true)} className="btn-ghost flex items-center gap-1.5">
          <Wallet className="w-3.5 h-3.5" /> Edit holding
        </button>
        <button onClick={remove} disabled={busy} title="Remove from register"
          className="btn-ghost !px-2 hover:text-red-400">
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
    );
  }

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="btn-ghost flex items-center gap-1.5">
        <Wallet className="w-3.5 h-3.5" /> I hold this
      </button>
    );
  }

  return (
    <form onSubmit={save} className="card p-4 w-full">
      <p className="section-label mb-3">
        {owned ? "Update" : "Record"} your {symbol} position
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <div>
          <label className="section-label">Quantity</label>
          <input className="input mt-1" type="number" min="1" value={qty}
                 onChange={(e) => setQty(e.target.value)} required />
        </div>
        <div>
          <label className="section-label">Avg buy price</label>
          <input className="input mt-1" type="number" step="0.01" min="0.01" value={price}
                 onChange={(e) => setPrice(e.target.value)} required />
        </div>
        <div>
          <label className="section-label">Buy date</label>
          <input className="input mt-1" type="date" value={buyDate}
                 onChange={(e) => setBuyDate(e.target.value)} required />
        </div>
        <div>
          <label className="section-label">Notes</label>
          <input className="input mt-1" value={notes} onChange={(e) => setNotes(e.target.value)}
                 placeholder="optional" />
        </div>
      </div>
      <p className="text-[11px] text-gray-600 mt-2">
        Nothing is sent to your broker. This only tells the system you own it, which is
        what separates ADD from BUY and HOLD from WATCH.
      </p>
      <div className="flex justify-end gap-2 mt-3">
        <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
        <button type="submit" className="btn-primary !py-1.5" disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}
