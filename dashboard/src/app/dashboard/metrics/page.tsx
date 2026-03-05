"use client";
import { useEffect, useState } from "react";
import { getMetrics, MetricsResponse } from "@/lib/api";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Cell,
} from "recharts";

/* ── MetricCard ─────────────────────────────────────────────────────────────── */
function MetricCard({
  label,
  value,
  sub,
  color = "text-slate-800",
  bg = "bg-white",
}: {
  label: string;
  value: number | string;
  sub?: string;
  color?: string;
  bg?: string;
}) {
  return (
    <div className={`${bg} rounded-xl border border-slate-100 shadow-sm p-5`}>
      <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">{label}</p>
      <p className={`text-4xl font-black mt-2 ${color}`}>{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-1">{sub}</p>}
    </div>
  );
}

/* ── chart config ───────────────────────────────────────────────────────────── */
const BAR_COLORS: Record<string, string> = {
  pending:    "#6366f1",
  processing: "#06b6d4",
  shipped:    "#22c55e",
  failed:     "#ef4444",
  canceled:   "#9ca3af",
};

/* ── main page ──────────────────────────────────────────────────────────────── */
export default function MetricsPage() {
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [error, setError]     = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastAt, setLastAt]   = useState("");

  const load = () => {
    setLoading(true);
    setError(null);
    getMetrics()
      .then(m => { setMetrics(m); setLastAt(new Date().toLocaleTimeString()); })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  const chartData = metrics
    ? [
        { name: "Pending",    value: metrics.pending,    key: "pending"    },
        { name: "Processing", value: metrics.processing,  key: "processing" },
        { name: "Shipped",    value: metrics.shipped,     key: "shipped"    },
        { name: "Failed",     value: metrics.failed,      key: "failed"     },
        { name: "Canceled",   value: metrics.canceled,    key: "canceled"   },
      ]
    : [];

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* ── header ─────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-800">Metrics</h1>
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

      {/* ── skeleton ────────────────────────────────────────────────────────── */}
      {loading && !metrics && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-28 bg-slate-100 rounded-xl animate-pulse" />
          ))}
        </div>
      )}

      {/* ── KPI cards ───────────────────────────────────────────────────────── */}
      {metrics && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <MetricCard
              label="Orders Today"
              value={metrics.orders_today}
              sub="Seoul day boundary"
              color="text-blue-600"
            />
            <MetricCard
              label="Pending"
              value={metrics.pending}
              sub="Received + Validated + Placing"
              color="text-indigo-600"
            />
            <MetricCard
              label="Processing"
              value={metrics.processing}
              sub="Placed — awaiting tracking"
              color="text-cyan-600"
            />
            <MetricCard
              label="Failed"
              value={metrics.failed}
              sub="All time"
              color={metrics.failed > 0 ? "text-red-600" : "text-slate-600"}
              bg={metrics.failed > 0 ? "bg-red-50" : "bg-white"}
            />
          </div>

          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <MetricCard
              label="Shipped"
              value={metrics.shipped}
              sub="All time"
              color="text-green-600"
            />
            <MetricCard
              label="Canceled"
              value={metrics.canceled}
              sub="All time"
              color="text-gray-500"
            />
            <MetricCard
              label="Total Orders"
              value={metrics.total}
              sub="All time, all statuses"
              color="text-slate-700"
            />
          </div>

          {/* ── bar chart ─────────────────────────────────────────────────── */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-100 p-6">
            <h2 className="text-sm font-semibold text-slate-600 mb-5">Order Status Distribution</h2>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={chartData} barCategoryGap="35%">
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 12, fill: "#64748b" }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 12, fill: "#64748b" }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ borderRadius: 8, border: "1px solid #e2e8f0", boxShadow: "0 2px 8px rgba(0,0,0,.06)" }}
                  cursor={{ fill: "#f8fafc" }}
                />
                <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                  {chartData.map(entry => (
                    <Cell key={entry.key} fill={BAR_COLORS[entry.key] ?? "#94a3b8"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* ── summary row ─────────────────────────────────────────────── */}
          {metrics.total > 0 && (
            <div className="bg-white rounded-xl shadow-sm border border-slate-100 p-5">
              <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-4">Status Breakdown (%)</h2>
              <div className="space-y-2">
                {chartData.map(entry => {
                  const pct = metrics.total > 0 ? Math.round((entry.value / metrics.total) * 100) : 0;
                  return (
                    <div key={entry.key} className="flex items-center gap-3 text-sm">
                      <span className="w-24 text-slate-600 font-medium">{entry.name}</span>
                      <div className="flex-1 h-2 rounded-full bg-slate-100 overflow-hidden">
                        <div
                          className="h-full rounded-full"
                          style={{ width: `${pct}%`, backgroundColor: BAR_COLORS[entry.key] }}
                        />
                      </div>
                      <span className="w-10 text-right text-slate-500 text-xs">{pct}%</span>
                      <span className="w-10 text-right text-slate-400 text-xs">{entry.value}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
