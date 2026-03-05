"use client";
import { useEffect, useState, useCallback } from "react";
import { listOrders, retryPlace, getOrder, OrderItem, OrdersResponse } from "@/lib/api";

/* ── constants ─────────────────────────────────────────────────────────────── */
const STATUSES = ["RECEIVED","VALIDATED","PLACING","PLACED","SHIPPED","FAILED","CANCELED"];

const STATUS_PILL: Record<string, string> = {
  RECEIVED:  "bg-blue-100 text-blue-700",
  VALIDATED: "bg-indigo-100 text-indigo-700",
  PLACING:   "bg-yellow-100 text-yellow-700",
  PLACED:    "bg-cyan-100 text-cyan-700",
  SHIPPED:   "bg-green-100 text-green-700",
  FAILED:    "bg-red-100 text-red-700",
  CANCELED:  "bg-gray-100 text-gray-500",
};

/* ── tiny helpers ───────────────────────────────────────────────────────────── */
function Pill({ status }: { status: string }) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${STATUS_PILL[status] ?? "bg-gray-100 text-gray-600"}`}>
      {status}
    </span>
  );
}

function fmtDate(s: string) { return s ? s.slice(0, 16).replace("T", " ") : "—"; }

/* ── Order Detail Modal ─────────────────────────────────────────────────────── */
type DetailOrder = Awaited<ReturnType<typeof getOrder>>;

function OrderModal({ orderId, onClose }: { orderId: string; onClose: () => void }) {
  const [order, setOrder]   = useState<DetailOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [retrying, setRetrying] = useState(false);
  const [msg, setMsg]       = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    getOrder(orderId)
      .then(setOrder)
      .catch((e: Error) => setMsg({ ok: false, text: e.message }))
      .finally(() => setLoading(false));
  }, [orderId]);

  const handleRetry = async () => {
    setRetrying(true);
    setMsg(null);
    try {
      await retryPlace(orderId);
      setMsg({ ok: true, text: "Retry queued — order status will update shortly." });
      // refresh detail
      const updated = await getOrder(orderId);
      setOrder(updated);
    } catch (e: unknown) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setRetrying(false);
    }
  };

  return (
    /* backdrop */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto">
        {/* header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100">
          <h2 className="font-bold text-slate-800 text-lg">Order Detail</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700 text-xl leading-none">✕</button>
        </div>

        <div className="px-6 py-5 space-y-5">
          {loading && <p className="text-slate-400 text-sm text-center py-8">Loading…</p>}

          {msg && (
            <div className={`rounded-lg px-4 py-2.5 text-sm font-medium ${msg.ok ? "bg-green-50 text-green-700 border border-green-200" : "bg-red-50 text-red-700 border border-red-200"}`}>
              {msg.text}
            </div>
          )}

          {order && (
            <>
              {/* Core fields */}
              <dl className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm">
                {([
                  ["Order ID",       order.shopify_order_id],
                  ["Status",         <Pill key="s" status={order.status} />],
                  ["Email",          order.email],
                  ["Supplier",       order.supplier ?? "—"],
                  ["Total",          `${order.total_price} ${order.currency}`],
                  ["Financial",      order.financial_status],
                  ["Supplier Ref",   order.supplier_order_id ?? "—"],
                  ["Tracking #",     order.tracking_number ?? "—"],
                  ["Placed",         fmtDate(order.placed_at ?? "")],
                  ["Shipped",        fmtDate(order.shipped_at ?? "")],
                  ["Created",        fmtDate(order.created_at)],
                  ["Fail Reason",    order.fail_reason ?? "—"],
                ] as [string, React.ReactNode][]).map(([label, val]) => (
                  <div key={label}>
                    <dt className="text-xs font-medium text-slate-500 uppercase tracking-wide">{label}</dt>
                    <dd className="mt-0.5 text-slate-800">{val}</dd>
                  </div>
                ))}
              </dl>

              {/* Actions */}
              {order.status === "FAILED" && (
                <div className="pt-1">
                  <button
                    onClick={handleRetry}
                    disabled={retrying}
                    className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-semibold rounded-lg transition"
                  >
                    {retrying ? "Retrying…" : "↺  Retry Place"}
                  </button>
                </div>
              )}

              {/* Event log */}
              {order.events.length > 0 && (
                <div>
                  <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Event Log</h3>
                  <div className="rounded-lg border border-slate-100 overflow-hidden">
                    <table className="w-full text-xs">
                      <thead className="bg-slate-50">
                        <tr>
                          {["Time","Source","Type","Note"].map(h => (
                            <th key={h} className="px-3 py-2 text-left font-semibold text-slate-400 uppercase">{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-50">
                        {order.events.map(ev => (
                          <tr key={ev.id} className="hover:bg-slate-50">
                            <td className="px-3 py-2 text-slate-400 whitespace-nowrap">{fmtDate(ev.created_at)}</td>
                            <td className="px-3 py-2 text-slate-500">{ev.source}</td>
                            <td className="px-3 py-2 font-medium text-slate-700">{ev.event_type}</td>
                            <td className="px-3 py-2 text-slate-500">{ev.note ?? "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Main Orders Page ───────────────────────────────────────────────────────── */
export default function OrdersPage() {
  const [data, setData]         = useState<OrdersResponse | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [q, setQ]               = useState("");
  const [page, setPage]         = useState(1);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState<string | null>(null);
  const [modalId, setModalId]   = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [retryMsg, setRetryMsg] = useState<Record<string, { ok: boolean; text: string }>>({});

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    listOrders({ status: statusFilter || undefined, q: q || undefined, page, page_size: 25 })
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [statusFilter, q, page]);

  useEffect(() => { load(); }, [load]);

  const handleRetry = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setRetryingId(id);
    setRetryMsg(prev => ({ ...prev, [id]: { ok: false, text: "" } }));
    try {
      await retryPlace(id);
      setRetryMsg(prev => ({ ...prev, [id]: { ok: true, text: "Queued" } }));
      setTimeout(() => load(), 1500);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setRetryMsg(prev => ({ ...prev, [id]: { ok: false, text: msg } }));
    } finally {
      setRetryingId(null);
    }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-800">Orders</h1>
        {data && <span className="text-sm text-slate-400">{data.total} total</span>}
      </div>

      {/* ── Filters ─────────────────────────────────────────────────────────── */}
      <div className="flex gap-3 flex-wrap items-center">
        <select
          value={statusFilter}
          onChange={e => { setStatusFilter(e.target.value); setPage(1); }}
          className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm bg-white"
        >
          <option value="">All statuses</option>
          {STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
        </select>

        <input
          type="text"
          value={q}
          onChange={e => setQ(e.target.value)}
          onKeyDown={e => e.key === "Enter" && (setPage(1), load())}
          placeholder="Search ID / email…"
          className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm w-56"
        />
        <button
          onClick={() => { setPage(1); load(); }}
          className="bg-blue-600 text-white px-4 py-1.5 rounded-lg text-sm hover:bg-blue-700 font-medium"
        >
          Search
        </button>
        <button
          onClick={load}
          className="border border-slate-300 px-3 py-1.5 rounded-lg text-sm hover:bg-slate-50 text-slate-600"
        >
          ↺ Refresh
        </button>
      </div>

      {error && <p className="text-red-600 text-sm bg-red-50 border border-red-200 rounded-lg px-4 py-2">{error}</p>}

      {/* ── Table ───────────────────────────────────────────────────────────── */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-100 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 border-b border-slate-200">
            <tr>
              {["Order ID","Status","Supplier","Email","Total","Created","Action"].map(h => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading && (
              <tr><td colSpan={7} className="py-12 text-center text-slate-400">Loading…</td></tr>
            )}
            {!loading && data?.items.length === 0 && (
              <tr><td colSpan={7} className="py-12 text-center text-slate-400">No orders found.</td></tr>
            )}
            {!loading && data?.items.map((o: OrderItem) => (
              <tr
                key={o.id}
                className="hover:bg-slate-50 transition cursor-pointer"
                onClick={() => setModalId(o.id)}
              >
                <td className="px-4 py-3 font-mono text-xs text-blue-600">
                  {o.shopify_order_id}
                </td>
                <td className="px-4 py-3"><Pill status={o.status} /></td>
                <td className="px-4 py-3 text-slate-500">{o.supplier ?? "—"}</td>
                <td className="px-4 py-3 text-slate-600 max-w-[180px] truncate">{o.email}</td>
                <td className="px-4 py-3 font-medium text-slate-700">{o.total_price} {o.currency}</td>
                <td className="px-4 py-3 text-slate-400 text-xs">{fmtDate(o.created_at)}</td>
                <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                  {o.status === "FAILED" && (
                    <div className="flex items-center gap-2">
                      <button
                        onClick={e => handleRetry(o.id, e)}
                        disabled={retryingId === o.id}
                        className="px-3 py-1 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-xs font-semibold rounded-lg transition"
                      >
                        {retryingId === o.id ? "…" : "↺ Retry"}
                      </button>
                      {retryMsg[o.id]?.text && (
                        <span className={`text-xs font-medium ${retryMsg[o.id].ok ? "text-green-600" : "text-red-500"}`}>
                          {retryMsg[o.id].text}
                        </span>
                      )}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── Pagination ──────────────────────────────────────────────────────── */}
      {data && data.total > 25 && (
        <div className="flex items-center gap-3 text-sm text-slate-600">
          <button
            disabled={page === 1}
            onClick={() => setPage(p => p - 1)}
            className="px-3 py-1 rounded border border-slate-300 disabled:opacity-40 hover:bg-slate-50"
          >← Prev</button>
          <span className="text-slate-500">Page {page} of {Math.ceil(data.total / 25)}</span>
          <button
            disabled={page * 25 >= data.total}
            onClick={() => setPage(p => p + 1)}
            className="px-3 py-1 rounded border border-slate-300 disabled:opacity-40 hover:bg-slate-50"
          >Next →</button>
        </div>
      )}

      {/* ── Detail Modal ────────────────────────────────────────────────────── */}
      {modalId && (
        <OrderModal orderId={modalId} onClose={() => setModalId(null)} />
      )}
    </div>
  );
}
