"use client";
import { useEffect, useState } from "react";
import { getKPI, getAlerts, getChart, KPI, Alerts, ChartRow } from "@/lib/api";
import {
  ResponsiveContainer, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from "recharts";

// ── KPI Card ──────────────────────────────────────────────────────────────────
function KpiCard({ label, value, sub, accent }: {
  label: string; value: string | number; sub?: string; accent?: string;
}) {
  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-100 p-5">
      <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">{label}</p>
      <p className={`text-3xl font-bold mt-1 ${accent ?? "text-slate-800"}`}>{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-1">{sub}</p>}
    </div>
  );
}

// ── Alert Badge ───────────────────────────────────────────────────────────────
function AlertBadge({ count, label }: { count: number; label: string }) {
  const color = count > 0 ? "bg-red-100 text-red-700 border-red-200" : "bg-green-50 text-green-700 border-green-200";
  return (
    <div className={`rounded-lg border px-4 py-3 flex justify-between items-center ${color}`}>
      <span className="text-sm font-medium">{label}</span>
      <span className="text-2xl font-bold">{count}</span>
    </div>
  );
}

export default function DashboardOverview() {
  const [kpi, setKpi]       = useState<KPI | null>(null);
  const [alerts, setAlerts] = useState<Alerts | null>(null);
  const [chart, setChart]   = useState<ChartRow[]>([]);
  const [error, setError]   = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getKPI(), getAlerts(), getChart(7)])
      .then(([k, a, c]) => { setKpi(k); setAlerts(a); setChart(c); })
      .catch((e) => setError(e.message));
  }, []);

  if (error)
    return (
      <div className="p-6 text-red-600">
        Failed to load dashboard: {error}.{" "}
        <a href="/login" className="underline">Re-login</a>
      </div>
    );

  return (
    <div className="p-6 space-y-6 max-w-6xl mx-auto">
      <h1 className="text-2xl font-bold text-slate-800">Overview</h1>

      {/* KPI Grid */}
      {kpi ? (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
          <KpiCard label="Orders Today"   value={kpi.orders_today} />
          <KpiCard
            label="Revenue Today"
            value={`₩${kpi.revenue_today.toLocaleString()}`}
          />
          <KpiCard
            label="Avg Margin"
            value={`${kpi.avg_margin_pct.toFixed(1)}%`}
            accent={kpi.avg_margin_pct < 15 ? "text-red-600" : "text-green-600"}
          />
          <KpiCard
            label="Failed Today"
            value={kpi.failed_today}
            accent={kpi.failed_today > 0 ? "text-red-600" : "text-slate-800"}
          />
          <KpiCard
            label="Stale Tracking"
            value={kpi.tracking_stale_count}
            accent={kpi.tracking_stale_count > 0 ? "text-amber-600" : "text-slate-800"}
          />
          <KpiCard label="Open Tickets" value={kpi.open_tickets_count} />
        </div>
      ) : (
        <div className="grid grid-cols-6 gap-4">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="h-24 bg-slate-100 rounded-xl animate-pulse" />
          ))}
        </div>
      )}

      {/* Alerts */}
      {alerts && (
        <div>
          <h2 className="text-lg font-semibold text-slate-700 mb-3">Alerts</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <AlertBadge count={alerts.tracking_stale.length}         label="Stale Tracking" />
            <AlertBadge count={alerts.margin_guard_violations.length} label="Margin Violations" />
            <AlertBadge count={alerts.bot_failures_last_hour}         label="Bot Failures / 1h" />
            <AlertBadge count={alerts.queue_backlog ?? 0}             label="Queue Backlog" />
          </div>
        </div>
      )}

      {/* 7-day Chart */}
      {chart.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-slate-100 p-5">
          <h2 className="text-lg font-semibold text-slate-700 mb-4">7-Day Trend</h2>
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={chart}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} />
              <YAxis yAxisId="left"  tick={{ fontSize: 11 }} />
              <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
              <Tooltip />
              <Legend />
              <Area yAxisId="left"  type="monotone" dataKey="orders"  stroke="#3b82f6" fill="#dbeafe" name="Orders" />
              <Area yAxisId="right" type="monotone" dataKey="revenue" stroke="#10b981" fill="#d1fae5" name="Revenue (₩)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
