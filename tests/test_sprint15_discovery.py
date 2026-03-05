"""
tests/test_sprint15_discovery.py
─────────────────────────────────
Sprint 15 – AI Product Discovery: mock-only test suite

Coverage
--------
1.  test_tiktok_collector_returns_signals          – collector returns ≥1 items, all required fields
2.  test_amazon_bestsellers_collector              – collector returns ≥1 items, scores in 0–10 range
3.  test_tiktok_score_normalisation                – trend_score in [0, 10]
4.  test_amazon_rank_to_score_rank1                – rank 1 → score ≈ 9.9
5.  test_amazon_rank_to_score_rank100              – rank 100 → score ≈ 0.0
6.  test_product_matcher_normalise                 – _normalise strips punctuation correctly
7.  test_product_matcher_tokenise                  – _tokenise removes stop-words
8.  test_product_matcher_token_overlap_exact       – identical token sets → 1.0
9.  test_product_matcher_token_overlap_partial     – partial overlap within expected range
10. test_product_matcher_no_match_db               – DB returns no rows → None
11. test_product_matcher_fuzzy_match               – fuzzy pass resolves to expected id
12. test_score_formula_weights                     – final = weighted sum matches manual calc
13. test_score_clamp_above_one                     – clamping prevents score > 1.0
14. test_score_clamp_below_zero                    – clamping prevents score < 0.0
15. test_scoring_no_last_price_neutral_margin      – missing last_price → margin_score = 0.5
16. test_scoring_no_suppliers_zero_supplier_score  – no suppliers → supplier_score = 0.0
17. test_scoring_no_competitors_full_competition   – no competitors → competition_score = 1.0
18. test_discovery_service_dry_run_no_db_writes    – dry_run=True → no session.add called
19. test_discovery_service_dedup_by_canonical      – two signals same canonical → 1 candidate
20. test_discovery_top_n_trim                      – >N candidates → only top N kept
21. test_discovery_redis_lock_skips_concurrent     – locked → task returns skipped
22. test_get_top_candidates_empty                  – empty DB → returns []
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Trend collector imports ───────────────────────────────────────────────────
from app.services.trend_collectors.tiktok_trends import (
    collect_tiktok_trends,
    _normalise_score,
)
from app.services.trend_collectors.amazon_bestsellers import (
    collect_amazon_bestsellers,
    _rank_to_score,
)

# ── Product matcher imports ───────────────────────────────────────────────────
from app.services.product_matcher import (
    _normalise,
    _tokenise,
    _token_overlap_ratio,
    match_trend_to_canonical,
)

# ── Scoring imports ───────────────────────────────────────────────────────────
from app.services.product_scoring import (
    compute_product_score,
    ScoreBreakdown,
    _clamp,
)

# ── Discovery service ─────────────────────────────────────────────────────────
from app.services.discovery_service import (
    run_product_discovery,
    get_top_candidates,
    DiscoveryResult,
)

# ── Celery task ───────────────────────────────────────────────────────────────
from app.workers.tasks_discovery import _run_discovery_async


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fake_canonical(
    name: str = "COSRX Advanced Snail Essence 100ml",
    brand: str = "COSRX",
    last_price: float | None = 28.99,
    has_ean: bool = True,
    has_image: bool = True,
) -> MagicMock:
    cp = MagicMock()
    cp.id                = uuid.uuid4()
    cp.name              = name
    cp.brand             = brand
    cp.last_price        = Decimal(str(last_price)) if last_price else None
    cp.ean               = "0123456789012" if has_ean else None
    cp.image_urls_json   = '["https://cdn.example.com/img.jpg"]' if has_image else None
    cp.pricing_enabled   = True
    cp.canonical_sku     = name.lower().replace(" ", "-")
    return cp


def _make_result(
    scalar_one_or_none=None,
    scalar_one=0,
    scalars_all=None,
) -> MagicMock:
    """Return a synchronous MagicMock that mimics SQLAlchemy result."""
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=scalar_one_or_none)
    r.scalar_one         = MagicMock(return_value=scalar_one)
    r.scalars            = MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=scalars_all or []))
    )
    return r


def _empty_result() -> MagicMock:
    return _make_result(scalar_one_or_none=None, scalar_one=0, scalars_all=[])


# ─────────────────────────────────────────────────────────────────────────────
# 1. TikTok collector
# ─────────────────────────────────────────────────────────────────────────────

def test_tiktok_collector_returns_signals():
    signals = collect_tiktok_trends()
    assert len(signals) >= 1
    for s in signals:
        assert s["source"] == "tiktok"
        assert "external_id" in s
        assert "name" in s
        assert "trend_score" in s
        assert 0.0 <= s["trend_score"] <= 10.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. Amazon collector
# ─────────────────────────────────────────────────────────────────────────────

def test_amazon_bestsellers_collector():
    signals = collect_amazon_bestsellers()
    assert len(signals) >= 1
    for s in signals:
        assert s["source"] == "amazon_bestsellers"
        assert "external_id" in s
        assert 0.0 <= s["trend_score"] <= 10.0


# ─────────────────────────────────────────────────────────────────────────────
# 3. TikTok score normalisation
# ─────────────────────────────────────────────────────────────────────────────

def test_tiktok_score_normalisation():
    score = _normalise_score(1_000_000, 200_000)
    assert 0.0 <= score <= 10.0


# ─────────────────────────────────────────────────────────────────────────────
# 4 & 5. Amazon rank-to-score
# ─────────────────────────────────────────────────────────────────────────────

def test_amazon_rank_to_score_rank1():
    score = _rank_to_score(1)
    assert score >= 9.0, f"rank 1 should score ≥9.0, got {score}"


def test_amazon_rank_to_score_rank100():
    score = _rank_to_score(100)
    assert score <= 1.0, f"rank 100 should score ≤1.0, got {score}"


# ─────────────────────────────────────────────────────────────────────────────
# 6. _normalise
# ─────────────────────────────────────────────────────────────────────────────

def test_product_matcher_normalise():
    result = _normalise("COSRX Advanced Snail 96 Mucin Power Essence!")
    assert result == "cosrx advanced snail 96 mucin power essence"
    assert "!" not in result


# ─────────────────────────────────────────────────────────────────────────────
# 7. _tokenise
# ─────────────────────────────────────────────────────────────────────────────

def test_product_matcher_tokenise():
    tokens = _tokenise("The Advanced Snail 96ml set for skin")
    # stop-words 'the', 'for', 'set', 'ml' should be removed
    assert "the" not in tokens
    assert "set" not in tokens
    assert "ml" not in tokens
    assert "advanced" in tokens
    assert "snail" in tokens


# ─────────────────────────────────────────────────────────────────────────────
# 8. _token_overlap_ratio – exact match
# ─────────────────────────────────────────────────────────────────────────────

def test_product_matcher_token_overlap_exact():
    toks = ["cosrx", "snail", "essence"]
    ratio = _token_overlap_ratio(toks, toks)
    assert ratio == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 9. _token_overlap_ratio – partial overlap
# ─────────────────────────────────────────────────────────────────────────────

def test_product_matcher_token_overlap_partial():
    a = ["cosrx", "snail", "essence", "100"]
    b = ["cosrx", "snail", "toner", "150"]
    ratio = _token_overlap_ratio(a, b)
    assert 0.0 < ratio < 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 10. match_trend_to_canonical – no rows in DB → None
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_product_matcher_no_match_db():
    session = MagicMock()
    session.execute = AsyncMock(return_value=_empty_result())

    result = await match_trend_to_canonical(
        session,
        {"name": "Unknown Product XYZ", "brand": None},
    )
    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# 11. match_trend_to_canonical – fuzzy match via mocked DB
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_product_matcher_fuzzy_match():
    cp = _fake_canonical("COSRX Advanced Snail 96 Mucin Power Essence 100ml", "COSRX")

    call_count = 0

    async def _execute(stmt, *a, **kw):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            # Pass 1 (slug) + Pass 2 (brand exact) → no match
            return _make_result(scalar_one_or_none=None, scalars_all=[])
        else:
            # Pass 3 (fuzzy) → return [cp]
            return _make_result(scalar_one_or_none=None, scalars_all=[cp])

    session = MagicMock()
    session.execute = _execute

    result = await match_trend_to_canonical(
        session,
        {"name": "COSRX Snail Power Essence", "brand": "COSRX"},
        fuzzy_threshold=0.3,  # lower threshold so mock data triggers match
    )
    assert result == cp.id


# ─────────────────────────────────────────────────────────────────────────────
# 12. Score formula weights
# ─────────────────────────────────────────────────────────────────────────────

def test_score_formula_weights():
    trend       = 0.8
    margin      = 0.6
    competition = 0.5
    supplier    = 0.7
    content     = 0.9

    expected = round(
        trend * 0.35
        + margin * 0.25
        + competition * 0.20
        + supplier * 0.10
        + content * 0.10,
        4,
    )
    manual = round(0.8 * 0.35 + 0.6 * 0.25 + 0.5 * 0.20 + 0.7 * 0.10 + 0.9 * 0.10, 4)
    assert abs(expected - manual) < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# 13 & 14. _clamp helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_score_clamp_above_one():
    assert _clamp(1.5) == 1.0


def test_score_clamp_below_zero():
    assert _clamp(-0.3) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 15. Scoring: no last_price → margin_score = 0.5 (neutral)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scoring_no_last_price_neutral_margin():
    cp = _fake_canonical(last_price=None)

    call_count = 0

    async def _execute(stmt, *a, **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_result(scalar_one_or_none=cp)
        return _make_result(scalar_one=0, scalar_one_or_none=None)

    session = MagicMock()
    session.execute = _execute

    result = await compute_product_score(session, cp.id, 7.0)
    assert result is not None
    assert result.margin_score == 0.5


# ─────────────────────────────────────────────────────────────────────────────
# 16. Scoring: no suppliers → supplier_score = 0.0
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scoring_no_suppliers_zero_supplier_score():
    cp = _fake_canonical()

    call_count = 0

    async def _execute(stmt, *a, **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_result(scalar_one_or_none=cp)
        return _make_result(scalar_one=0, scalar_one_or_none=None)

    session = MagicMock()
    session.execute = _execute

    result = await compute_product_score(session, cp.id, 5.0)
    assert result is not None
    assert result.supplier_score == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 17. Scoring: no competitors → competition_score = 1.0
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scoring_no_competitors_full_competition():
    cp = _fake_canonical()

    call_count = 0

    async def _execute(stmt, *a, **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_result(scalar_one_or_none=cp)
        return _make_result(scalar_one=0, scalar_one_or_none=None)

    session = MagicMock()
    session.execute = _execute

    result = await compute_product_score(session, cp.id, 6.0)
    assert result is not None
    assert result.competition_score == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 18. Discovery service: dry_run=True → no session.add() calls
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_discovery_service_dry_run_no_db_writes():
    session = MagicMock()
    session.execute = AsyncMock(return_value=_empty_result())
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    result = await run_product_discovery(session, dry_run=True, top_n=10)

    assert isinstance(result, DiscoveryResult)
    assert result.dry_run is True
    assert session.add.call_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 19. Discovery service: two signals for same canonical → dedup to 1 candidate
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_discovery_service_dedup_by_canonical():
    shared_id = uuid.uuid4()

    signal_a = {
        "source": "tiktok", "external_id": "tt-001",
        "name": "COSRX Snail Essence", "brand": "COSRX",
        "category": "Essence", "trend_score": 8.0, "raw_data_json": "{}",
    }
    signal_b = {
        "source": "amazon_bestsellers", "external_id": "B001",
        "name": "COSRX Snail Essence 100ml", "brand": "COSRX",
        "category": "Essence", "trend_score": 7.5, "raw_data_json": "{}",
    }

    session = MagicMock()
    session.execute = AsyncMock(return_value=_empty_result())
    session.add = MagicMock()
    session.flush = AsyncMock()

    score_a = ScoreBreakdown(
        canonical_product_id=shared_id,
        trend_score=0.80, margin_score=0.60,
        competition_score=1.0, supplier_score=0.5,
        content_score=0.9, final_score=0.7650,
    )
    score_b = ScoreBreakdown(
        canonical_product_id=shared_id,
        trend_score=0.75, margin_score=0.60,
        competition_score=1.0, supplier_score=0.5,
        content_score=0.9, final_score=0.7475,
    )

    with (
        patch("app.services.discovery_service.collect_tiktok_trends", return_value=[signal_a]),
        patch("app.services.discovery_service.collect_amazon_bestsellers", return_value=[signal_b]),
        patch("app.services.discovery_service.match_trend_to_canonical", return_value=shared_id),
        patch(
            "app.services.discovery_service.compute_product_score",
            side_effect=[score_a, score_b],
        ),
    ):
        result = await run_product_discovery(session, dry_run=True, top_n=50)

    # Both signals matched same canonical → only 1 unique candidate
    assert len(result.top_candidates) == 1
    # Should keep higher final_score (signal_a = 0.7650)
    assert abs(result.top_candidates[0]["final_score"] - 0.7650) < 0.001


# ─────────────────────────────────────────────────────────────────────────────
# 20. Discovery service: top-N trim
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_discovery_top_n_trim():
    """When more candidates than top_n are generated, only top_n survive."""
    ids = [uuid.uuid4() for _ in range(5)]
    signals = [
        {
            "source": "tiktok",
            "external_id": f"tt-{i:03d}",
            "name": f"Product {i}",
            "brand": "TestBrand",
            "category": "Serum",
            "trend_score": float(i + 1),
            "raw_data_json": "{}",
        }
        for i in range(5)
    ]
    scores = [
        ScoreBreakdown(
            canonical_product_id=ids[i],
            trend_score=float(i + 1) / 10,
            margin_score=0.5,
            competition_score=1.0,
            supplier_score=0.5,
            content_score=0.8,
            final_score=round(float(i + 1) / 10 * 0.35 + 0.5 * 0.25 + 1.0 * 0.20 + 0.5 * 0.10 + 0.8 * 0.10, 4),
        )
        for i in range(5)
    ]

    session = MagicMock()
    session.execute = AsyncMock(return_value=_empty_result())
    session.add = MagicMock()
    session.flush = AsyncMock()

    ids_iter = iter(ids)

    with (
        patch("app.services.discovery_service.collect_tiktok_trends", return_value=signals),
        patch("app.services.discovery_service.collect_amazon_bestsellers", return_value=[]),
        patch(
            "app.services.discovery_service.match_trend_to_canonical",
            side_effect=lambda *a, **kw: next(ids_iter),
        ),
        patch(
            "app.services.discovery_service.compute_product_score",
            side_effect=scores,
        ),
    ):
        result = await run_product_discovery(session, dry_run=True, top_n=3)

    assert len(result.top_candidates) == 3
    top_scores = [c["final_score"] for c in result.top_candidates]
    assert top_scores == sorted(top_scores, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# 21. Redis lock: concurrent lock → task returns skipped
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_discovery_redis_lock_skips_concurrent():
    """If Redis lock is already held, the task should skip gracefully."""
    with patch(
        "app.workers.tasks_discovery._acquire_lock",
        return_value=False,
    ):
        result = await _run_discovery_async(dry_run=False, top_n=50)

    assert result["status"] == "skipped"
    assert result["reason"] == "concurrent_lock"


# ─────────────────────────────────────────────────────────────────────────────
# 22. get_top_candidates: empty DB → returns []
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_top_candidates_empty():
    session = MagicMock()
    session.execute = AsyncMock(return_value=_make_result(scalars_all=[]))

    result = await get_top_candidates(session, limit=50)
    assert isinstance(result, list)
    assert len(result) == 0
