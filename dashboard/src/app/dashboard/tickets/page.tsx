"use client";
import { useEffect, useState, useCallback } from "react";
import {
  listTickets,
  closeTicket,
  listOrders,
  retryPlace,
  TicketItem,
  TicketsResponse,
  OrderItem,
} from "@/lib/api";

/* ── helpers ────────────────────────────────────────────────────────────────── */
const STATUS_PILL: Record<string, string> = {
  OPEN:   "bg-yellow-100 text-yellow-700",
  CLOSED: "bg-green-100 text-green-700",
};
const TYPE_PILL: Record<string, string> = {
  manual:         "bg-blue-100 text-blue-700",
  margin_guard:   "bg-red-100 text-red-700",
  tracking_stale: "bg-amber-100 text-amber-700",
  bot_failure:    "bg-purple-100 text-purple-700",
};
function fmtDate(s: string) { return s ? s.slice(0, 16).replace("T", " ") : "—"; }

/* ── Failed Orders section ──────────────────────────────────────────────────── */
function FailedOrders() {
  const [orders, setOrders]     = useState<OrderItem[]>([]);
  const [loading, setLoading]   = useState(false);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [msgs, setMsgs]         = useState<Record<string, { ok: boolean; text: string }>>({});

  const loadFailed = useCallback(() => {
    setLoading(true);
    listOrders({ status: "FAILED", page_size: 50 })
      .then(r => setOrders(r.items))
      .catch(() => setOrders([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadFailed(); }, [loadFailed]);

  const handleRetry = async (id: string) => {
    setRetryingId(id);
    setMsgs(prev => ({ ...prev, [id]: { ok: false, text: "" } }));
    try {
      await retryPlace(id);
      setMsgs(prev => ({ ...prev, [id]: { ok: true, text: "Queued ✓" } }));
      setTimeout(loadFailed, 2000);
    } catch (e: unknown) {
      setMsgs(prev => ({ ...prev, [id]: { ok: false, text: e instanceof Error ? e.message : String(e) } }));
    } finally {
      setRetryingId(null);
    }
  };

  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-100 overflow-hidden">
      <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
        <h2 className="font-semibold text-slate-700">
          Failed Orders
          {orders.length > 0 && (
            <span className="ml-2 px-2 py-0.5 bg-red-100 text-red-700 text-xs font-bold rounded-full">{orders.length}</span>
          )}
        </h2>
        <button onClick={loadFailed} className="text-xs text-slate-400 hover:text-slate-700">↺ Refresh</button>
      </div>

      {loading
        ? <p className="text-slate-400 text-sm text-center py-8">Loading…</p>
        : orders.length === 0
          ? <p className="text-slate-400 text-sm text-center py-8">No failed orders. ✓</p>
          : (
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  {["Order ID","Email","Supplier","Fail Reason","Created","Action"].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {orders.map(o => (
                  <tr key={o.id} className="hover:bg-slate-50">
                    <td className="px-4 py-3 font-mono text-xs text-blue-600">{o.shopify_order_id}</td>
                    <td className="px-4 py-3 text-slate-600 max-w-[140px] truncate">{o.email}</td>
                    <td className="px-4 py-3 text-slate-500">{o.supplier ?? "—"}</td>
                    <td className="px-4 py-3 text-red-600 text-xs max-w-[200px] truncate">{o.fail_reason ?? "—"}</td>
                    <td className="px-4 py-3 text-slate-400 text-xs">{fmtDate(o.created_at)}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => handleRetry(o.id)}
                          disabled={retryingId === o.id}
                          className="px-3 py-1 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-xs font-semibold rounded-lg transition"
                        >
                          {retryingId === o.id ? "…" : "↺ Retry"}
                        </button>
                        {msgs[o.id]?.text && (
                          <span className={`text-xs font-medium ${msgs[o.id].ok ? "text-green-600" : "text-red-500"}`}>
                            {msgs[o.id].text}
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
      }
    </div>
  );
}

/* ── main tickets page ──────────────────────────────────────────────────────── */
export default function TicketsPage() {
  const [data, setData]         = useState<TicketsResponse | null>(null);
  const [statusFilter, setStatusFilter] = useState("OPEN");
  const [typeFilter, setTypeFilter]     = useState("");
  const [q, setQ]               = useState("");
  const [page, setPage]         = useState(1);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState<string | null>(null);
  const [closingId, setClosingId] = useState<string | null>(null);
  const [closeMsgs, setCloseMsgs] = useState<Record<string, string>>({});

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    listTickets({
      status: statusFilter || undefined,
      type:   typeFilter   || undefined,
      q:      q            || undefined,
      page,
    })
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [statusFilter, typeFilter, q, page]);

  useEffect(() => { load(); }, [load]);

  const handleClose = async (id: string) => {
    setClosingId(id);
    try {
      await closeTicket(id);
      setCloseMsgs(prev => ({ ...prev, [id]: "Closed ✓" }));
      setTimeout(load, 800);
    } catch (e: unknown) {
      setCloseMsgs(prev => ({ ...prev, [id]: e instanceof Error ? e.message : "Failed" }));
    } finally {
      setClosingId(null);
    }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold text-slate-800">Tickets</h1>

      {/* ── Failed orders (always shown at top) ─────────────────────────────── */}
      <FailedOrders />

      {/* ── Ticket list ──────────────────────────────────────────────────────── */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-100 overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-100">
          <h2 className="font-semibold text-slate-700">Support Tickets</h2>
        </div>

        {/* filters */}
        <div className="px-6 py-3 border-b border-slate-100 flex gap-3 flex-wrap items-center">
          <select
            value={statusFilter}
            onChange={e => { setStatusFilter(e.target.value); setPage(1); }}
            className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm bg-white"
          >
            <option value="">All statuses</option>
            <option value="OPEN">OPEN</option>
            <option value="CLOSED">CLOSED</option>
          </select>

          <select
            value={typeFilter}
            onChange={e => { setTypeFilter(e.target.value); setPage(1); }}
            className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm bg-white"
          >
            <option value="">All types</option>
            {["manual","margin_guard","tracking_stale","bot_failure"].map(t => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>

          <input
            type="text"
            value={q}
            onChange={e => setQ(e.target.value)}
            onKeyDown={e => e.key === "Enter" && (setPage(1), load())}
            placeholder="Search subject…"
            className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm w-48"
          />
          <button
            onClick={() => { setPage(1); load(); }}
            className="bg-blue-600 text-white px-4 py-1.5 rounded-lg text-sm hover:bg-blue-700 font-medium"
          >
            Search
          </button>
        </div>

        {error && <p className="text-red-600 text-sm px-6 py-3">{error}</p>}

        <table className="w-full text-sm">
          <thead className="bg-slate-50 border-b border-slate-200">
            <tr>
              {["ID","Type","Status","Subject","Order","Created","Action"].map(h => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading && <tr><td colSpan={7} className="py-10 text-center text-slate-400">Loading…</td></tr>}
            {!loading && data?.items.length === 0 && (
              <tr><td colSpan={7} className="py-10 text-center text-slate-400">No tickets found.</td></tr>
            )}
            {!loading && data?.items.map((t: TicketItem) => (
              <tr key={t.id} className="hover:bg-slate-50">
                <td className="px-4 py-3 font-mono text-xs text-slate-400">{t.id.slice(0, 8)}…</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${TYPE_PILL[t.type] ?? "bg-gray-100 text-gray-600"}`}>
                    {t.type}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${STATUS_PILL[t.status] ?? "bg-gray-100 text-gray-600"}`}>
                    {t.status}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-700 max-w-[200px] truncate">{t.subject ?? "—"}</td>
                <td className="px-4 py-3">
                  {t.order_id
                    ? <a href={`/dashboard/orders/${t.order_id}`} className="font-mono text-xs text-blue-600 hover:underline">{t.order_id.slice(0,8)}…</a>
                    : <span className="text-slate-400">—</span>}
                </td>
                <td className="px-4 py-3 text-slate-400 text-xs">{fmtDate(t.created_at)}</td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    {t.status === "OPEN" && (
                      <button
                        onClick={() => handleClose(t.id)}
                        disabled={closingId === t.id}
                        className="px-3 py-1 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white text-xs font-semibold rounded-lg transition"
                      >
                        {closingId === t.id ? "…" : "✓ Resolve"}
                      </button>
                    )}
                    {closeMsgs[t.id] && (
                      <span className={`text-xs font-medium ${closeMsgs[t.id].endsWith("✓") ? "text-green-600" : "text-red-500"}`}>
                        {closeMsgs[t.id]}
                      </span>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {/* pagination */}
        {data && data.total > 20 && (
          <div className="flex items-center gap-3 text-sm text-slate-600 px-6 py-4 border-t border-slate-100">
            <button
              disabled={page === 1}
              onClick={() => setPage(p => p - 1)}
              className="px-3 py-1 rounded border border-slate-300 disabled:opacity-40 hover:bg-slate-50"
            >← Prev</button>
            <span className="text-slate-400">Page {page} of {Math.ceil(data.total / 20)}</span>
            <button
              disabled={page * 20 >= data.total}
              onClick={() => setPage(p => p + 1)}
              className="px-3 py-1 rounded border border-slate-300 disabled:opacity-40 hover:bg-slate-50"
            >Next →</button>
          </div>
        )}
      </div>
    </div>
  );
}
