"use client";
import { useEffect, useState } from "react";
import { listTickets, closeTicket, TicketItem, TicketsResponse } from "@/lib/api";

const STATUS_COLORS: Record<string, string> = {
  OPEN:   "bg-yellow-100 text-yellow-700",
  CLOSED: "bg-green-100 text-green-700",
};

const TYPE_COLORS: Record<string, string> = {
  manual:         "bg-blue-100 text-blue-700",
  margin_guard:   "bg-red-100 text-red-700",
  tracking_stale: "bg-amber-100 text-amber-700",
  bot_failure:    "bg-purple-100 text-purple-700",
};

export default function TicketsPage() {
  const [data, setData]       = useState<TicketsResponse | null>(null);
  const [status, setStatus]   = useState("");
  const [type, setType]       = useState("");
  const [q, setQ]             = useState("");
  const [page, setPage]       = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);
  const [closing, setClosing] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    listTickets({ status: status || undefined, type: type || undefined, q: q || undefined, page })
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, [status, type, page]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleClose = async (id: string) => {
    setClosing(id);
    try {
      await closeTicket(id);
      load();
    } catch (e: unknown) {
      const err = e instanceof Error ? e.message : String(e);
      setError(`Close failed: ${err}`);
    } finally {
      setClosing(null);
    }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-4">
      <h1 className="text-2xl font-bold text-slate-800">Tickets</h1>

      {/* Filters */}
      <div className="flex gap-3 flex-wrap">
        <select
          value={status}
          onChange={(e) => { setStatus(e.target.value); setPage(1); }}
          className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm"
        >
          <option value="">All statuses</option>
          {["OPEN","CLOSED"].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>

        <select
          value={type}
          onChange={(e) => { setType(e.target.value); setPage(1); }}
          className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm"
        >
          <option value="">All types</option>
          {["manual","margin_guard","tracking_stale","bot_failure"].map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>

        <input
          type="text"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && (setPage(1), load())}
          placeholder="Search subject…"
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
              {["ID","Order","Type","Status","Subject","Created By","Created","Action"].map((h) => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading && (
              <tr><td colSpan={8} className="py-8 text-center text-slate-400">Loading…</td></tr>
            )}
            {!loading && data?.items.length === 0 && (
              <tr><td colSpan={8} className="py-8 text-center text-slate-400">No tickets found.</td></tr>
            )}
            {!loading && data?.items.map((t: TicketItem) => (
              <tr key={t.id} className="hover:bg-slate-50 transition">
                <td className="px-4 py-3 font-mono text-xs text-slate-500">{t.id.slice(0,8)}…</td>
                <td className="px-4 py-3 font-mono text-xs text-slate-500">
                  {t.order_id ? (
                    <a href={`/dashboard/orders/${t.order_id}`} className="text-blue-600 hover:underline">
                      {t.order_id.slice(0,8)}…
                    </a>
                  ) : "—"}
                </td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${TYPE_COLORS[t.type] ?? "bg-gray-100 text-gray-600"}`}>
                    {t.type}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[t.status] ?? "bg-gray-100 text-gray-600"}`}>
                    {t.status}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-700 max-w-[200px] truncate">{t.subject ?? "—"}</td>
                <td className="px-4 py-3 text-slate-400 text-xs">{t.created_by ?? "—"}</td>
                <td className="px-4 py-3 text-slate-400 text-xs">{t.created_at.slice(0,10)}</td>
                <td className="px-4 py-3">
                  {t.status === "OPEN" && (
                    <button
                      onClick={() => handleClose(t.id)}
                      disabled={closing === t.id}
                      className="text-xs px-2 py-1 rounded bg-green-600 text-white hover:bg-green-700 disabled:opacity-50"
                    >
                      {closing === t.id ? "Closing…" : "Close"}
                    </button>
                  )}
                </td>
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
