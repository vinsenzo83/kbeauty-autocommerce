'use client'

import { useEffect, useState, useCallback } from 'react'

// ─── Types ────────────────────────────────────────────────────────────────────

interface TrendSource {
  id: string
  source: string
  name: string
  is_enabled: boolean
  created_at: string | null
}

interface TrendItem {
  id: string
  source_id: string
  external_id: string
  title: string
  brand: string | null
  category: string | null
  rank: number | null
  price: number | null
  rating: number | null
  review_count: number | null
  observed_at: string | null
}

interface MentionSignal {
  id: string
  canonical_product_id: string
  source_id: string
  mentions: number
  velocity: number | null
  score: number | null
  observed_at: string | null
}

interface RunResult {
  task_id?: string
  dry_run?: boolean
  status?: string
  message?: string
  amazon_items?: number
  tiktok_signals?: number
  errors?: string[]
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const API = '/admin'

async function apiFetch<T>(path: string, opts?: RequestInit): Promise<T> {
  const token = typeof window !== 'undefined' ? localStorage.getItem('admin_token') : ''
  const res = await fetch(`${API}${path}`, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts?.headers ?? {}),
    },
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail ?? res.statusText)
  }
  return res.json()
}

const fmtDate = (s: string | null) =>
  s ? new Date(s).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) : '-'

const fmtScore = (n: number | null) =>
  n != null ? (n * 100).toFixed(1) + '%' : '-'

const fmtNum = (n: number | null) =>
  n != null ? n.toLocaleString() : '-'

// ─── Score bar ────────────────────────────────────────────────────────────────

function ScoreBar({ score }: { score: number | null }) {
  const pct = score != null ? Math.round(score * 100) : 0
  const color =
    pct >= 70 ? 'bg-green-500' : pct >= 40 ? 'bg-yellow-500' : 'bg-red-400'
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 bg-gray-200 rounded h-2">
        <div className={`${color} h-2 rounded`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-gray-600">{pct}%</span>
    </div>
  )
}

// ─── Run Modal ────────────────────────────────────────────────────────────────

