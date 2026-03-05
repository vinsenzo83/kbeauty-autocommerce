"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { listOrders, OrderItem, OrdersResponse } from "@/lib/api";

const STATUS_COLORS: Record<string, string> = {
  RECEIVED:  "bg-blue-100 text-blue-700",
  VALIDATED: "bg-indigo-100 text-indigo-700",
  PLACING:   "bg-yellow-100 text-yellow-700",
  PLACED:    "bg-cyan-100 text-cyan-700",
  SHIPPED:   "bg-green-100 text-green-700",
  FAILED:    "bg-red-100 text-red-700",
  CANCELED:  "bg-gray-100 text-gray-600",
};

export default function OrdersPage() {
  const [data, setData]       = useState<OrdersResponse | null>(null);
  const [status, setStatus]   = useState("");
  const [q, setQ]             = useState("");
  const [page, setPage]       = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    listOrders({ status: status || undefined, q: q || undefined, page, page_size: 20 })
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, [status, page]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-4">
      <h1 className="text-2xl font-bold text-slate-800">Orders</h1>

      {/* Filters */}
      <div className="flex gap-3 flex-wrap">
        <select
          value={status}
          onChange={(e) => { setStatus(e.target.value); setPage(1); }}
          className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm"
        >
          <option value="">All statuses</option>
          {["RECEIVED","VALIDATED","PLACING","PLACED","SHIPPED","FAILED","CANCELED"].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>

        <input
          type="text"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && (setPage(1), load())}
          placeholder="Search order ID / email…"
          className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm w-56"
        />
        <button
          onClick={() => { setPage(1); load(); }}
          className="bg-blue-600 text-white px-4 py-1.5 rounded-lg text-sm hover:bg-blue-700"
        >
          Search
        </button>
      </div>

      {error && <p className="text-red-600 text-sm">{error}</p>}

      {/* Table */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-100 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 border-b border-slate-200">
            <tr>
              {["Shopify ID","Email","Total","Status","Supplier","Created"].map((h) => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading && (
              <tr><td colSpan={6} className="py-8 text-center text-slate-400">Loading…</td></tr>
            )}
            {!loading && data?.items.length === 0 && (
              <tr><td colSpan={6} className="py-8 text-center text-slate-400">No orders found.</td></tr>
            )}
            {!loading && data?.items.map((o) => (
              <tr key={o.id} className="hover:bg-slate-50 transition">
                <td className="px-4 py-3">
                  <Link href={`/dashboard/orders/${o.id}`} className="text-blue-600 hover:underline font-mono text-xs">
                    {o.shopify_order_id}
                  </Link>
                </td>
                <td className="px-4 py-3 text-slate-600 truncate max-w-[180px]">{o.email}</td>
                <td className="px-4 py-3 font-medium">{o.total_price} {o.currency}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[o.status] ?? "bg-gray-100 text-gray-600"}`}>
                    {o.status}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-500">{o.supplier ?? "—"}</td>
                <td className="px-4 py-3 text-slate-400 text-xs">{o.created_at.slice(0,10)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {data && data.total > 20 && (
        <div className="flex items-center gap-3 text-sm text-slate-600">
          <button
            disabled={page === 1}
            onClick={() => setPage((p) => p - 1)}
            className="px-3 py-1 rounded border border-slate-300 disabled:opacity-40"
          >← Prev</button>
          <span>Page {page} · {data.total} total</span>
          <button
            disabled={page * 20 >= data.total}
            onClick={() => setPage((p) => p + 1)}
            className="px-3 py-1 rounded border border-slate-300 disabled:opacity-40"
          >Next →</button>
        </div>
      )}
    </div>
  );
}
