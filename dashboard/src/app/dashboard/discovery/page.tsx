"use client";
import { useEffect, useState, useCallback } from "react";

// ── API types ─────────────────────────────────────────────────────────────────

interface CandidateV2 {
  id: string;
  canonical_product_id: string;
  canonical_sku?: string;
  name?: string;
  brand?: string | null;
  last_price?: number | null;
  score: number;
  amazon_rank_score: number;
  supplier_rank_score: number;
  margin_score: number;
  review_score: number;
  competition_score: number;
  status: string;
  notes?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

interface DiscoveryRunResult {
  dry_run: boolean;
  candidates_generated: number;
  top_n: number;
  top_candidates: CandidateV2[];
}

interface CandidateListResult {
  total: number;
  items: CandidateV2[];
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("admin_token");
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(`/admin${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
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

const STATUS_PILL: Record<string, string> = {
  candidate: "bg-blue-100 text-blue-700",
  published: "bg-green-100 text-green-700",
  rejected:  "bg-red-100  text-red-700",
};

function Pill({ status }: { status: string }) {
  return (
    <span
      className={`px-2 py-0.5 rounded-full text-xs font-semibold ${
        STATUS_PILL[status] ?? "bg-gray-100 text-gray-500"
      }`}
    >
      {status}
    </span>
  );
}

// Score bar component
function ScoreBar({ value, color = "bg-blue-500" }: { value: number; color?: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-2 bg-gray-100 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-gray-600 w-8">{pct}%</span>
    </div>
  );
}

// Candidate detail drawer
function CandidateDrawer({
  candidate,
  onClose,
  onReject,
}: {
  candidate: CandidateV2;
  onClose: () => void;
  onReject: (id: string) => Promise<void>;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  async function handleReject() {
    setRejecting(true);
    try {
      await onReject(candidate.id);
      onClose();
    } finally {
      setRejecting(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg p-6 space-y-4">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">
              {candidate.name ?? candidate.canonical_sku ?? candidate.canonical_product_id.slice(0, 8)}
            </h2>
            {candidate.brand && (
              <p className="text-sm text-gray-500">{candidate.brand}</p>
            )}
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
        </div>

        {/* Score breakdown */}
        <div className="bg-gray-50 rounded-lg p-4 space-y-3">
          <h3 className="text-sm font-semibold text-gray-700 mb-2">Score Breakdown</h3>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <span className="text-gray-500">Amazon Rank</span>
              <div className="mt-1"><ScoreBar value={candidate.amazon_rank_score} color="bg-orange-400" /></div>
            </div>
            <div>
              <span className="text-gray-500">Supplier Rank</span>
              <div className="mt-1"><ScoreBar value={candidate.supplier_rank_score} color="bg-purple-400" /></div>
            </div>
            <div>
              <span className="text-gray-500">Margin</span>
              <div className="mt-1"><ScoreBar value={candidate.margin_score} color="bg-green-500" /></div>
            </div>
            <div>
              <span className="text-gray-500">Review</span>
              <div className="mt-1"><ScoreBar value={candidate.review_score} color="bg-yellow-400" /></div>
            </div>
            <div>
              <span className="text-gray-500">Competition</span>
              <div className="mt-1"><ScoreBar value={candidate.competition_score} color="bg-red-400" /></div>
            </div>
            <div className="col-span-2 pt-1 border-t border-gray-200">
              <span className="text-gray-700 font-semibold">Final Score</span>
              <div className="mt-1"><ScoreBar value={candidate.score} color="bg-blue-600" /></div>
            </div>
          </div>
        </div>

        {/* Metadata */}
        <div className="text-sm text-gray-600 grid grid-cols-2 gap-1">
          <span className="text-gray-400">SKU</span><span>{candidate.canonical_sku ?? "—"}</span>
          <span className="text-gray-400">Price</span><span>{fmtPrice(candidate.last_price)}</span>
          <span className="text-gray-400">Status</span><span><Pill status={candidate.status} /></span>
          <span className="text-gray-400">Created</span><span>{fmtDate(candidate.created_at)}</span>
        </div>

        {candidate.notes && (
          <p className="text-xs text-gray-400 italic">{candidate.notes}</p>
        )}

        {/* Actions */}
        {candidate.status === "candidate" && (
          <div className="flex gap-2 pt-2 border-t border-gray-100">
            <textarea
              className="flex-1 border border-gray-200 rounded px-2 py-1 text-xs resize-none h-8"
              placeholder="Rejection reason (optional)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <button
              onClick={handleReject}
              disabled={rejecting}
              className="px-3 py-1 rounded bg-red-500 text-white text-xs font-semibold hover:bg-red-600 disabled:opacity-50"
            >
              {rejecting ? "Rejecting…" : "Reject"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function DiscoveryV2Page() {
  const [candidates, setCandidates]       = useState<CandidateV2[]>([]);
  const [loading, setLoading]             = useState(true);
  const [running, setRunning]             = useState(false);
  const [error, setError]                 = useState<string | null>(null);
  const [runResult, setRunResult]         = useState<DiscoveryRunResult | null>(null);
  const [statusFilter, setStatusFilter]   = useState<string>("candidate");
  const [selectedCandidate, setSelected]  = useState<CandidateV2 | null>(null);
  const [lastRefreshed, setLastRefreshed] = useState<string>("");

  const loadCandidates = useCallback(async (status = statusFilter) => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<CandidateListResult>(
        `/discovery/v2/candidates?limit=50&status=${status}`
      );
      setCandidates(data.items ?? []);
      setLastRefreshed(new Date().toLocaleTimeString());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => { loadCandidates(); }, [loadCandidates]);

  async function handleRun(dry: boolean) {
    setRunning(true);
    setError(null);
    setRunResult(null);
    try {
      const data = await apiFetch<DiscoveryRunResult>(
        `/discovery/v2/run?limit=20&dry_run=${dry}`,
        { method: "POST" }
      );
      setRunResult(data);
      await loadCandidates();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  async function handleReject(candidateId: string) {
    const reason = (window.prompt("Rejection reason (optional):") || undefined);
    await apiFetch(
      `/discovery/v2/candidates/${candidateId}/reject${reason ? `?reason=${encodeURIComponent(reason)}` : ""}`,
      { method: "POST" }
    );
    await loadCandidates();
  }

  // Stats
  const total      = candidates.length;
  const published  = candidates.filter((c) => c.status === "published").length;
  const rejected   = candidates.filter((c) => c.status === "rejected").length;
  const avgScore   = total > 0
    ? candidates.reduce((s, c) => s + c.score, 0) / total
    : 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            🤖 AI Discovery Engine v2
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Sprint 17 – Automated top-20 product selection pipeline
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => handleRun(true)}
            disabled={running}
            className="px-4 py-2 rounded-lg bg-blue-100 text-blue-700 text-sm font-semibold hover:bg-blue-200 disabled:opacity-50"
          >
            {running ? "Running…" : "🔍 Run Dry-Run"}
          </button>
          <button
            onClick={() => handleRun(false)}
            disabled={running}
            className="px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50"
          >
            {running ? "Running…" : "🚀 Run Live"}
          </button>
          <button
            onClick={() => loadCandidates()}
            disabled={loading}
            className="px-3 py-2 rounded-lg border border-gray-200 text-gray-500 text-sm hover:bg-gray-50 disabled:opacity-50"
          >
            ↻
          </button>
        </div>
      </div>

      {/* Stats cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          { label: "Total Candidates", value: total,                     color: "text-blue-600" },
          { label: "Published",        value: published,                  color: "text-green-600" },
          { label: "Rejected",         value: rejected,                   color: "text-red-500" },
          { label: "Avg Score",        value: fmtScore(avgScore / 100),   color: "text-purple-600" },
        ].map(({ label, value, color }) => (
          <div key={label} className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
            <p className="text-xs text-gray-400">{label}</p>
            <p className={`text-2xl font-bold ${color}`}>{value}</p>
          </div>
        ))}
      </div>

      {/* Score formula info */}
      <div className="bg-blue-50 border border-blue-100 rounded-xl p-4 text-sm text-blue-800">
        <strong>Score formula:</strong> score = Amazon×0.35 + Supplier×0.25 + Margin×0.20 + Review×0.10 + Competition×0.10
      </div>

      {/* Run result */}
      {runResult && (
        <div className="bg-green-50 border border-green-200 rounded-xl p-4">
          <h3 className="font-semibold text-green-800 mb-2">
            {runResult.dry_run ? "🔍 Dry-Run" : "🚀 Live Run"} completed
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-sm text-green-700">
            <span>Candidates generated: <strong>{runResult.candidates_generated}</strong></span>
            <span>Top N: <strong>{runResult.top_n}</strong></span>
          </div>
          {runResult.top_candidates?.length > 0 && (
            <div className="mt-2 text-xs text-green-600">
              Top scores: {runResult.top_candidates.slice(0, 5).map((c) =>
                `${(c.score * 100).toFixed(0)}%`
              ).join(", ")}
            </div>
          )}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-xl p-3 text-sm">
          {error}
        </div>
      )}

      {/* Filter */}
      <div className="flex items-center gap-3">
        <span className="text-sm text-gray-500">Filter:</span>
        {["candidate", "published", "rejected", "all"].map((s) => (
          <button
            key={s}
            onClick={() => { setStatusFilter(s); loadCandidates(s); }}
            className={`px-3 py-1 rounded-full text-xs font-semibold border ${
              statusFilter === s
                ? "bg-indigo-600 text-white border-indigo-600"
                : "bg-white text-gray-500 border-gray-200 hover:bg-gray-50"
            }`}
          >
            {s}
          </button>
        ))}
        {lastRefreshed && (
          <span className="ml-auto text-xs text-gray-300">Refreshed {lastRefreshed}</span>
        )}
      </div>

      {/* Candidate table */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
        {loading ? (
          <div className="p-12 text-center text-gray-400 text-sm">Loading candidates…</div>
        ) : candidates.length === 0 ? (
          <div className="p-12 text-center space-y-2">
            <p className="text-gray-400 text-sm">No candidates found.</p>
            <p className="text-gray-300 text-xs">
              Run the discovery pipeline to generate product candidates.
            </p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-100">
              <tr>
                {["Product", "Score", "Amazon", "Supplier", "Margin", "Review", "Comp.", "Price", "Status", ""].map(
                  (h) => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-gray-500">
                      {h}
                    </th>
                  )
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {candidates.map((c) => (
                <tr
                  key={c.id}
                  className="hover:bg-gray-50 transition-colors cursor-pointer"
                  onClick={() => setSelected(c)}
                >
                  <td className="px-4 py-3">
                    <div className="font-medium text-gray-900 truncate max-w-[180px]">
                      {c.name ?? c.canonical_sku ?? c.canonical_product_id.slice(0, 8)}
                    </div>
                    {c.brand && (
                      <div className="text-xs text-gray-400">{c.brand}</div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1">
                      <div
                        className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-white"
                        style={{
                          background: `hsl(${Math.round(c.score * 120)},70%,50%)`,
                        }}
                      >
                        {Math.round(c.score * 100)}
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">
                    {(c.amazon_rank_score * 100).toFixed(0)}%
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">
                    {(c.supplier_rank_score * 100).toFixed(0)}%
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">
                    {(c.margin_score * 100).toFixed(0)}%
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">
                    {(c.review_score * 100).toFixed(0)}%
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">
                    {(c.competition_score * 100).toFixed(0)}%
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">
                    {fmtPrice(c.last_price)}
                  </td>
                  <td className="px-4 py-3">
                    <Pill status={c.status} />
                  </td>
                  <td className="px-4 py-3">
                    {c.status === "candidate" && (
                      <button
                        onClick={(e) => { e.stopPropagation(); handleReject(c.id); }}
                        className="text-xs px-2 py-0.5 rounded border border-red-200 text-red-500 hover:bg-red-50"
                      >
                        Reject
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Candidate drawer */}
      {selectedCandidate && (
        <CandidateDrawer
          candidate={selectedCandidate}
          onClose={() => setSelected(null)}
          onReject={handleReject}
        />
      )}

      {/* Instructions */}
      <div className="bg-gray-50 rounded-xl p-4 text-xs text-gray-500 space-y-1">
        <p><strong>🔍 Run Dry-Run</strong> — Score all canonical products without publishing to Shopify.</p>
        <p><strong>🚀 Run Live</strong> — Score + publish top-20 to Shopify (requires SHOPIFY_ACCESS_TOKEN).</p>
        <p><strong>Reject</strong> — Mark a candidate as rejected; it will be excluded from future auto-publish.</p>
        <p className="text-gray-400">
          Score formula: Amazon×0.35 + Supplier×0.25 + Margin×0.20 + Review×0.10 + Competition×0.10
        </p>
      </div>
    </div>
  );
}
