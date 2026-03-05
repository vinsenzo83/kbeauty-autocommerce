"use client";
import { useEffect, useState, useCallback } from "react";

// ── API types ─────────────────────────────────────────────────────────────────
interface TrendSignal {
  id: string;
  source: string;
  external_id: string;
  name: string;
  brand: string | null;
  category: string | null;
  trend_score: number;
  collected_at: string | null;
}

interface ProductCandidate {
  id: string;
  canonical_product_id: string;
  trend_score: number;
  margin_score: number;
  competition_score: number;
  supplier_score: number;
  content_score: number;
  final_score: number;
  status: string;
  notes: string | null;
  created_at: string | null;
}

interface DiscoveryRunResult {
  status: string;
  dry_run: boolean;
  signals_collected: number;
  signals_matched: number;
  candidates_created: number;
  candidates_updated: number;
  candidates_rejected: number;
  top_count: number;
  top_candidates: ProductCandidate[];
  errors: string[];
}

// ── Helpers ───────────────────────────────────────────────────────────────────
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

function fmtScore(v: number | null | undefined) {
  return v != null ? (v * 100).toFixed(1) + "%" : "—";
}

function scoreColor(v: number): string {
  if (v >= 0.7) return "text-green-600 font-semibold";
  if (v >= 0.4) return "text-yellow-600";
  return "text-red-500";
}

const SOURCE_BADGE: Record<string, string> = {
  tiktok:              "bg-pink-100 text-pink-700",
  amazon_bestsellers:  "bg-orange-100 text-orange-700",
};

function SourceBadge({ source }: { source: string }) {
  return (
    <span
      className={`px-2 py-0.5 rounded-full text-xs font-semibold ${
        SOURCE_BADGE[source] ?? "bg-gray-100 text-gray-600"
      }`}
    >
      {source === "tiktok" ? "TikTok" : source === "amazon_bestsellers" ? "Amazon" : source}
    </span>
  );
}

const STATUS_PILL: Record<string, string> = {
  candidate: "bg-blue-100 text-blue-700",
  published: "bg-green-100 text-green-700",
  rejected:  "bg-gray-100 text-gray-400",
};
function StatusPill({ status }: { status: string }) {
  return (
    <span
      className={`px-2 py-0.5 rounded-full text-xs font-semibold ${
        STATUS_PILL[status] ?? "bg-gray-100 text-gray-600"
      }`}
    >
      {status}
    </span>
  );
}

// ── ScoreBar component ────────────────────────────────────────────────────────
function ScoreBar({ value, label }: { value: number; label: string }) {
  const pct = Math.round(value * 100);
  const color =
    pct >= 70 ? "bg-green-500" : pct >= 40 ? "bg-yellow-400" : "bg-red-400";
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-20 text-gray-500 shrink-0">{label}</span>
      <div className="flex-1 bg-gray-100 rounded h-2">
        <div className={`${color} h-2 rounded`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-8 text-right text-gray-600">{pct}%</span>
    </div>
  );
}

