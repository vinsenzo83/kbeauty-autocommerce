"use client";
import { useEffect, useState } from "react";
import { getHealth, HealthResponse } from "@/lib/api";

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-block w-3 h-3 rounded-full ${ok ? "bg-green-500" : "bg-red-500"}`}
      title={ok ? "OK" : "FAIL"}
    />
  );
}

function StatusRow({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className="flex items-center gap-3 py-2 border-b border-slate-100 last:border-0">
      <StatusDot ok={ok} />
      <span className="text-sm font-medium text-slate-700">{label}</span>
      <span className={`ml-auto text-xs font-semibold px-2 py-0.5 rounded-full ${ok ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
        {ok ? "OK" : "FAIL"}
      </span>
    </div>
  );
}

export default function HealthPage() {
  const [health, setHealth]   = useState<HealthResponse | null>(null);
  const [error, setError]     = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastRefreshed, setLastRefreshed] = useState<string>("");

  const load = () => {
    setLoading(true);
    setError(null);
    getHealth()
      .then((h) => {
        setHealth(h);
        setLastRefreshed(new Date().toLocaleTimeString());
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    const interval = setInterval(load, 30_000); // auto-refresh every 30s
    return () => clearInterval(interval);
  }, []);

  const overall = health ? (health.db_ok && health.redis_ok) : false;

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-800">System Health</h1>
        <div className="flex items-center gap-3">
          {lastRefreshed && (
            <span className="text-xs text-slate-400">Last refreshed: {lastRefreshed}</span>
          )}
          <button
            onClick={load}
            disabled={loading}
            className="text-sm bg-blue-600 text-white px-4 py-1.5 rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-red-600 text-sm">
          Error loading health data: {error}
        </div>
      )}

      {/* Overall Status Banner */}
      {health && (
        <div className={`rounded-xl px-6 py-4 flex items-center gap-4 ${overall ? "bg-green-50 border border-green-200" : "bg-red-50 border border-red-200"}`}>
          <div className={`w-5 h-5 rounded-full ${overall ? "bg-green-500" : "bg-red-500"}`} />
          <div>
            <p className={`font-bold text-lg ${overall ? "text-green-700" : "text-red-700"}`}>
              System {overall ? "Healthy" : "Degraded"}
            </p>
            <p className="text-sm text-slate-500">
              {overall ? "All core services are operational." : "One or more services are experiencing issues."}
            </p>
          </div>
        </div>
      )}

      {/* Service Status */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-100 p-5">
        <h2 className="text-sm font-semibold text-slate-500 uppercase mb-3">Services</h2>
        {loading && !health ? (
          <div className="text-slate-400 text-sm py-4 text-center">Loading…</div>
        ) : health ? (
          <div>
            <StatusRow label="Database (PostgreSQL)" ok={health.db_ok} />
            <StatusRow label="Cache (Redis)" ok={health.redis_ok} />
            <div className="flex items-center gap-3 py-2 border-b border-slate-100 last:border-0">
              <StatusDot ok={(health.queue_depth ?? 0) < 100} />
              <span className="text-sm font-medium text-slate-700">Celery Queue</span>
              <span className="ml-auto text-sm font-semibold text-slate-600">
                {health.queue_depth !== null && health.queue_depth !== undefined
                  ? `${health.queue_depth} jobs`
                  : "N/A"}
              </span>
            </div>
          </div>
        ) : null}
      </div>

      {/* Recent Failures */}
      {health && health.recent_failures_24h.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-slate-100 overflow-hidden">
          <h2 className="text-sm font-semibold text-slate-500 uppercase p-5 pb-3">
            Recent Failures (24h)
          </h2>
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase">Event Type</th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-slate-500 uppercase">Count</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {health.recent_failures_24h.map((row) => (
                <tr key={row.event_type} className="hover:bg-slate-50">
                  <td className="px-4 py-3 text-slate-700 font-medium">{row.event_type}</td>
                  <td className="px-4 py-3 text-right">
                    <span className={`font-bold ${row.count > 0 ? "text-red-600" : "text-slate-500"}`}>
                      {row.count}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {health && health.recent_failures_24h.length === 0 && (
        <div className="bg-green-50 border border-green-200 rounded-xl px-6 py-4 text-green-700 text-sm">
          ✓ No failures recorded in the last 24 hours.
        </div>
      )}
    </div>
  );
}
