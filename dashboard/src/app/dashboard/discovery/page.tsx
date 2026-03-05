"use client";
import { useEffect, useState, useCallback } from "react";

/* ── Types ──────────────────────────────────────────────────────────────────── */
interface TrendSignal {
  id:           string;
  source:       string;
  external_id:  string;
  name:         string;
  brand:        string | null;
  category:     string | null;
  trend_score:  number;
  collected_at: string | null;
}

interface ProductCandidate {
  id:                   string | null;
  canonical_product_id: string;
  canonical_sku?:       string;
  name?:                string;
  brand?:               string | null;
  last_price?:          number | null;
  final_score:          number | null;
  trend_score:          number | null;
  margin_score:         number | null;
  competition_score:    number | null;
  supplier_score:       number | null;
  content_score:        number | null;
  status:               string;
  source?:              string;
  already_published?:   boolean;
}

interface DiscoveryRunResult {
  status:              string;
  dry_run:             boolean;
  signals_collected:   number;
  signals_matched:     number;
  candidates_created:  number;
  candidates_updated:  number;
  candidates_rejected: number;
  top_count:           number;
  top_candidates:      ProductCandidate[];
  errors:              string[];
}

/* ── Helpers ────────────────────────────────────────────────────────────────── */
const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts.headers ?? {}),
    },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return res.json();
}

function fmtDate(s: string | null | undefined) {
  return s ? s.slice(0, 16).replace("T", " ") : "—";
}

function fmtScore(v: number | null | undefined) {
  return v != null ? (v * 100).toFixed(1) + "%" : "—";
}

function fmtPrice(v: number | null | undefined) {
  return v != null ? `$${v.toFixed(2)}` : "—";
}

const SOURCE_PILL: Record<string, string> = {
  tiktok:             "bg-pink-100 text-pink-700",
  amazon_bestsellers: "bg-yellow-100 text-yellow-700",
  fallback:           "bg-gray-100 text-gray-500",
  discovery:          "bg-blue-100 text-blue-700",
};

const STATUS_PILL: Record<string, string> = {
  candidate: "bg-blue-100 text-blue-700",
  published: "bg-green-100 text-green-700",
  rejected:  "bg-gray-100 text-gray-400",
};

