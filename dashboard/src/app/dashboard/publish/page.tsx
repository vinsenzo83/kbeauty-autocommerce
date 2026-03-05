"use client";
import { useEffect, useState, useCallback } from "react";
import {
  getPublishPreview,
  triggerPublish,
  listPublishJobs,
  getPublishJob,
  PublishPreviewItem,
  PublishJob,
} from "@/lib/api";

/* ── helpers ──────────────────────────────────────────────────────────────── */
function fmtDate(s: string | null | undefined) {
  return s ? s.slice(0, 16).replace("T", " ") : "—";
}

const STATUS_PILL: Record<string, string> = {
  success:  "bg-green-100 text-green-700",
  partial:  "bg-yellow-100 text-yellow-700",
  failed:   "bg-red-100 text-red-700",
  running:  "bg-blue-100 text-blue-700",
  published:"bg-green-100 text-green-700",
  queued:   "bg-gray-100 text-gray-500",
  skipped:  "bg-gray-100 text-gray-400",
};

function Pill({ status }: { status: string }) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${STATUS_PILL[status] ?? "bg-gray-100 text-gray-600"}`}>
      {status}
    </span>
  );
}

/* ── Job Detail Modal ─────────────────────────────────────────────────────── */
function JobModal({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const [job, setJob] = useState<PublishJob | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getPublishJob(jobId)
      .then(setJob)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [jobId]);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-3xl max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="font-bold text-lg">Publish Job Detail</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-2xl leading-none">×</button>
        </div>

        {loading ? (
          <div className="p-8 text-center text-gray-400">Loading…</div>
        ) : !job ? (
          <div className="p-8 text-center text-red-500">Job not found</div>
        ) : (
          <div className="p-4 space-y-4">
            {/* Summary */}
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div><span className="text-gray-500">Job ID:</span> <span className="font-mono text-xs">{job.id}</span></div>
              <div><span className="text-gray-500">Status:</span> <Pill status={job.status} /></div>
              <div><span className="text-gray-500">Dry Run:</span> {job.dry_run ? "✅ Yes" : "🚀 Live"}</div>
              <div><span className="text-gray-500">Channel:</span> {job.channel}</div>
              <div><span className="text-gray-500">Target:</span> {job.target_count}</div>
              <div><span className="text-gray-500">Published:</span> <span className="text-green-600 font-bold">{job.published_count}</span></div>
              <div><span className="text-gray-500">Failed:</span> <span className="text-red-500 font-bold">{job.failed_count}</span></div>
              <div><span className="text-gray-500">Skipped:</span> {job.skipped_count}</div>
              <div className="col-span-2"><span className="text-gray-500">Notes:</span> {job.notes || "—"}</div>
              <div><span className="text-gray-500">Created:</span> {fmtDate(job.created_at)}</div>
            </div>

            {/* Items table */}
            {job.items && job.items.length > 0 && (
              <div>
                <h3 className="font-semibold text-sm mb-2 text-gray-700">Product Items ({job.items.length})</h3>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs border-collapse">
                    <thead>
                      <tr className="bg-gray-50">
                        <th className="text-left p-2 border">Status</th>
                        <th className="text-left p-2 border">Shopify ID</th>
                        <th className="text-left p-2 border">Reason</th>
                        <th className="text-left p-2 border">Updated</th>
                      </tr>
                    </thead>
                    <tbody>
                      {job.items.map((item) => (
                        <tr key={item.id} className="hover:bg-gray-50">
                          <td className="p-2 border"><Pill status={item.status} /></td>
                          <td className="p-2 border font-mono">{item.shopify_product_id || "—"}</td>
                          <td className="p-2 border text-gray-600">{item.reason || "—"}</td>
                          <td className="p-2 border">{fmtDate(item.updated_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Main Page ────────────────────────────────────────────────────────────── */
export default function PublishPage() {
  const [preview, setPreview] = useState<PublishPreviewItem[]>([]);
  const [jobs, setJobs] = useState<PublishJob[]>([]);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [selectedJob, setSelectedJob] = useState<string | null>(null);
  const [showLiveWarning, setShowLiveWarning] = useState(false);
  const [limit, setLimit] = useState(20);

  const loadJobs = useCallback(async () => {
    try {
      const res = await listPublishJobs(50);
      setJobs(res.items);
    } catch (e: unknown) {
      console.error(e);
    }
  }, []);

  useEffect(() => { loadJobs(); }, [loadJobs]);

  const handlePreview = async () => {
    setLoading(true);
    setMsg(null);
    try {
      const res = await getPublishPreview(limit);
      setPreview(res.items);
      setMsg({ ok: true, text: `${res.total} products selected for preview.` });
    } catch (e: unknown) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setLoading(false);
    }
  };

  const handlePublish = async (dryRun: boolean) => {
    setLoading(true);
    setMsg(null);
    setShowLiveWarning(false);
    try {
      const res = await triggerPublish(limit, dryRun);
      setMsg({
        ok: true,
        text: `Job enqueued! Task ID: ${res.task_id} | Dry Run: ${res.dry_run}`,
      });
      setTimeout(loadJobs, 3000); // refresh jobs after 3s
    } catch (e: unknown) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-6 space-y-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">🚀 Auto-Publish Pipeline</h1>
          <p className="text-gray-500 text-sm mt-1">Publish top canonical products to Shopify</p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-sm text-gray-600">Limit:</label>
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="border rounded px-2 py-1 text-sm"
          >
            {[5, 10, 20, 50, 100].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Action Buttons */}
      <div className="flex flex-wrap gap-3">
        <button
          onClick={handlePreview}
          disabled={loading}
          className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 font-medium text-sm"
        >
          🔍 Preview Top {limit}
        </button>
        <button
          onClick={() => handlePublish(true)}
          disabled={loading}
          className="px-4 py-2 bg-yellow-500 text-white rounded-lg hover:bg-yellow-600 disabled:opacity-50 font-medium text-sm"
        >
          🧪 Dry Run Publish ({limit})
        </button>
        <button
          onClick={() => setShowLiveWarning(true)}
          disabled={loading}
          className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 font-medium text-sm"
        >
          🚀 Live Publish ({limit})
        </button>
        <button
          onClick={loadJobs}
          disabled={loading}
          className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 disabled:opacity-50 font-medium text-sm"
        >
          🔄 Refresh Jobs
        </button>
      </div>

      {/* Live Publish Warning Modal */}
      {showLiveWarning && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 shadow-2xl max-w-md w-full mx-4">
            <h2 className="text-xl font-bold text-red-600 mb-3">⚠️ Live Publish Warning</h2>
            <p className="text-gray-700 mb-4">
              This will create or update <strong>{limit} real Shopify products</strong>.
              This action cannot be undone. Are you sure?
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowLiveWarning(false)}
                className="px-4 py-2 bg-gray-200 rounded-lg hover:bg-gray-300 font-medium"
              >
                Cancel
              </button>
              <button
                onClick={() => handlePublish(false)}
                className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 font-medium"
              >
                Confirm Live Publish
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Feedback message */}
      {msg && (
        <div className={`p-3 rounded-lg text-sm ${msg.ok ? "bg-green-50 text-green-700 border border-green-200" : "bg-red-50 text-red-700 border border-red-200"}`}>
          {msg.ok ? "✅" : "❌"} {msg.text}
        </div>
      )}

      {/* Preview Table */}
      {preview.length > 0 && (
        <div>
          <h2 className="text-lg font-semibold text-gray-800 mb-3">Preview: {preview.length} Products</h2>
          <div className="overflow-x-auto border rounded-xl shadow-sm">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="text-left p-3 font-medium text-gray-600">SKU</th>
                  <th className="text-left p-3 font-medium text-gray-600">Name</th>
                  <th className="text-left p-3 font-medium text-gray-600">Brand</th>
                  <th className="text-right p-3 font-medium text-gray-600">Price</th>
                  <th className="text-center p-3 font-medium text-gray-600">Suppliers</th>
                  <th className="text-center p-3 font-medium text-gray-600">Shopify</th>
                </tr>
              </thead>
              <tbody>
                {preview.map((p) => (
                  <tr key={p.canonical_product_id} className="border-t hover:bg-gray-50">
                    <td className="p-3 font-mono text-xs text-gray-500">{p.canonical_sku}</td>
                    <td className="p-3 font-medium">{p.name}</td>
                    <td className="p-3 text-gray-600">{p.brand || "—"}</td>
                    <td className="p-3 text-right font-bold">{p.last_price ? `$${p.last_price.toFixed(2)}` : "—"}</td>
                    <td className="p-3 text-center">
                      <span className={`text-xs font-bold ${p.in_stock_suppliers > 0 ? "text-green-600" : "text-red-500"}`}>
                        {p.in_stock_suppliers}
                      </span>
                    </td>
                    <td className="p-3 text-center text-xs">
                      {p.has_shopify_mapping ? (
                        <span className="text-green-600">✅ Mapped</span>
                      ) : (
                        <span className="text-gray-400">➕ New</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Recent Jobs Table */}
      <div>
        <h2 className="text-lg font-semibold text-gray-800 mb-3">Recent Publish Jobs</h2>
        {jobs.length === 0 ? (
          <p className="text-gray-400 text-sm">No publish jobs yet. Run your first publish above.</p>
        ) : (
          <div className="overflow-x-auto border rounded-xl shadow-sm">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="text-left p-3 font-medium text-gray-600">Status</th>
                  <th className="text-left p-3 font-medium text-gray-600">Channel</th>
                  <th className="text-center p-3 font-medium text-gray-600">Mode</th>
                  <th className="text-center p-3 font-medium text-gray-600">Target</th>
                  <th className="text-center p-3 font-medium text-gray-600 text-green-700">✅ Published</th>
                  <th className="text-center p-3 font-medium text-red-600">❌ Failed</th>
                  <th className="text-left p-3 font-medium text-gray-600">Created</th>
                  <th className="text-center p-3 font-medium text-gray-600">Detail</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.id} className="border-t hover:bg-gray-50">
                    <td className="p-3"><Pill status={j.status} /></td>
                    <td className="p-3">{j.channel}</td>
                    <td className="p-3 text-center">
                      {j.dry_run ? (
                        <span className="text-xs bg-yellow-100 text-yellow-700 px-2 py-0.5 rounded-full">Dry Run</span>
                      ) : (
                        <span className="text-xs bg-red-100 text-red-700 px-2 py-0.5 rounded-full">Live</span>
                      )}
                    </td>
                    <td className="p-3 text-center">{j.target_count}</td>
                    <td className="p-3 text-center font-bold text-green-600">{j.published_count}</td>
                    <td className="p-3 text-center font-bold text-red-500">{j.failed_count}</td>
                    <td className="p-3 text-gray-500">{fmtDate(j.created_at)}</td>
                    <td className="p-3 text-center">
                      <button
                        onClick={() => setSelectedJob(j.id)}
                        className="text-indigo-600 hover:text-indigo-800 text-xs underline"
                      >
                        View
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Job Detail Modal */}
      {selectedJob && (
        <JobModal jobId={selectedJob} onClose={() => setSelectedJob(null)} />
      )}
    </div>
  );
}
