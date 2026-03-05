"use client";
import { useEffect, useState, useCallback } from "react";
import {
  getRepricingPreview,
  triggerRepricing,
  listRepricingRuns,
  getRepricingRun,
  RepricingPreviewItem,
  RepricingRun,
} from "@/lib/api";

function fmtDate(s: string | null | undefined) {
  return s ? s.slice(0, 16).replace("T", " ") : "—";
}

function fmtPrice(v: number | null | undefined) {
  return v != null ? `$${v.toFixed(2)}` : "—";
}

function fmtDelta(delta: number | null | undefined) {
  if (delta == null) return "—";
  const color = delta > 0 ? "text-green-600" : delta < 0 ? "text-red-500" : "text-gray-400";
  const sign  = delta > 0 ? "+" : "";
  return <span className={color}>{sign}{delta.toFixed(2)}</span>;
}

const STATUS_PILL: Record<string, string> = {
  success: "bg-green-100 text-green-700",
  partial: "bg-yellow-100 text-yellow-700",
  failed:  "bg-red-100 text-red-700",
  running: "bg-blue-100 text-blue-700",
  updated: "bg-green-100 text-green-700",
  skipped: "bg-gray-100 text-gray-400",
};
function Pill({ status }: { status: string }) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${STATUS_PILL[status] ?? "bg-gray-100 text-gray-600"}`}>
      {status}
    </span>
  );
}

/* ── Run Detail Modal ─────────────────────────────────────────────────────── */
function RunModal({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [run, setRun] = useState<RepricingRun | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getRepricingRun(runId)
      .then(setRun)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [runId]);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-3xl max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="font-bold text-lg">Repricing Run Detail</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-2xl">×</button>
        </div>
        {loading ? (
          <div className="p-8 text-center text-gray-400">Loading…</div>
        ) : !run ? (
          <div className="p-8 text-center text-red-500">Run not found</div>
        ) : (
          <div className="p-4 space-y-4">
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div><span className="text-gray-500">Status:</span> <Pill status={run.status} /></div>
              <div><span className="text-gray-500">Mode:</span> {run.dry_run ? "🧪 Dry Run" : "🚀 Live"}</div>
              <div><span className="text-gray-500">Updated:</span> <span className="font-bold text-green-600">{run.updated_count}</span></div>
              <div><span className="text-gray-500">Skipped:</span> {run.skipped_count}</div>
              <div><span className="text-gray-500">Failed:</span> <span className="text-red-500">{run.failed_count}</span></div>
              <div><span className="text-gray-500">Created:</span> {fmtDate(run.created_at)}</div>
              <div className="col-span-2"><span className="text-gray-500">Notes:</span> {run.notes || "—"}</div>
            </div>
            {run.items && run.items.length > 0 && (
              <div>
                <h3 className="font-semibold text-sm mb-2">Items ({run.items.length})</h3>
                <table className="w-full text-xs border-collapse">
                  <thead><tr className="bg-gray-50">
                    <th className="p-2 border text-left">Status</th>
                    <th className="p-2 border text-right">Old</th>
                    <th className="p-2 border text-right">Recommended</th>
                    <th className="p-2 border text-right">Applied</th>
                    <th className="p-2 border text-left">Reason</th>
                  </tr></thead>
                  <tbody>
                    {run.items.map((i) => (
                      <tr key={i.id} className="hover:bg-gray-50">
                        <td className="p-2 border"><Pill status={i.status} /></td>
                        <td className="p-2 border text-right">{fmtPrice(i.old_price)}</td>
                        <td className="p-2 border text-right font-bold">{fmtPrice(i.recommended_price)}</td>
                        <td className="p-2 border text-right">{fmtPrice(i.applied_price)}</td>
                        <td className="p-2 border text-gray-500">{i.reason || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Main Page ────────────────────────────────────────────────────────────── */
export default function RepricingPage() {
  const [preview, setPreview]     = useState<RepricingPreviewItem[]>([]);
  const [runs, setRuns]           = useState<RepricingRun[]>([]);
  const [loading, setLoading]     = useState(false);
  const [msg, setMsg]             = useState<{ ok: boolean; text: string } | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [showLiveWarn, setShowLiveWarn] = useState(false);
  const [limit, setLimit]         = useState(50);

  const loadRuns = useCallback(async () => {
    try { const r = await listRepricingRuns(50); setRuns(r.items); } catch { /* */ }
  }, []);

  useEffect(() => { loadRuns(); }, [loadRuns]);

  const handlePreview = async () => {
    setLoading(true); setMsg(null);
    try {
      const r = await getRepricingPreview(limit);
      setPreview(r.items);
      setMsg({ ok: true, text: `${r.total} products in preview.` });
    } catch (e: unknown) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally { setLoading(false); }
  };

  const handleApply = async (dryRun: boolean) => {
    setLoading(true); setMsg(null); setShowLiveWarn(false);
    try {
      const r = await triggerRepricing(limit, dryRun);
      setMsg({ ok: true, text: `Repricing job enqueued. Task: ${r.task_id} | Dry: ${r.dry_run}` });
      setTimeout(loadRuns, 4000);
    } catch (e: unknown) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally { setLoading(false); }
  };

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">📊 Market Price Intelligence & Repricing</h1>
          <p className="text-gray-500 text-sm mt-1">Auto-reprice Shopify products using competitor data</p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-sm text-gray-600">Limit:</label>
          <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}
            className="border rounded px-2 py-1 text-sm">
            {[10, 20, 50, 100].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
      </div>

      {/* Buttons */}
      <div className="flex flex-wrap gap-3">
        <button onClick={handlePreview} disabled={loading}
          className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 font-medium text-sm">
          🔍 Preview Repricing
        </button>
        <button onClick={() => handleApply(true)} disabled={loading}
          className="px-4 py-2 bg-yellow-500 text-white rounded-lg hover:bg-yellow-600 disabled:opacity-50 font-medium text-sm">
          🧪 Dry-Run Apply
        </button>
        <button onClick={() => setShowLiveWarn(true)} disabled={loading}
          className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 font-medium text-sm">
          🚀 Live Apply
        </button>
        <button onClick={loadRuns} disabled={loading}
          className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 disabled:opacity-50 font-medium text-sm">
          🔄 Refresh Runs
        </button>
      </div>

      {/* Live Warning */}
      {showLiveWarn && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 shadow-2xl max-w-md w-full mx-4">
            <h2 className="text-xl font-bold text-red-600 mb-3">⚠️ Live Repricing Warning</h2>
            <p className="text-gray-700 mb-4">This will update <strong>real Shopify variant prices</strong> for up to {limit} products. Always run dry-run first.</p>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setShowLiveWarn(false)} className="px-4 py-2 bg-gray-200 rounded-lg hover:bg-gray-300">Cancel</button>
              <button onClick={() => handleApply(false)} className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700">Confirm Live</button>
            </div>
          </div>
        </div>
      )}

      {/* Feedback */}
      {msg && (
        <div className={`p-3 rounded-lg text-sm ${msg.ok ? "bg-green-50 text-green-700 border border-green-200" : "bg-red-50 text-red-700 border border-red-200"}`}>
          {msg.ok ? "✅" : "❌"} {msg.text}
        </div>
      )}

      {/* Preview Table */}
      {preview.length > 0 && (
        <div>
          <h2 className="text-lg font-semibold text-gray-800 mb-3">Preview ({preview.length})</h2>
          <div className="overflow-x-auto border rounded-xl shadow-sm">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="text-left p-3 font-medium text-gray-600">Product</th>
                  <th className="text-right p-3 font-medium text-gray-600">Cost</th>
                  <th className="text-right p-3 font-medium text-gray-600">Comp. Min</th>
                  <th className="text-right p-3 font-medium text-gray-600">Comp. Median</th>
                  <th className="text-right p-3 font-medium text-gray-600">Current</th>
                  <th className="text-right p-3 font-medium text-gray-600">Recommended</th>
                  <th className="text-right p-3 font-medium text-gray-600">Delta</th>
                  <th className="text-right p-3 font-medium text-gray-600">Margin%</th>
                  <th className="text-left p-3 font-medium text-gray-600">Note</th>
                </tr>
              </thead>
              <tbody>
                {preview.map((p) => (
                  <tr key={p.canonical_product_id} className="border-t hover:bg-gray-50">
                    <td className="p-3">
                      <div className="font-medium text-sm">{p.name}</div>
                      <div className="text-xs text-gray-400">{p.canonical_sku}</div>
                    </td>
                    <td className="p-3 text-right">{fmtPrice(p.supplier_cost)}</td>
                    <td className="p-3 text-right text-gray-500">{fmtPrice(p.competitor_min)}</td>
                    <td className="p-3 text-right text-gray-500">{fmtPrice(p.competitor_median)}</td>
                    <td className="p-3 text-right">{fmtPrice(p.current_price)}</td>
                    <td className="p-3 text-right font-bold text-indigo-700">{fmtPrice(p.recommended_price)}</td>
                    <td className="p-3 text-right">{fmtDelta(p.delta)}</td>
                    <td className="p-3 text-right">
                      {p.expected_margin_pct != null ? `${p.expected_margin_pct.toFixed(1)}%` : "—"}
                    </td>
                    <td className="p-3 text-xs text-gray-400">
                      {p.skip_reason || p.repricing_reason || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Recent Runs */}
      <div>
        <h2 className="text-lg font-semibold text-gray-800 mb-3">Recent Repricing Runs</h2>
        {runs.length === 0 ? (
          <p className="text-gray-400 text-sm">No runs yet. Trigger one above.</p>
        ) : (
          <div className="overflow-x-auto border rounded-xl shadow-sm">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="text-left p-3 font-medium text-gray-600">Status</th>
                  <th className="text-center p-3 font-medium text-gray-600">Mode</th>
                  <th className="text-center p-3 font-medium text-gray-600">Updated</th>
                  <th className="text-center p-3 font-medium text-gray-600">Skipped</th>
                  <th className="text-center p-3 font-medium text-red-600">Failed</th>
                  <th className="text-left p-3 font-medium text-gray-600">Created</th>
                  <th className="text-center p-3 font-medium text-gray-600">Detail</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} className="border-t hover:bg-gray-50">
                    <td className="p-3"><Pill status={r.status} /></td>
                    <td className="p-3 text-center">{r.dry_run ? <span className="text-xs bg-yellow-100 text-yellow-700 px-2 py-0.5 rounded-full">Dry</span> : <span className="text-xs bg-red-100 text-red-700 px-2 py-0.5 rounded-full">Live</span>}</td>
                    <td className="p-3 text-center font-bold text-green-600">{r.updated_count}</td>
                    <td className="p-3 text-center">{r.skipped_count}</td>
                    <td className="p-3 text-center text-red-500 font-bold">{r.failed_count}</td>
                    <td className="p-3 text-gray-500">{fmtDate(r.created_at)}</td>
                    <td className="p-3 text-center">
                      <button onClick={() => setSelectedRun(r.id)} className="text-indigo-600 hover:text-indigo-800 text-xs underline">View</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {selectedRun && <RunModal runId={selectedRun} onClose={() => setSelectedRun(null)} />}
    </div>
  );
}