// ── CandidateDetail Modal ─────────────────────────────────────────────────────
function CandidateModal({
  candidate,
  onClose,
  onPublish,
}: {
  candidate: ProductCandidate;
  onClose: () => void;
  onPublish: (id: string, dryRun: boolean) => Promise<void>;
}) {
  const [publishing, setPublishing] = useState(false);
  const [pubResult, setPubResult] = useState<string | null>(null);

  const handlePublish = async (dryRun: boolean) => {
    setPublishing(true);
    setPubResult(null);
    try {
      const res = await apiFetch<{ publish_status: string; published_count: number }>(
        `/admin/discovery/publish/${candidate.id}?dry_run=${dryRun}`,
        { method: "POST" }
      );
      setPubResult(
        `✅ ${dryRun ? "[DRY RUN] " : ""}publish_status=${res.publish_status} published=${res.published_count}`
      );
      await onPublish(candidate.id, dryRun);
    } catch (e: unknown) {
      setPubResult(`❌ ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setPublishing(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg">
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="font-bold text-lg">Candidate Detail</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-2xl"
          >
            ×
          </button>
        </div>
        <div className="p-5 space-y-4">
          <div className="text-xs text-gray-400 break-all">
            canonical_id: {candidate.canonical_product_id}
          </div>
          <div className="space-y-2">
            <ScoreBar value={candidate.trend_score}       label="Trend"       />
            <ScoreBar value={candidate.margin_score}      label="Margin"      />
            <ScoreBar value={candidate.competition_score} label="Competition" />
            <ScoreBar value={candidate.supplier_score}    label="Supplier"    />
            <ScoreBar value={candidate.content_score}     label="Content"     />
          </div>
          <div className="flex items-center gap-2 pt-1">
            <span className="font-bold text-sm text-gray-700">Final Score:</span>
            <span className={`text-lg font-bold ${scoreColor(candidate.final_score)}`}>
              {fmtScore(candidate.final_score)}
            </span>
            <StatusPill status={candidate.status} />
          </div>
          {candidate.notes && (
            <p className="text-xs text-gray-500 italic">{candidate.notes}</p>
          )}
          <p className="text-xs text-gray-400">Created: {fmtDate(candidate.created_at)}</p>

          {candidate.status === "candidate" && (
            <div className="flex gap-2 pt-2">
              <button
                onClick={() => handlePublish(true)}
                disabled={publishing}
                className="flex-1 bg-yellow-400 hover:bg-yellow-500 text-white text-sm font-semibold py-2 rounded-lg disabled:opacity-50"
              >
                {publishing ? "…" : "Dry Run Publish"}
              </button>
              <button
                onClick={() => handlePublish(false)}
                disabled={publishing}
                className="flex-1 bg-green-600 hover:bg-green-700 text-white text-sm font-semibold py-2 rounded-lg disabled:opacity-50"
              >
                {publishing ? "…" : "🚀 Live Publish"}
              </button>
            </div>
          )}
          {pubResult && (
            <p className="text-xs font-mono bg-gray-50 p-2 rounded border">{pubResult}</p>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function DiscoveryPage() {
  const [tab, setTab] = useState<"candidates" | "trends">("candidates");

  // Candidates state
  const [candidates, setCandidates] = useState<ProductCandidate[]>([]);
  const [candLoading, setCandLoading] = useState(true);
  const [candError, setCandError]   = useState<string | null>(null);
  const [selectedCand, setSelectedCand] = useState<ProductCandidate | null>(null);

  // Trends state
  const [trends, setTrends]         = useState<TrendSignal[]>([]);
  const [trendLoading, setTrendLoading] = useState(true);
  const [trendError, setTrendError] = useState<string | null>(null);

  // Run pipeline state
  const [running, setRunning]       = useState(false);
  const [runResult, setRunResult]   = useState<DiscoveryRunResult | null>(null);
  const [runError, setRunError]     = useState<string | null>(null);

  // Load candidates
  const loadCandidates = useCallback(async () => {
    setCandLoading(true);
    setCandError(null);
    try {
      const data = await apiFetch<{ total: number; items: ProductCandidate[] }>(
        "/admin/discovery/candidates?limit=50"
      );
      setCandidates(data.items);
    } catch (e: unknown) {
      setCandError(e instanceof Error ? e.message : String(e));
    } finally {
      setCandLoading(false);
    }
  }, []);

  // Load trends
  const loadTrends = useCallback(async () => {
    setTrendLoading(true);
    setTrendError(null);
    try {
      const data = await apiFetch<{ total: number; items: TrendSignal[] }>(
        "/admin/discovery/trends?limit=50"
      );
      setTrends(data.items);
    } catch (e: unknown) {
      setTrendError(e instanceof Error ? e.message : String(e));
    } finally {
      setTrendLoading(false);
    }
  }, []);

  useEffect(() => {
    loadCandidates();
    loadTrends();
  }, [loadCandidates, loadTrends]);

  // Run discovery pipeline
  const handleRun = async (dryRun: boolean) => {
    setRunning(true);
    setRunResult(null);
    setRunError(null);
    try {
      const data = await apiFetch<DiscoveryRunResult>(
        `/admin/discovery/run?dry_run=${dryRun}&top_n=50`,
        { method: "POST" }
      );
      setRunResult(data);
      // Refresh data after successful live run
      if (!dryRun) {
        await loadCandidates();
        await loadTrends();
      }
    } catch (e: unknown) {
      setRunError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">
            🔍 AI Product Discovery
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Sprint 15 — Trend signals → Scored candidates → Shopify publish
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => handleRun(true)}
            disabled={running}
            className="bg-yellow-400 hover:bg-yellow-500 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-50 transition"
          >
            {running ? "⏳ Running…" : "🧪 Dry Run"}
          </button>
          <button
            onClick={() => handleRun(false)}
            disabled={running}
            className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-50 transition"
          >
            {running ? "⏳ Running…" : "▶ Run Discovery"}
          </button>
        </div>
      </div>

      {/* Run result banner */}
      {runError && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
          ❌ {runError}
        </div>
      )}
      {runResult && (
        <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-4 text-sm space-y-1">
          <p className="font-semibold text-indigo-800">
            {runResult.dry_run ? "🧪 Dry Run" : "✅ Live Run"} Complete
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-2">
            {[
              ["Signals Collected", runResult.signals_collected],
              ["Matched to Canonical", runResult.signals_matched],
              ["Candidates Created", runResult.candidates_created],
              ["Candidates Updated", runResult.candidates_updated],
            ].map(([label, val]) => (
              <div key={String(label)} className="bg-white rounded p-2 text-center border border-indigo-100">
                <p className="text-xl font-bold text-indigo-700">{val}</p>
                <p className="text-xs text-gray-500">{label}</p>
              </div>
            ))}
          </div>
          {runResult.errors.length > 0 && (
            <details className="mt-2">
              <summary className="text-xs text-red-600 cursor-pointer">
                {runResult.errors.length} errors
              </summary>
              <ul className="mt-1 space-y-0.5">
                {runResult.errors.map((e, i) => (
                  <li key={i} className="text-xs text-red-500 font-mono">{e}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {/* Tabs */}
      <div className="border-b flex gap-4">
        {(["candidates", "trends"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`pb-2 text-sm font-medium capitalize border-b-2 transition ${
              tab === t
                ? "border-indigo-600 text-indigo-700"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {t === "candidates" ? `📋 Candidates (${candidates.length})` : `📡 Trend Signals (${trends.length})`}
          </button>
        ))}
      </div>

      {/* Candidates Tab */}
      {tab === "candidates" && (
        <div>
          {candLoading ? (
            <p className="text-gray-500 text-sm py-6 text-center">Loading candidates…</p>
          ) : candError ? (
            <p className="text-red-500 text-sm py-4">Error: {candError}</p>
          ) : candidates.length === 0 ? (
            <div className="text-center py-10 text-gray-400">
              <p className="text-4xl mb-2">🔍</p>
              <p className="text-sm">No candidates yet. Click "Run Discovery" to start.</p>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-xl border shadow-sm">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 text-gray-600 text-xs uppercase tracking-wide">
                  <tr>
                    <th className="px-4 py-3 text-left">Canonical ID</th>
                    <th className="px-4 py-3 text-right">Trend</th>
                    <th className="px-4 py-3 text-right">Margin</th>
                    <th className="px-4 py-3 text-right">Competition</th>
                    <th className="px-4 py-3 text-right">Supplier</th>
                    <th className="px-4 py-3 text-right">Content</th>
                    <th className="px-4 py-3 text-right font-bold">Final</th>
                    <th className="px-4 py-3 text-center">Status</th>
                    <th className="px-4 py-3 text-center">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {candidates.map((c) => (
                    <tr
                      key={c.id}
                      className="hover:bg-indigo-50 cursor-pointer transition"
                      onClick={() => setSelectedCand(c)}
                    >
                      <td className="px-4 py-3 font-mono text-xs text-gray-500 max-w-[160px] truncate">
                        {c.canonical_product_id.slice(0, 8)}…
                      </td>
                      <td className={`px-4 py-3 text-right ${scoreColor(c.trend_score)}`}>
                        {fmtScore(c.trend_score)}
                      </td>
                      <td className={`px-4 py-3 text-right ${scoreColor(c.margin_score)}`}>
                        {fmtScore(c.margin_score)}
                      </td>
                      <td className={`px-4 py-3 text-right ${scoreColor(c.competition_score)}`}>
                        {fmtScore(c.competition_score)}
                      </td>
                      <td className={`px-4 py-3 text-right ${scoreColor(c.supplier_score)}`}>
                        {fmtScore(c.supplier_score)}
                      </td>
                      <td className={`px-4 py-3 text-right ${scoreColor(c.content_score)}`}>
                        {fmtScore(c.content_score)}
                      </td>
                      <td className={`px-4 py-3 text-right text-base font-bold ${scoreColor(c.final_score)}`}>
                        {fmtScore(c.final_score)}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <StatusPill status={c.status} />
                      </td>
                      <td className="px-4 py-3 text-center">
                        {c.status === "candidate" && (
                          <button
                            onClick={(e) => { e.stopPropagation(); setSelectedCand(c); }}
                            className="text-xs bg-green-600 hover:bg-green-700 text-white px-2 py-1 rounded transition"
                          >
                            Publish
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Trends Tab */}
      {tab === "trends" && (
        <div>
          {trendLoading ? (
            <p className="text-gray-500 text-sm py-6 text-center">Loading trends…</p>
          ) : trendError ? (
            <p className="text-red-500 text-sm py-4">Error: {trendError}</p>
          ) : trends.length === 0 ? (
            <div className="text-center py-10 text-gray-400">
              <p className="text-4xl mb-2">📡</p>
              <p className="text-sm">No trend signals yet. Run Discovery to collect signals.</p>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-xl border shadow-sm">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 text-gray-600 text-xs uppercase tracking-wide">
                  <tr>
                    <th className="px-4 py-3 text-left">Source</th>
                    <th className="px-4 py-3 text-left">Product Name</th>
                    <th className="px-4 py-3 text-left">Brand</th>
                    <th className="px-4 py-3 text-left">Category</th>
                    <th className="px-4 py-3 text-right">Trend Score</th>
                    <th className="px-4 py-3 text-right">Collected</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {trends.map((t) => (
                    <tr key={t.id} className="hover:bg-gray-50 transition">
                      <td className="px-4 py-3">
                        <SourceBadge source={t.source} />
                      </td>
                      <td className="px-4 py-3 max-w-xs">
                        <p className="truncate font-medium text-gray-800">{t.name}</p>
                        <p className="text-xs text-gray-400 font-mono">{t.external_id}</p>
                      </td>
                      <td className="px-4 py-3 text-gray-600">{t.brand ?? "—"}</td>
                      <td className="px-4 py-3 text-gray-500 text-xs">{t.category ?? "—"}</td>
                      <td className="px-4 py-3 text-right">
                        <span className={`font-bold ${scoreColor(t.trend_score / 10)}`}>
                          {t.trend_score.toFixed(2)}
                        </span>
                        <span className="text-xs text-gray-400 ml-1">/ 10</span>
                      </td>
                      <td className="px-4 py-3 text-right text-xs text-gray-400">
                        {fmtDate(t.collected_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Candidate Detail Modal */}
      {selectedCand && (
        <CandidateModal
          candidate={selectedCand}
          onClose={() => setSelectedCand(null)}
          onPublish={async (_id, _dryRun) => {
            await loadCandidates();
            setSelectedCand(null);
          }}
        />
      )}
    </div>
  );
}