function RunModal({
  onClose,
  onRun,
}: {
  onClose: () => void
  onRun: (dryRun: boolean, limit: number) => Promise<void>
}) {
  const [dryRun, setDryRun] = useState(true)
  const [limit, setLimit] = useState(200)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<RunResult | null>(null)
  const [err, setErr] = useState('')

  const handleRun = async () => {
    setLoading(true)
    setErr('')
    try {
      await onRun(dryRun, limit)
      setResult({ status: 'queued', message: '트렌드 수집 태스크가 큐에 추가되었습니다.' })
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl p-6 w-full max-w-md shadow-xl">
        <h2 className="text-lg font-semibold mb-4">트렌드 수집 실행 (Sprint 18)</h2>

        <div className="space-y-4">
          <div>
            <label className="text-sm font-medium text-gray-700">실행 모드</label>
            <div className="mt-1 flex gap-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  checked={dryRun}
                  onChange={() => setDryRun(true)}
                  className="text-blue-600"
                />
                <span className="text-sm">Dry-run (저장 안 함)</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  checked={!dryRun}
                  onChange={() => setDryRun(false)}
                  className="text-blue-600"
                />
                <span className="text-sm text-red-600 font-medium">Live (DB 저장)</span>
              </label>
            </div>
          </div>

          <div>
            <label className="text-sm font-medium text-gray-700">
              수집 한도 (limit)
            </label>
            <input
              type="number"
              min={1}
              max={1000}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="mt-1 w-full border rounded px-3 py-2 text-sm"
            />
          </div>

          {err && (
            <div className="bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded text-sm">
              {err}
            </div>
          )}
          {result && (
            <div className="bg-green-50 border border-green-200 text-green-700 px-3 py-2 rounded text-sm">
              {result.message}
            </div>
          )}
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-600 border rounded hover:bg-gray-50"
          >
            닫기
          </button>
          <button
            onClick={handleRun}
            disabled={loading}
            className={`px-4 py-2 text-sm rounded text-white font-medium ${
              dryRun
                ? 'bg-blue-600 hover:bg-blue-700'
                : 'bg-orange-600 hover:bg-orange-700'
            } disabled:opacity-50`}
          >
            {loading ? '실행 중...' : dryRun ? '🔍 Dry-run' : '🚀 Live 실행'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function TrendsV2Page() {
  // Sources
  const [sources, setSources] = useState<TrendSource[]>([])
  // Amazon items
  const [items, setItems] = useState<TrendItem[]>([])
  const [itemFilter, setItemFilter] = useState<'amazon' | 'all'>('amazon')
  // Mention signals
  const [mentions, setMentions] = useState<MentionSignal[]>([])
  // Run modal
  const [showRun, setShowRun] = useState(false)
  // Loading states
  const [loadingSources, setLoadingSources] = useState(false)
  const [loadingItems, setLoadingItems] = useState(false)
  const [loadingMentions, setLoadingMentions] = useState(false)
  const [lastRun, setLastRun] = useState<string | null>(null)
  const [err, setErr] = useState('')

  const loadSources = useCallback(async () => {
    setLoadingSources(true)
    try {
      const data = await apiFetch<{ sources: TrendSource[] }>('/trends/v2/sources')
      setSources(data.sources)
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setLoadingSources(false)
    }
  }, [])

  const loadItems = useCallback(async (src?: string) => {
    setLoadingItems(true)
    try {
      const qs = src ? `?source=${src}&limit=50` : '?limit=50'
      const data = await apiFetch<{ items: TrendItem[] }>(`/trends/v2/items${qs}`)
      setItems(data.items)
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setLoadingItems(false)
    }
  }, [])

  const loadMentions = useCallback(async () => {
    setLoadingMentions(true)
    try {
      const data = await apiFetch<{ mentions: MentionSignal[] }>('/trends/v2/mentions?limit=50')
      setMentions(data.mentions)
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setLoadingMentions(false)
    }
  }, [])

  useEffect(() => {
    loadSources()
    loadItems('amazon')
    loadMentions()
  }, [loadSources, loadItems, loadMentions])

  const handleRun = async (dryRun: boolean, limit: number) => {
    const qs = `?dry_run=${dryRun}&limit=${limit}`
    await apiFetch<RunResult>(`/trends/v2/run${qs}`, { method: 'POST' })
    setLastRun(new Date().toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }))
  }

  const handleItemFilterChange = (v: 'amazon' | 'all') => {
    setItemFilter(v)
    loadItems(v === 'amazon' ? 'amazon' : undefined)
  }

  return (
    <div className="p-6 space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">트렌드 신호 v2</h1>
          <p className="text-sm text-gray-500 mt-1">
            Sprint 18 – Amazon 베스트셀러 & TikTok 언급 신호
          </p>
          {lastRun && (
            <p className="text-xs text-green-600 mt-1">마지막 실행: {lastRun}</p>
          )}
        </div>
        <button
          onClick={() => setShowRun(true)}
          className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 font-medium shadow"
        >
          🚀 수집 실행
        </button>
      </div>

      {err && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm">
          {err}
          <button onClick={() => setErr('')} className="ml-3 underline">닫기</button>
        </div>
      )}

      {/* Sources */}
      <div className="bg-white rounded-xl shadow-sm border p-5">
        <h2 className="text-base font-semibold text-gray-800 mb-4">📡 트렌드 소스</h2>
        {loadingSources ? (
          <p className="text-sm text-gray-400">로딩 중...</p>
        ) : sources.length === 0 ? (
          <p className="text-sm text-gray-400">등록된 소스가 없습니다. 먼저 수집을 실행하세요.</p>
        ) : (
          <div className="flex flex-wrap gap-3">
            {sources.map((s) => (
              <div
                key={s.id}
                className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm ${
                  s.is_enabled
                    ? 'border-green-200 bg-green-50 text-green-800'
                    : 'border-gray-200 bg-gray-50 text-gray-500'
                }`}
              >
                <span className={s.is_enabled ? 'text-green-500' : 'text-gray-400'}>●</span>
                <span className="font-medium">{s.name}</span>
                <span className="text-xs text-gray-400">({s.source})</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Amazon Bestsellers */}
      <div className="bg-white rounded-xl shadow-sm border">
        <div className="flex items-center justify-between p-5 border-b">
          <h2 className="text-base font-semibold text-gray-800">🛒 Amazon 베스트셀러</h2>
          <div className="flex gap-2">
            {(['amazon', 'all'] as const).map((v) => (
              <button
                key={v}
                onClick={() => handleItemFilterChange(v)}
                className={`px-3 py-1 text-xs rounded font-medium ${
                  itemFilter === v
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                {v === 'amazon' ? 'Amazon' : '전체'}
              </button>
            ))}
          </div>
        </div>
        <div className="overflow-x-auto">
          {loadingItems ? (
            <p className="p-5 text-sm text-gray-400">로딩 중...</p>
          ) : items.length === 0 ? (
            <p className="p-5 text-sm text-gray-400">
              데이터가 없습니다. 수집 실행 후 확인하세요.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 text-left text-xs text-gray-500 uppercase tracking-wide">
                  <th className="px-4 py-3">순위</th>
                  <th className="px-4 py-3">상품명</th>
                  <th className="px-4 py-3">브랜드</th>
                  <th className="px-4 py-3">카테고리</th>
                  <th className="px-4 py-3 text-right">가격</th>
                  <th className="px-4 py-3 text-right">평점</th>
                  <th className="px-4 py-3 text-right">리뷰</th>
                  <th className="px-4 py-3">수집 시각</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {items.map((item) => (
                  <tr key={item.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium text-gray-900">
                      {item.rank != null ? `#${item.rank}` : '-'}
                    </td>
                    <td className="px-4 py-3 max-w-xs truncate text-gray-800" title={item.title}>
                      {item.title}
                    </td>
                    <td className="px-4 py-3 text-gray-600">{item.brand ?? '-'}</td>
                    <td className="px-4 py-3 text-gray-500 text-xs">{item.category ?? '-'}</td>
                    <td className="px-4 py-3 text-right text-gray-700">
                      {item.price != null ? `$${item.price.toFixed(2)}` : '-'}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {item.rating != null ? (
                        <span className="text-yellow-600">★ {item.rating.toFixed(1)}</span>
                      ) : '-'}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600">
                      {fmtNum(item.review_count)}
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-400">
                      {fmtDate(item.observed_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* TikTok Mention Signals */}
      <div className="bg-white rounded-xl shadow-sm border">
        <div className="flex items-center justify-between p-5 border-b">
          <h2 className="text-base font-semibold text-gray-800">📱 TikTok 언급 리더보드</h2>
          <button
            onClick={loadMentions}
            className="text-xs text-blue-600 hover:underline"
          >
            새로고침
          </button>
        </div>
        <div className="overflow-x-auto">
          {loadingMentions ? (
            <p className="p-5 text-sm text-gray-400">로딩 중...</p>
          ) : mentions.length === 0 ? (
            <p className="p-5 text-sm text-gray-400">
              데이터가 없습니다. 수집 실행 후 확인하세요.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 text-left text-xs text-gray-500 uppercase tracking-wide">
                  <th className="px-4 py-3">상품 ID</th>
                  <th className="px-4 py-3 text-right">언급수</th>
                  <th className="px-4 py-3 text-right">속도</th>
                  <th className="px-4 py-3">트렌드 점수</th>
                  <th className="px-4 py-3">수집 시각</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {mentions.map((m) => (
                  <tr key={m.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-mono text-xs text-gray-600">
                      {m.canonical_product_id.slice(0, 8)}…
                    </td>
                    <td className="px-4 py-3 text-right font-semibold text-gray-800">
                      {fmtNum(m.mentions)}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600">
                      {m.velocity != null ? m.velocity.toFixed(2) : '-'}
                    </td>
                    <td className="px-4 py-3">
                      <ScoreBar score={m.score} />
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-400">
                      {fmtDate(m.observed_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Run Modal */}
      {showRun && (
        <RunModal onClose={() => setShowRun(false)} onRun={handleRun} />
      )}
    </div>
  )
}
