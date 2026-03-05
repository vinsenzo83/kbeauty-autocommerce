"use client";
import { useEffect, useState } from "react";
import { getHealth, HealthResponse } from "@/lib/api";

/* ── helpers ────────────────────────────────────────────────────────────────── */
function dot(ok: boolean) {
  return (
    <span
      className={`inline-block w-3 h-3 rounded-full flex-shrink-0 ${ok ? "bg-green-500" : "bg-red-500"}`}
    />
  );
}

function Badge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className={`px-2.5 py-0.5 rounded-full text-xs font-semibold ${ok ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
      {ok ? label : `${label} DOWN`}
    </span>
  );
}

function ServiceRow({
  label,
  ok,
  detail,
}: {
  label: string;
  ok: boolean;
  detail?: string;
}) {
  return (
    <div className="flex items-center gap-3 py-3 border-b border-slate-100 last:border-0">
      {dot(ok)}
      <span className="flex-1 text-sm font-medium text-slate-700">{label}</span>
      {detail && <span className="text-sm text-slate-500">{detail}</span>}
      <Badge ok={ok} label={ok ? "OK" : "FAIL"} />
    </div>
  );
}

/* ── main page ──────────────────────────────────────────────────────────────── */
export default function HealthPage() {
  const [health, setHealth]   = useState<HealthResponse | null>(null);
  const [error, setError]     = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastAt, setLastAt]   = useState("");

  const load = () => {
    setLoading(true);
    setError(null);
    getHealth()
      .then(h => { setHealth(h); setLastAt(new Date().toLocaleTimeString()); })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);

  /* derived */
  const queueOk   = (health?.queue_depth ?? 0) < 100;
  const overallOk = !!(health?.db_ok && health?.redis_ok);

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6">
      {/* ── header ───────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-800">System Health</h1>
        <div className="flex items-center gap-3">
          {lastAt && <span className="text-xs text-slate-400">Updated {lastAt}</span>}
          <button
            onClick={load}
            disabled={loading}
            className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 font-medium"
          >
            {loading ? "Refreshing…" : "↺ Refresh"}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-xl px-5 py-3 text-red-600 text-sm">
          {error}
        </div>
      )}

      {/* ── overall banner ───────────────────────────────────────────────────── */}
      {health && (
        <div className={`flex items-center gap-4 rounded-xl px-6 py-4 border ${overallOk ? "bg-green-50 border-green-200" : "bg-red-50 border-red-200"}`}>
          <div className={`w-5 h-5 rounded-full flex-shrink-0 ${overallOk ? "bg-green-500" : "bg-red-500"}`} />
          <div>
            <p className={`font-bold text-lg ${overallOk ? "text-green-700" : "text-red-700"}`}>
              {overallOk ? "All Systems Operational" : "System Degraded"}
            </p>
            <p className="text-sm text-slate-500">
              {overallOk
                ? "Database and cache are reachable."
                : "One or more critical services are unavailable."}
            </p>
          </div>
        </div>
      )}

      {/* ── service status ───────────────────────────────────────────────────── */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-100 px-6 py-2">
        <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wide pt-4 pb-2">Services</h2>
        {loading && !health
          ? <p className="text-slate-400 text-sm py-6 text-center">Loading…</p>
          : health && (
            <>
              <ServiceRow label="PostgreSQL Database"  ok={health.db_ok} />
              <ServiceRow label="Redis Cache"          ok={health.redis_ok} />
              <ServiceRow
                label="Celery Queue"
                ok={queueOk}
                detail={health.queue_depth !== null && health.queue_depth !== undefined
                  ? `${health.queue_depth} job${health.queue_depth !== 1 ? "s" : ""} pending`
                  : "unavailable"}
              />
            </>
          )
        }
      </div>

      {/* ── queue depth gauge ────────────────────────────────────────────────── */}
      {health && health.queue_depth !== null && health.queue_depth !== undefined && (
        <div className="bg-white rounded-xl shadow-sm border border-slate-100 px-6 py-5">
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">Queue Depth</h2>
          <div className="flex items-end gap-4">
            <span className={`text-5xl font-black ${health.queue_depth > 50 ? "text-red-600" : health.queue_depth > 10 ? "text-amber-500" : "text-green-600"}`}>
              {health.queue_depth}
            </span>
            <span className="text-slate-500 text-sm mb-1">jobs in Celery queue</span>
          </div>
          {/* progress bar */}
          <div className="mt-3 h-2 rounded-full bg-slate-100 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${health.queue_depth > 50 ? "bg-red-500" : health.queue_depth > 10 ? "bg-amber-400" : "bg-green-500"}`}
              style={{ width: `${Math.min(100, (health.queue_depth / 100) * 100)}%` }}
            />
          </div>
          <p className="text-xs text-slate-400 mt-1">
            {health.queue_depth === 0
              ? "Queue is empty — workers are idle."
              : health.queue_depth > 50
              ? "⚠ High queue depth — workers may be overloaded."
              : "Queue depth is within normal range."}
          </p>
        </div>
      )}

      {/* ── recent failures ──────────────────────────────────────────────────── */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-100 overflow-hidden">
        <div className="px-6 pt-5 pb-3">
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
            Recent Failures — last 24 h
          </h2>
        </div>
        {!health ? null : health.recent_failures_24h.length === 0 ? (
          <div className="px-6 pb-5">
            <p className="text-sm text-green-700 bg-green-50 border border-green-200 rounded-lg px-4 py-3">
              ✓ No failures recorded in the last 24 hours.
            </p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-y border-slate-200">
              <tr>
                <th className="px-6 py-2 text-left text-xs font-semibold text-slate-500 uppercase">Event Type</th>
                <th className="px-6 py-2 text-right text-xs font-semibold text-slate-500 uppercase">Count</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {health.recent_failures_24h.map(row => (
                <tr key={row.event_type} className="hover:bg-slate-50">
                  <td className="px-6 py-3 text-slate-700 font-medium">{row.event_type}</td>
                  <td className="px-6 py-3 text-right">
                    <span className={`font-bold text-base ${row.count > 0 ? "text-red-600" : "text-slate-500"}`}>
                      {row.count}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
