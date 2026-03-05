"use client";
import { useEffect, useState, useCallback } from "react";

// ── API types ─────────────────────────────────────────────────────────────────
interface KpiSnapshot {
  window_minutes: number;
  collected_at: string;
  total_order_count: number;
  pending_order_count: number;
  order_error_rate: number;
  supplier_order_count: number;
  fulfillment_error_count: number;
  fulfillment_error_rate: number;
  avg_fulfillment_hours: number;
  repricing_run_count: number;
  repricing_updated_count: number;
  repricing_error_count: number;
  publish_job_count: number;
  publish_success_count: number;
  publish_failure_count: number;
  discovery_candidate_count: number;
  market_price_count: number;
  recent_errors: ErrorEvent[];
}

interface AlertEvent {
  id: string;
  rule_id: string;
  rule_name: string;
  metric: string;
  observed_value: number;
  threshold: number;
  severity: string;
  status: string;
  notes: string | null;
  fired_at: string | null;
  resolved_at: string | null;
}

interface ErrorEvent {
  type: string;
  id: string;
  reason: string;
  supplier?: string;
  ts: string | null;
}

// ── API helpers ───────────────────────────────────────────────────────────────
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string, opts?: RequestInit): Promise<T> {
  const token =
    typeof window !== "undefined" ? localStorage.getItem("auth_token") : null;
  const res = await fetch(`${API_BASE}${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts?.headers ?? {}),
    },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function fmtDate(s: string | null | undefined) {
  return s ? s.slice(0, 16).replace("T", " ") : "—";
}
function fmtPct(v: number) {
  return (v * 100).toFixed(1) + "%";
}

// ── Severity styling ──────────────────────────────────────────────────────────
const SEV_BADGE: Record<string, string> = {
  critical: "bg-red-100 text-red-700 border border-red-200",
  warning:  "bg-yellow-100 text-yellow-700 border border-yellow-200",
  info:     "bg-blue-100 text-blue-600 border border-blue-200",
};
function SevBadge({ s }: { s: string }) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${SEV_BADGE[s] ?? "bg-gray-100 text-gray-600"}`}>
      {s}
    </span>
  );
}
const STATUS_PILL: Record<string, string> = {
  open:         "bg-red-100 text-red-700",
  acknowledged: "bg-yellow-100 text-yellow-700",
  resolved:     "bg-green-100 text-green-700",
};
function StatusPill({ status }: { status: string }) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${STATUS_PILL[status] ?? "bg-gray-100 text-gray-600"}`}>
      {status}
    </span>
  );
}

// ── KPI Card ──────────────────────────────────────────────────────────────────
function KpiCard({
  label, value, sub, color,
}: {
  label: string; value: string | number; sub?: string; color?: string;
}) {
  return (
    <div className="bg-white rounded-xl border shadow-sm p-4 flex flex-col gap-1">
      <p className="text-xs text-gray-500 uppercase tracking-wide">{label}</p>
      <p className={`text-2xl font-bold ${color ?? "text-gray-800"}`}>{value}</p>
      {sub && <p className="text-xs text-gray-400">{sub}</p>}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function OpsPage() {
  const [tab, setTab] = useState<"kpis" | "alerts" | "errors">("kpis");
  const [window_, setWindow_] = useState(60);

  // KPI state
  const [kpis, setKpis]       = useState<KpiSnapshot | null>(null);
  const [kpiLoading, setKpiLoading] = useState(true);
  const [kpiError, setKpiError]     = useState<string | null>(null);

  // Alerts state
  const [alerts, setAlerts]       = useState<AlertEvent[]>([]);
  const [alertLoading, setAlertLoading] = useState(true);
  const [alertError, setAlertError]     = useState<string | null>(null);
  const [actionMsg, setActionMsg]       = useState<string | null>(null);

  const loadKpis = useCallback(async () => {
    setKpiLoading(true);
    setKpiError(null);
    try {
      const data = await apiFetch<KpiSnapshot>(`/admin/ops/kpis?window_minutes=${window_}`);
      setKpis(data);
    } catch (e: unknown) {
      setKpiError(e instanceof Error ? e.message : String(e));
    } finally {
      setKpiLoading(false);
    }
  }, [window_]);

  const loadAlerts = useCallback(async () => {
    setAlertLoading(true);
    setAlertError(null);
    try {
      const data = await apiFetch<{ total: number; items: AlertEvent[] }>("/admin/ops/alerts?limit=50");
      setAlerts(data.items);
    } catch (e: unknown) {
      setAlertError(e instanceof Error ? e.message : String(e));
    } finally {
      setAlertLoading(false);
    }
  }, []);

  useEffect(() => { loadKpis(); }, [loadKpis]);
  useEffect(() => { loadAlerts(); }, [loadAlerts]);

  const handleAck = async (id: string) => {
    try {
      await apiFetch(`/admin/ops/alerts/${id}/acknowledge`, { method: "POST" });
      setActionMsg(`✅ Alert ${id.slice(0, 8)}… acknowledged`);
      await loadAlerts();
    } catch (e: unknown) {
      setActionMsg(`❌ ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleResolve = async (id: string) => {
    try {
      await apiFetch(`/admin/ops/alerts/${id}/resolve`, { method: "POST" });
      setActionMsg(`✅ Alert ${id.slice(0, 8)}… resolved`);
      await loadAlerts();
    } catch (e: unknown) {
      setActionMsg(`❌ ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const openAlerts = alerts.filter(a => a.status === "open").length;
  const criticalAlerts = alerts.filter(a => a.severity === "critical" && a.status === "open").length;

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">⚙️ Ops Dashboard</h1>
          <p className="text-sm text-gray-500 mt-1">
            Sprint 16 — KPIs · Alerts · Error Feed
            {kpis && <span className="ml-2 text-gray-400">· updated {fmtDate(kpis.collected_at)}</span>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-500">Window:</label>
          <select
            value={window_}
            onChange={e => setWindow_(Number(e.target.value))}
            className="border rounded px-2 py-1 text-sm"
          >
            {[15, 30, 60, 180, 360, 720, 1440].map(v => (
              <option key={v} value={v}>{v >= 60 ? `${v/60}h` : `${v}m`}</option>
            ))}
          </select>
          <button
            onClick={() => { loadKpis(); loadAlerts(); }}
            className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm px-3 py-1.5 rounded-lg transition"
          >
            🔄 Refresh
          </button>
        </div>
      </div>

      {/* Quick status bar */}
      {criticalAlerts > 0 && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 flex items-center gap-2 text-sm text-red-700 font-semibold">
          🚨 {criticalAlerts} critical alert{criticalAlerts > 1 ? "s" : ""} open — check Alerts tab
        </div>
      )}
      {actionMsg && (
        <div className="bg-indigo-50 border border-indigo-200 rounded-lg px-4 py-2 text-sm text-indigo-700">
          {actionMsg}
        </div>
      )}

      {/* KPI summary cards (always visible) */}
      {!kpiLoading && kpis && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          <KpiCard label="Pending Orders"  value={kpis.pending_order_count}
            color={kpis.pending_order_count > 50 ? "text-red-600" : "text-gray-800"} />
          <KpiCard label="Fulfill Error Rate" value={fmtPct(kpis.fulfillment_error_rate)}
            color={kpis.fulfillment_error_rate > 0.1 ? "text-red-600" : "text-green-600"} />
          <KpiCard label="Repriced Products" value={kpis.repricing_updated_count}
            sub={`${kpis.repricing_run_count} runs`} />
          <KpiCard label="Published OK"    value={kpis.publish_success_count}
            sub={`${kpis.publish_failure_count} failed`}
            color={kpis.publish_failure_count > 2 ? "text-yellow-600" : "text-green-600"} />
          <KpiCard label="Discovery Cands" value={kpis.discovery_candidate_count}
            sub={`${kpis.market_price_count} mkt prices`} />
        </div>
      )}

      {/* Tabs */}
      <div className="border-b flex gap-4">
        {(["kpis", "alerts", "errors"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`pb-2 text-sm font-medium capitalize border-b-2 transition ${
              tab === t ? "border-indigo-600 text-indigo-700" : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {t === "kpis" ? "📊 KPIs" : t === "alerts" ? `🔔 Alerts (${openAlerts})` : "❌ Errors"}
          </button>
        ))}
      </div>

      {/* KPIs Tab */}
      {tab === "kpis" && (
        <div>
          {kpiLoading ? (
            <p className="text-gray-500 text-sm py-6 text-center">Loading KPIs…</p>
          ) : kpiError ? (
            <p className="text-red-500 text-sm py-4">Error: {kpiError}</p>
          ) : kpis ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {/* Orders */}
              <div className="bg-white rounded-xl border shadow-sm p-4">
                <h3 className="font-semibold text-gray-700 mb-3 text-sm">📦 Orders</h3>
                <div className="space-y-1.5 text-sm">
                  {[
                    ["Total Orders",      kpis.total_order_count],
                    ["Pending",           kpis.pending_order_count],
                    ["Error Rate (win.)", fmtPct(kpis.order_error_rate)],
                  ].map(([k,v]) => (
                    <div key={String(k)} className="flex justify-between">
                      <span className="text-gray-500">{k}</span>
                      <span className="font-medium">{v}</span>
                    </div>
                  ))}
                </div>
              </div>
              {/* Fulfillment */}
              <div className="bg-white rounded-xl border shadow-sm p-4">
                <h3 className="font-semibold text-gray-700 mb-3 text-sm">🚚 Fulfillment</h3>
                <div className="space-y-1.5 text-sm">
                  {[
                    ["Supplier Orders (win.)", kpis.supplier_order_count],
                    ["Errors",                 kpis.fulfillment_error_count],
                    ["Error Rate",             fmtPct(kpis.fulfillment_error_rate)],
                  ].map(([k,v]) => (
                    <div key={String(k)} className="flex justify-between">
                      <span className="text-gray-500">{k}</span>
                      <span className={`font-medium ${String(k).includes("Error") && Number(v) > 0 ? "text-red-600" : ""}`}>{v}</span>
                    </div>
                  ))}
                </div>
              </div>
              {/* Repricing */}
              <div className="bg-white rounded-xl border shadow-sm p-4">
                <h3 className="font-semibold text-gray-700 mb-3 text-sm">💰 Repricing</h3>
                <div className="space-y-1.5 text-sm">
                  {[
                    ["Runs (win.)",     kpis.repricing_run_count],
                    ["Updated",        kpis.repricing_updated_count],
                    ["Errors",         kpis.repricing_error_count],
                  ].map(([k,v]) => (
                    <div key={String(k)} className="flex justify-between">
                      <span className="text-gray-500">{k}</span>
                      <span className="font-medium">{v}</span>
                    </div>
                  ))}
                </div>
              </div>
              {/* Publishing */}
              <div className="bg-white rounded-xl border shadow-sm p-4">
                <h3 className="font-semibold text-gray-700 mb-3 text-sm">🚀 Publishing</h3>
                <div className="space-y-1.5 text-sm">
                  {[
                    ["Jobs (win.)",  kpis.publish_job_count],
                    ["Success",     kpis.publish_success_count],
                    ["Failed",      kpis.publish_failure_count],
                  ].map(([k,v]) => (
                    <div key={String(k)} className="flex justify-between">
                      <span className="text-gray-500">{k}</span>
                      <span className={`font-medium ${k === "Failed" && Number(v) > 2 ? "text-yellow-600" : ""}`}>{v}</span>
                    </div>
                  ))}
                </div>
              </div>
              {/* Discovery */}
              <div className="bg-white rounded-xl border shadow-sm p-4">
                <h3 className="font-semibold text-gray-700 mb-3 text-sm">🔭 Discovery</h3>
                <div className="space-y-1.5 text-sm">
                  {[
                    ["Candidates",     kpis.discovery_candidate_count],
                    ["Market Prices",  kpis.market_price_count],
                  ].map(([k,v]) => (
                    <div key={String(k)} className="flex justify-between">
                      <span className="text-gray-500">{k}</span>
                      <span className="font-medium">{v}</span>
                    </div>
                  ))}
                </div>
              </div>
              {/* Window info */}
              <div className="bg-indigo-50 rounded-xl border border-indigo-100 p-4">
                <h3 className="font-semibold text-indigo-700 mb-3 text-sm">ℹ️ Snapshot Info</h3>
                <div className="space-y-1.5 text-sm">
                  <div className="flex justify-between">
                    <span className="text-indigo-500">Window</span>
                    <span className="font-medium text-indigo-700">{kpis.window_minutes}m</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-indigo-500">Collected</span>
                    <span className="font-medium text-indigo-700 text-xs">{fmtDate(kpis.collected_at)}</span>
                  </div>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      )}

      {/* Alerts Tab */}
      {tab === "alerts" && (
        <div>
          {alertLoading ? (
            <p className="text-gray-500 text-sm py-6 text-center">Loading alerts…</p>
          ) : alertError ? (
            <p className="text-red-500 text-sm py-4">Error: {alertError}</p>
          ) : alerts.length === 0 ? (
            <div className="text-center py-10 text-gray-400">
              <p className="text-4xl mb-2">✅</p>
              <p className="text-sm">No open alerts — all systems nominal.</p>
            </div>
          ) : (
            <div className="space-y-2">
              {alerts.map(a => (
                <div key={a.id} className={`bg-white rounded-xl border shadow-sm p-4 flex items-start justify-between gap-3 ${
                  a.severity === "critical" && a.status === "open" ? "border-red-200" : ""
                }`}>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap mb-1">
                      <SevBadge s={a.severity} />
                      <StatusPill status={a.status} />
                      <span className="font-semibold text-sm text-gray-800 truncate">{a.rule_name}</span>
                    </div>
                    <p className="text-xs text-gray-500">
                      {a.metric}: observed <strong>{a.observed_value.toFixed(4)}</strong> · threshold {a.threshold.toFixed(4)}
                    </p>
                    {a.notes && <p className="text-xs text-gray-400 mt-0.5 truncate">{a.notes}</p>}
                    <p className="text-xs text-gray-400 mt-0.5">Fired: {fmtDate(a.fired_at)}</p>
                  </div>
                  {a.status !== "resolved" && (
                    <div className="flex gap-1 shrink-0">
                      {a.status === "open" && (
                        <button onClick={() => handleAck(a.id)}
                          className="text-xs bg-yellow-400 hover:bg-yellow-500 text-white px-2 py-1 rounded">
                          Ack
                        </button>
                      )}
                      <button onClick={() => handleResolve(a.id)}
                        className="text-xs bg-green-600 hover:bg-green-700 text-white px-2 py-1 rounded">
                        Resolve
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Errors Tab */}
      {tab === "errors" && (
        <div>
          {kpiLoading ? (
            <p className="text-gray-500 text-sm py-6 text-center">Loading errors…</p>
          ) : !kpis || kpis.recent_errors.length === 0 ? (
            <div className="text-center py-10 text-gray-400">
              <p className="text-4xl mb-2">🎉</p>
              <p className="text-sm">No recent errors in the last {window_} minutes.</p>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-xl border shadow-sm">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 text-gray-600 text-xs uppercase tracking-wide">
                  <tr>
                    <th className="px-4 py-3 text-left">Type</th>
                    <th className="px-4 py-3 text-left">ID</th>
                    <th className="px-4 py-3 text-left">Reason</th>
                    <th className="px-4 py-3 text-left">Supplier</th>
                    <th className="px-4 py-3 text-right">Time</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {kpis.recent_errors.map((e, i) => (
                    <tr key={i} className="hover:bg-gray-50">
                      <td className="px-4 py-3">
                        <span className="px-2 py-0.5 rounded text-xs font-semibold bg-red-50 text-red-600">{e.type}</span>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-gray-500">{e.id.slice(0, 8)}…</td>
                      <td className="px-4 py-3 text-gray-700 max-w-xs truncate">{e.reason}</td>
                      <td className="px-4 py-3 text-gray-500">{e.supplier ?? "—"}</td>
                      <td className="px-4 py-3 text-right text-xs text-gray-400">{fmtDate(e.ts)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