function Pill({ label, color }: { label: string; color?: string }) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${color ?? "bg-gray-100 text-gray-600"}`}>
      {label}
    </span>
  );
}

/* ── Score Bar ──────────────────────────────────────────────────────────────── */
function ScoreBar({ value, color = "bg-blue-400" }: { value: number | null; color?: string }) {
  const pct = value != null ? Math.round(value * 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 bg-gray-200 rounded-full h-1.5">
        <div className={`${color} h-1.5 rounded-full`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-gray-500">{pct}%</span>
    </div>
  );
}

/* ── Main Page ──────────────────────────────────────────────────────────────── */
export default function DiscoveryPage() {
  const [activeTab, setActiveTab] = useState<"candidates" | "trends">("candidates");
  const [candidates, setCandidates] = useState<ProductCandidate[]>([]);
  const [trends, setTrends] = useState<TrendSignal[]>([]);
  const [loading, setLoading] = useState(false);
  const [runResult, setRunResult] = useState<DiscoveryRunResult | null>(null);
  const [runLoading, setRunLoading] = useState(false);
  const [publishLoading, setPublishLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [sourceFilter, setSourceFilter] = useState<string>("all");

  /* ── Data loaders ─────────────────────────────────────────────────────────── */
  const loadCandidates = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<{ total: number; items: ProductCandidate[] }>(
        "/admin/discovery/candidates?limit=50"
      );
      setCandidates(data.items);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load candidates");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadTrends = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const url = sourceFilter !== "all"
        ? `/admin/discovery/trends?limit=50&source=${encodeURIComponent(sourceFilter)}`
        : "/admin/discovery/trends?limit=50";
      const data = await apiFetch<{ total: number; items: TrendSignal[] }>(url);
      setTrends(data.items);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load trends");
    } finally {
      setLoading(false);
    }
  }, [sourceFilter]);

  useEffect(() => {
    if (activeTab === "candidates") loadCandidates();
    else loadTrends();
  }, [activeTab, loadCandidates, loadTrends]);

  /* ── Run Discovery ────────────────────────────────────────────────────────── */
  const handleRunDiscovery = async (dryRun: boolean) => {
    setRunLoading(true);
    setError(null);
    setRunResult(null);
    setSuccessMsg(null);
    try {
      const result = await apiFetch<DiscoveryRunResult>(
        `/admin/discovery/run?dry_run=${dryRun}&top_n=50`,
        { method: "POST" }
      );
      setRunResult(result);
      setSuccessMsg(
        dryRun
          ? `✓ Dry run complete – ${result.signals_matched} matched, ${result.top_count} top candidates`
          : `✓ Discovery complete – ${result.candidates_created} created, ${result.candidates_updated} updated`
      );
      if (!dryRun) await loadCandidates();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Discovery run failed");
    } finally {
      setRunLoading(false);
    }
  };

  /* ── Publish Candidate ────────────────────────────────────────────────────── */
  const handlePublish = async (candidateId: string, dryRun: boolean) => {
    setPublishLoading(candidateId);
    setError(null);
    setSuccessMsg(null);
    try {
      const result = await apiFetch<{ name: string; publish_status: string; dry_run: boolean }>(
        `/admin/discovery/publish/${candidateId}?dry_run=${dryRun}`,
        { method: "POST" }
      );
      setSuccessMsg(
        dryRun
          ? `✓ Dry-run publish: "${result.name}" → status=${result.publish_status}`
          : `✓ Published: "${result.name}" → Shopify`
      );
      await loadCandidates();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Publish failed");
    } finally {
      setPublishLoading(null);
    }
  };

  /* ── Render ───────────────────────────────────────────────────────────────── */
  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">🔭 AI Product Discovery</h1>
          <p className="text-sm text-gray-500 mt-1">
            Sprint 15 – Trend ingestion, scoring, and candidate management
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <button
            onClick={() => handleRunDiscovery(true)}
            disabled={runLoading}
            className="px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg text-sm font-medium disabled:opacity-50 transition"
          >
            {runLoading ? "Running…" : "🔬 Dry Run"}
          </button>
          <button
            onClick={() => handleRunDiscovery(false)}
            disabled={runLoading}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 transition"
          >
            {runLoading ? "Running…" : "🚀 Run Discovery"}
          </button>
          <button
            onClick={() => activeTab === "candidates" ? loadCandidates() : loadTrends()}
            disabled={loading}
            className="px-4 py-2 bg-white border border-gray-200 hover:border-gray-400 text-gray-600 rounded-lg text-sm disabled:opacity-50 transition"
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* Alerts */}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm">
          {error}
        </div>
      )}
      {successMsg && (
        <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded-lg text-sm">
          {successMsg}
        </div>
      )}

      {/* Run Result Summary */}
      {runResult && (
        <div className="bg-blue-50 border border-blue-200 rounded-xl p-4">
          <h3 className="font-semibold text-blue-800 mb-3">
            Discovery Run {runResult.dry_run ? "(Dry Run)" : "(Live)"}
          </h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
            {[
              { label: "Signals Collected", value: runResult.signals_collected },
              { label: "Matched to Canonical", value: runResult.signals_matched },
              { label: "Candidates Created", value: runResult.candidates_created },
              { label: "Candidates Updated", value: runResult.candidates_updated },
            ].map(({ label, value }) => (
              <div key={label} className="bg-white rounded-lg p-3 text-center">
                <div className="text-2xl font-bold text-blue-700">{value}</div>
                <div className="text-gray-500 text-xs mt-1">{label}</div>
              </div>
            ))}
          </div>
          {runResult.errors.length > 0 && (
            <div className="mt-3 text-xs text-red-600">
              <strong>Errors:</strong> {runResult.errors.join("; ")}
            </div>
          )}
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-4">
          {(["candidates", "trends"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`pb-2 text-sm font-medium capitalize border-b-2 transition ${
                activeTab === tab
                  ? "border-blue-600 text-blue-600"
                  : "border-transparent text-gray-500 hover:text-gray-700"
              }`}
            >
              {tab === "candidates" ? `📦 Candidates (${candidates.length})` : `📡 Trends (${trends.length})`}
            </button>
          ))}
        </nav>
      </div>

      {/* Loading */}
      {loading && (
        <div className="text-center text-gray-400 py-8 text-sm animate-pulse">Loading…</div>
      )}

      {/* ── Candidates Tab ───────────────────────────────────────────────────── */}
      {!loading && activeTab === "candidates" && (
        <>
          {candidates.length === 0 ? (
            <div className="text-center text-gray-400 py-16">
              <div className="text-4xl mb-3">🔭</div>
              <p className="text-sm">No candidates yet. Click <strong>Run Discovery</strong> to populate.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="bg-gray-50 text-left">
                    <th className="px-3 py-2 font-medium text-gray-600 whitespace-nowrap">Product</th>
                    <th className="px-3 py-2 font-medium text-gray-600 text-right">Price</th>
                    <th className="px-3 py-2 font-medium text-gray-600">Final Score</th>
                    <th className="px-3 py-2 font-medium text-gray-600">Trend</th>
                    <th className="px-3 py-2 font-medium text-gray-600">Margin</th>
                    <th className="px-3 py-2 font-medium text-gray-600">Competition</th>
                    <th className="px-3 py-2 font-medium text-gray-600">Supplier</th>
                    <th className="px-3 py-2 font-medium text-gray-600">Content</th>
                    <th className="px-3 py-2 font-medium text-gray-600">Status</th>
                    <th className="px-3 py-2 font-medium text-gray-600">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {candidates.map((c, idx) => (
                    <tr key={c.id ?? idx} className="hover:bg-gray-50 transition">
                      <td className="px-3 py-3">
                        <div className="font-medium text-gray-800 max-w-xs truncate">
                          {c.name ?? c.canonical_product_id.slice(0, 8)}
                        </div>
                        {c.brand && (
                          <div className="text-xs text-gray-400">{c.brand}</div>
                        )}
                        {c.source && (
                          <Pill label={c.source} color={SOURCE_PILL[c.source] ?? "bg-gray-100 text-gray-500"} />
                        )}
                      </td>
                      <td className="px-3 py-3 text-right font-mono text-gray-700">
                        {fmtPrice(c.last_price)}
                      </td>
                      <td className="px-3 py-3">
                        <div className="font-bold text-blue-700">{fmtScore(c.final_score)}</div>
                        <ScoreBar value={c.final_score} color="bg-blue-500" />
                      </td>
                      <td className="px-3 py-3">
                        <ScoreBar value={c.trend_score} color="bg-pink-400" />
                      </td>
                      <td className="px-3 py-3">
                        <ScoreBar value={c.margin_score} color="bg-green-400" />
                      </td>
                      <td className="px-3 py-3">
                        <ScoreBar value={c.competition_score} color="bg-yellow-400" />
                      </td>
                      <td className="px-3 py-3">
                        <ScoreBar value={c.supplier_score} color="bg-purple-400" />
                      </td>
                      <td className="px-3 py-3">
                        <ScoreBar value={c.content_score} color="bg-gray-400" />
                      </td>
                      <td className="px-3 py-3">
                        <Pill label={c.status} color={STATUS_PILL[c.status] ?? "bg-gray-100 text-gray-500"} />
                        {c.already_published && (
                          <div className="mt-1">
                            <Pill label="on Shopify" color="bg-emerald-100 text-emerald-700" />
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-3">
                        {c.id && c.status === "candidate" && (
                          <div className="flex flex-col gap-1">
                            <button
                              onClick={() => handlePublish(c.id!, true)}
                              disabled={publishLoading === c.id}
                              className="px-2 py-1 bg-gray-100 hover:bg-gray-200 text-gray-600 rounded text-xs disabled:opacity-50"
                            >
                              {publishLoading === c.id ? "…" : "Dry Publish"}
                            </button>
                            <button
                              onClick={() => handlePublish(c.id!, false)}
                              disabled={publishLoading === c.id || c.already_published}
                              className="px-2 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded text-xs disabled:opacity-50"
                            >
                              {publishLoading === c.id ? "…" : "Publish →"}
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {/* ── Trends Tab ───────────────────────────────────────────────────────── */}
      {!loading && activeTab === "trends" && (
        <>
          {/* Source filter */}
          <div className="flex gap-2 items-center text-sm">
            <span className="text-gray-500">Source:</span>
            {["all", "tiktok", "amazon_bestsellers"].map((s) => (
              <button
                key={s}
                onClick={() => setSourceFilter(s)}
                className={`px-3 py-1 rounded-full text-xs font-medium transition ${
                  sourceFilter === s
                    ? "bg-blue-600 text-white"
                    : "bg-gray-100 hover:bg-gray-200 text-gray-600"
                }`}
              >
                {s === "all" ? "All" : s === "tiktok" ? "TikTok" : "Amazon"}
              </button>
            ))}
          </div>

          {trends.length === 0 ? (
            <div className="text-center text-gray-400 py-16">
              <div className="text-4xl mb-3">📡</div>
              <p className="text-sm">No trend signals yet. Run discovery to collect trends.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="bg-gray-50 text-left">
                    <th className="px-3 py-2 font-medium text-gray-600">Product</th>
                    <th className="px-3 py-2 font-medium text-gray-600">Brand</th>
                    <th className="px-3 py-2 font-medium text-gray-600">Category</th>
                    <th className="px-3 py-2 font-medium text-gray-600">Source</th>
                    <th className="px-3 py-2 font-medium text-gray-600">Trend Score</th>
                    <th className="px-3 py-2 font-medium text-gray-600">Collected</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {trends.map((t) => (
                    <tr key={t.id} className="hover:bg-gray-50 transition">
                      <td className="px-3 py-3">
                        <div className="font-medium text-gray-800 max-w-xs truncate">{t.name}</div>
                        <div className="text-xs text-gray-400 font-mono">{t.external_id}</div>
                      </td>
                      <td className="px-3 py-3 text-gray-600">{t.brand ?? "—"}</td>
                      <td className="px-3 py-3 text-gray-500">{t.category ?? "—"}</td>
                      <td className="px-3 py-3">
                        <Pill label={t.source} color={SOURCE_PILL[t.source] ?? "bg-gray-100 text-gray-500"} />
                      </td>
                      <td className="px-3 py-3">
                        <div className="font-bold text-pink-700">{t.trend_score.toFixed(2)}</div>
                        <ScoreBar value={t.trend_score / 10} color="bg-pink-400" />
                      </td>
                      <td className="px-3 py-3 text-gray-400 text-xs whitespace-nowrap">
                        {fmtDate(t.collected_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
