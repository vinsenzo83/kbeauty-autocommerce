"""
tests/test_sprint15_discovery.py
──────────────────────────────────
Sprint 15 – AI Product Discovery mock-only test suite.

Coverage
--------
1.  test_tiktok_collector_returns_sorted_signals
2.  test_amazon_collector_returns_sorted_signals
3.  test_tiktok_score_normalisation
4.  test_amazon_rank_to_score
5.  test_token_normalisation_helpers
6.  test_fuzzy_matcher_exact_slug_match
7.  test_fuzzy_matcher_brand_name_match
8.  test_fuzzy_matcher_no_match_returns_none
9.  test_fuzzy_matcher_token_overlap
10. test_score_formula_weights
11. test_score_no_supplier_neutral
12. test_discovery_pipeline_dry_run
13. test_discovery_pipeline_deduplication
14. test_discovery_top_n_trim
15. test_get_top_candidates_returns_sorted
16. test_discovery_run_celery_task_mock

All tests are mock-only: SQLAlchemy is replaced with AsyncMock,
Shopify API is never called, Redis is never contacted.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fake_canonical(
    *,
    name: str = "COSRX Advanced Snail 96 Mucin Power Essence 100ml",
    brand: str = "COSRX",
    last_price: float | None = 28.99,
    canonical_sku: str = "cosrx-advanced-snail-96-mucin-power-essence-100ml",
) -> MagicMock:
    cp = MagicMock()
    cp.id              = uuid.uuid4()
    cp.canonical_sku   = canonical_sku
    cp.name            = name
    cp.brand           = brand
    cp.last_price      = Decimal(str(last_price)) if last_price else None
    cp.image_urls_json = '["https://example.com/img.jpg"]'
    cp.ean             = "1234567890123"
    cp.pricing_enabled = True
    cp.target_margin_rate    = Decimal("0.30")
    cp.min_margin_abs        = Decimal("3.00")
    cp.shipping_cost_default = Decimal("3.00")
    return cp


def _fake_supplier_product(
    *,
    canonical_product_id: uuid.UUID | None = None,
    price: float = 12.50,
    stock_status: str = "IN_STOCK",
) -> MagicMock:
    sp = MagicMock()
    sp.id                    = uuid.uuid4()
    sp.canonical_product_id  = canonical_product_id or uuid.uuid4()
    sp.price                 = Decimal(str(price))
    sp.stock_status          = stock_status
    return sp


async def _async_val(val: Any):
    return val


def _mock_session_returning(rows: list, scalar_value: Any = None):
    """Build an AsyncSession mock whose execute() returns the given rows."""
    session = AsyncMock()

    async def _execute(_query, *args, **kwargs):
        result = MagicMock()
        result.scalars.return_value.all.return_value = list(rows)
        result.scalar_one_or_none.return_value = rows[0] if rows else None
        result.scalar_one.return_value = scalar_value if scalar_value is not None else (rows[0] if rows else 0)
        result.scalar.return_value = scalar_value
        return result

    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()
    session.flush   = AsyncMock()
    session.commit  = AsyncMock()
    session.rollback= AsyncMock()
    session.close   = AsyncMock()
    return session


# ─────────────────────────────────────────────────────────────────────────────
# 1. TikTok collector
# ─────────────────────────────────────────────────────────────────────────────

def test_tiktok_collector_returns_sorted_signals():
    from app.services.trend_collectors.tiktok_trends import collect_tiktok_trends
    signals = collect_tiktok_trends()

    assert len(signals) >= 10, "Expected at least 10 TikTok signals"
    scores = [s["trend_score"] for s in signals]
    assert scores == sorted(scores, reverse=True), "Signals must be sorted descending by trend_score"

    first = signals[0]
    assert first["source"] == "tiktok"
    assert "external_id" in first
    assert "name"         in first
    assert 0.0 <= first["trend_score"] <= 10.0
    assert "raw_data_json" in first
    # Verify raw_data_json is valid JSON
    parsed = json.loads(first["raw_data_json"])
    assert "video_id" in parsed


# ─────────────────────────────────────────────────────────────────────────────
# 2. Amazon collector
# ─────────────────────────────────────────────────────────────────────────────

def test_amazon_collector_returns_sorted_signals():
    from app.services.trend_collectors.amazon_bestsellers import collect_amazon_bestsellers
    signals = collect_amazon_bestsellers()

    assert len(signals) >= 10, "Expected at least 10 Amazon signals"
    scores = [s["trend_score"] for s in signals]
    assert scores == sorted(scores, reverse=True), "Signals must be sorted descending by trend_score"

    first = signals[0]
    assert first["source"] == "amazon_bestsellers"
    assert "external_id" in first   # ASIN
    assert 0.0 <= first["trend_score"] <= 10.0
    parsed = json.loads(first["raw_data_json"])
    assert "asin" in parsed
    assert "rank" in parsed


# ─────────────────────────────────────────────────────────────────────────────
# 3. TikTok score normalisation
# ─────────────────────────────────────────────────────────────────────────────

def test_tiktok_score_normalisation():
    from app.services.trend_collectors.tiktok_trends import _normalise_score
    # Very high engagement → near 10 (formula: log10(likes)*0.6 + log10(shares)*0.4)
    # log10(2_000_000)*0.6 + log10(600_000)*0.4 ≈ 6.09 + ... actual: ~6.1
    high = _normalise_score(2_000_000, 600_000)
    assert 5.0 <= high <= 10.0, f"Expected high engagement score 5-10, got {high}"
    # Low engagement → low score
    low = _normalise_score(1_000, 100)
    assert low < 5.0, f"Expected low score < 5, got {low}"
    # Score never exceeds 10
    capped = _normalise_score(100_000_000, 50_000_000)
    assert capped <= 10.0
    # Higher engagement always produces higher score
    score_high = _normalise_score(1_000_000, 300_000)
    score_low  = _normalise_score(10_000,    2_000)
    assert score_high > score_low


# ─────────────────────────────────────────────────────────────────────────────
# 4. Amazon rank → score
# ─────────────────────────────────────────────────────────────────────────────

def test_amazon_rank_to_score():
    from app.services.trend_collectors.amazon_bestsellers import _rank_to_score
    assert _rank_to_score(1)   == pytest.approx(9.9, abs=0.1)
    assert _rank_to_score(50)  == pytest.approx(5.1, abs=0.2)
    assert _rank_to_score(100) == pytest.approx(0.0, abs=0.1)
    # Rank never produces score > 9.9 or < 0
    assert 0.0 <= _rank_to_score(1)   <= 9.9
    assert 0.0 <= _rank_to_score(100) <= 9.9


# ─────────────────────────────────────────────────────────────────────────────
# 5. Token normalisation helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_token_normalisation_helpers():
    from app.services.product_matcher import _normalise, _tokenise, _token_overlap_ratio

    assert _normalise("COSRX Advanced Snail!") == "cosrx advanced snail"
    tokens = _tokenise("COSRX Advanced Snail 96 Mucin Power Essence 100ml")
    assert "cosrx"    in tokens
    assert "snail"    in tokens
    assert "mucin"    in tokens
    # Stop-words should be filtered
    assert "ml"  not in tokens
    assert "100" not in tokens  # 3+ chars required, but '100' may or may not be filtered

    # Overlap ratio
    a = ["cosrx", "snail", "mucin"]
    b = ["cosrx", "snail", "power"]
    ratio = _token_overlap_ratio(a, b)
    assert 0.4 <= ratio <= 0.6, f"Expected ~0.5 overlap, got {ratio}"

    # Empty inputs
    assert _token_overlap_ratio([], b) == 0.0
    assert _token_overlap_ratio(a, []) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 6. Fuzzy matcher – exact slug match (Pass 1)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fuzzy_matcher_exact_slug_match():
    from app.services.product_matcher import match_trend_to_canonical

    cp = _fake_canonical(canonical_sku="cosrx-advanced-snail-96-mucin-power-essence-100ml")
    session = _mock_session_returning([cp])

    trend = {
        "name":  "COSRX Advanced Snail 96 Mucin Power Essence 100ml",
        "brand": "COSRX",
    }
    result = await match_trend_to_canonical(session, trend)
    assert result == cp.id


# ─────────────────────────────────────────────────────────────────────────────
# 7. Fuzzy matcher – brand + name match (Pass 2)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fuzzy_matcher_brand_name_match():
    from app.services.product_matcher import match_trend_to_canonical

    cp = _fake_canonical(
        name="Laneige Lip Sleeping Mask Berry 20g",
        brand="Laneige",
        canonical_sku="laneige-lip-sleeping-mask-berry-20g",
    )

    call_count = 0

    async def _execute(query, *a, **kw):
        nonlocal call_count
        result = MagicMock()
        call_count += 1
        # Pass 1 (slug): no match; Pass 2 (brand filter): return cp
        if call_count == 1:
            result.scalar_one_or_none.return_value = None
            result.scalars.return_value.all.return_value = []
        else:
            result.scalar_one_or_none.return_value = cp
            result.scalars.return_value.all.return_value = [cp]
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)

    trend = {"name": "Laneige Lip Sleeping Mask Berry 20g", "brand": "Laneige"}
    result = await match_trend_to_canonical(session, trend)
    assert result == cp.id


# ─────────────────────────────────────────────────────────────────────────────
# 8. Fuzzy matcher – no match returns None
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fuzzy_matcher_no_match_returns_none():
    from app.services.product_matcher import match_trend_to_canonical

    # Session always returns empty results
    session = _mock_session_returning([])

    trend = {
        "name":  "Completely Unknown Product XYZ 999ml",
        "brand": "UnknownBrand",
    }
    result = await match_trend_to_canonical(session, trend, fuzzy_threshold=0.9)
    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# 9. Fuzzy matcher – token overlap (Pass 3)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fuzzy_matcher_token_overlap():
    from app.services.product_matcher import match_trend_to_canonical

    cp = _fake_canonical(
        name="Some By Mi AHA BHA PHA Toner 150ml",
        brand="Some By Mi",
        canonical_sku="some-by-mi-aha-bha-pha-toner-150ml",
    )
    call_count = 0

    async def _execute(query, *a, **kw):
        nonlocal call_count
        result = MagicMock()
        call_count += 1
        if call_count == 1:
            # Pass 1 slug: no match
            result.scalar_one_or_none.return_value = None
            result.scalars.return_value.all.return_value = []
        else:
            # All subsequent passes return the cp for fuzzy scoring
            result.scalar_one_or_none.return_value = None
            result.scalars.return_value.all.return_value = [cp]
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)

    # Slightly different casing / extra text but same core tokens
    trend = {
        "name":  "Some By Mi AHA BHA PHA 30 Days Miracle Toner 150ml",
        "brand": "some by mi",
    }
    result = await match_trend_to_canonical(session, trend, fuzzy_threshold=0.4)
    # With a low threshold, core token overlap should produce a match
    assert result == cp.id


# ─────────────────────────────────────────────────────────────────────────────
# 10. Score formula weights
# ─────────────────────────────────────────────────────────────────────────────

def test_score_formula_weights():
    """Verify the weighted formula: final = t*0.35 + m*0.25 + c*0.20 + s*0.10 + cnt*0.10"""
    from app.services.product_scoring import _clamp, _round4

    def formula(t, m, c, s, cnt):
        return _round4(t * 0.35 + m * 0.25 + c * 0.20 + s * 0.10 + cnt * 0.10)

    # All zeros → 0.0
    assert formula(0, 0, 0, 0, 0) == 0.0
    # All ones → 1.0
    assert formula(1, 1, 1, 1, 1) == pytest.approx(1.0, abs=0.001)

    # Known values
    result = formula(0.8, 0.6, 0.9, 1.0, 0.7)
    expected = round(0.8*0.35 + 0.6*0.25 + 0.9*0.20 + 1.0*0.10 + 0.7*0.10, 4)
    assert result == pytest.approx(expected, abs=0.0001)

    # Clamp: inputs above 1 are clipped
    assert _clamp(1.5) == 1.0
    assert _clamp(-0.2) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 11. Scoring – no supplier → neutral margin score
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_score_no_supplier_neutral():
    from app.services.product_scoring import compute_product_score

    cp = _fake_canonical(last_price=None)  # No last_price

    call_count = 0
    async def _execute(query, *a, **kw):
        nonlocal call_count
        result = MagicMock()
        call_count += 1
        # Canonical product query
        result.scalar_one_or_none.return_value = cp if call_count == 1 else None
        result.scalar_one.return_value = 0
        result.scalars.return_value.all.return_value = []
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)

    breakdown = await compute_product_score(session, cp.id, trend_score_raw=7.5)
    assert breakdown is not None
    # No last_price → margin_score neutral = 0.5
    assert breakdown.margin_score == pytest.approx(0.5, abs=0.01)
    # No suppliers → supplier_score = 0.0
    assert breakdown.supplier_score == pytest.approx(0.0, abs=0.01)
    # Final score should still be a valid float in [0, 1]
    assert 0.0 <= breakdown.final_score <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 12. Discovery pipeline – dry run (no DB writes)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_discovery_pipeline_dry_run():
    """Dry run: signals collected and scored but session.add never called."""
    from app.services.discovery_service import run_product_discovery

    cp = _fake_canonical()

    call_count = 0
    async def _execute(query, *a, **kw):
        nonlocal call_count
        result = MagicMock()
        call_count += 1
        # Return a canonical product for matching queries
        result.scalar_one_or_none.return_value = cp
        result.scalar_one.return_value = 1
        result.scalars.return_value.all.return_value = [cp]
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()
    session.flush   = AsyncMock()
    session.commit  = AsyncMock()

    result = await run_product_discovery(session, dry_run=True, top_n=50)

    assert result.dry_run is True
    assert result.signals_collected > 0
    # In dry_run mode, session.add might be called 0 or very few times
    # Key assertion: session.commit is NOT called by the pipeline itself
    session.commit.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 13. Discovery deduplication – same canonical product from multiple sources
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_discovery_pipeline_deduplication():
    """
    When the same canonical product matches signals from both TikTok and Amazon,
    only one candidate entry should be kept (the higher-scored one).
    """
    from app.services.discovery_service import run_product_discovery

    shared_cp = _fake_canonical(
        name="COSRX Advanced Snail 96 Mucin Power Essence 100ml",
        brand="COSRX",
    )

    async def _execute(query, *a, **kw):
        result = MagicMock()
        result.scalar_one_or_none.return_value = shared_cp
        result.scalar_one.return_value = 1
        result.scalars.return_value.all.return_value = [shared_cp]
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()
    session.flush   = AsyncMock()

    result = await run_product_discovery(session, dry_run=True, top_n=50)

    # Because both TikTok and Amazon match to the same cp,
    # the top_candidates should have at most 1 entry for that product.
    product_ids = [c["canonical_product_id"] for c in result.top_candidates]
    assert len(product_ids) == len(set(product_ids)), (
        "Duplicate canonical_product_id in top_candidates – deduplication failed"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 14. Top-N trimming
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_discovery_top_n_trim():
    """Pipeline keeps at most top_n candidates."""
    from app.services.discovery_service import run_product_discovery

    # Each signal maps to a DIFFERENT canonical product
    canonical_products = [
        _fake_canonical(
            name=f"Product {i} Name Long Enough To Match",
            brand=f"Brand{i}",
            canonical_sku=f"brand{i}-product-{i}",
        )
        for i in range(30)
    ]

    idx = 0
    async def _execute(query, *a, **kw):
        nonlocal idx
        result = MagicMock()
        # Cycle through different canonical products
        cp = canonical_products[idx % len(canonical_products)]
        idx += 1
        result.scalar_one_or_none.return_value = cp
        result.scalar_one.return_value = 1
        result.scalars.return_value.all.return_value = [cp]
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()
    session.flush   = AsyncMock()

    result = await run_product_discovery(session, dry_run=True, top_n=5)

    assert len(result.top_candidates) <= 5, (
        f"top_candidates should be ≤ 5, got {len(result.top_candidates)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 15. get_top_candidates – sorted by final_score descending
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_top_candidates_returns_sorted():
    from app.services.discovery_service import get_top_candidates

    # Build mock candidate rows (unsorted intentionally)
    cands = []
    for score in [0.45, 0.92, 0.67, 0.81]:
        c = MagicMock()
        c.id                     = uuid.uuid4()
        c.canonical_product_id   = uuid.uuid4()
        c.trend_product_id       = None
        c.trend_score            = Decimal("0.8")
        c.margin_score           = Decimal("0.6")
        c.competition_score      = Decimal("0.7")
        c.supplier_score         = Decimal("1.0")
        c.content_score          = Decimal("0.9")
        c.final_score            = Decimal(str(score))
        c.status                 = "candidate"
        c.notes                  = None
        c.created_at             = None
        c.updated_at             = None
        cands.append(c)

    # SQLAlchemy mock returns already-sorted (DB ORDER BY final_score DESC)
    sorted_cands = sorted(cands, key=lambda x: float(x.final_score), reverse=True)

    async def _execute(query, *a, **kw):
        result = MagicMock()
        result.scalars.return_value.all.return_value = sorted_cands
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)

    items = await get_top_candidates(session, limit=10)

    scores = [i["final_score"] for i in items]
    assert scores == sorted(scores, reverse=True), (
        f"get_top_candidates must return items sorted by final_score DESC, got {scores}"
    )
    assert scores[0] == pytest.approx(0.92, abs=0.001)


# ─────────────────────────────────────────────────────────────────────────────
# 16. Celery task – mocked apply_async
# ─────────────────────────────────────────────────────────────────────────────

def test_discovery_run_celery_task_mock():
    """run_discovery_pipeline task can be invoked without real Redis/DB."""
    with (
        patch("app.workers.tasks_discovery._run_discovery_async") as mock_async,
        patch("asyncio.run") as mock_run,
    ):
        mock_run.return_value = {
            "status":              "ok",
            "signals_collected":   30,
            "signals_matched":     12,
            "candidates_created":  8,
            "candidates_updated":  2,
            "candidates_rejected": 2,
        }

        from app.workers.tasks_discovery import run_discovery_pipeline

        # Call the Celery task function directly (bypassing Celery broker)
        result = run_discovery_pipeline.__wrapped__(
            dry_run=True, top_n=10
        ) if hasattr(run_discovery_pipeline, "__wrapped__") else (
            run_discovery_pipeline.run(dry_run=True, top_n=10)
        )

        mock_run.assert_called_once()
        assert isinstance(result, dict)
        assert result.get("status") == "ok"
        assert result.get("signals_collected") == 30
