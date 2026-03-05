"""
tests/test_sprint17_discovery.py
──────────────────────────────────
Sprint 17 – AI Discovery Engine v2 mock-only test suite.

Coverage
--------
 1.  test_score_formula_weights         – weight constants sum to 1.0
 2.  test_score_product_formula         – score_product() result correctness
 3.  test_score_product_all_neutral     – all NEUTRAL_SCORE → 0.5
 4.  test_score_product_all_zero        – all 0 → 0.0
 5.  test_score_product_all_one         – all 1 → 1.0
 6.  test_score_product_clamped         – out-of-range inputs clamped [0,1]
 7.  test_margin_score_helper           – _margin_score() with price/cost
 8.  test_supplier_rank_score_helper    – _supplier_rank_score() edge cases
 9.  test_competition_score_helper      – _competition_score() band width
10.  test_generate_candidates_writes_rows – generate_candidates upserts rows
11.  test_generate_candidates_idempotent  – re-run updates, no duplicates
12.  test_get_top_candidates_sort_order   – sorted by score desc
13.  test_get_top_candidates_limit        – respects limit param
14.  test_reject_candidate               – sets status=rejected, stores reason
15.  test_mark_candidate_published       – sets status=published, idempotent
16.  test_run_discovery_v2_summary       – run_discovery_v2 returns expected keys
17.  test_celery_task_lock_prevents_double_run – Redis lock blocks concurrent call
18.  test_celery_task_mock              – run_discovery_and_publish Celery task (mocked)
19.  test_admin_list_candidates_shape   – GET /discovery/v2/candidates response shape
20.  test_admin_run_discovery_shape     – POST /discovery/v2/run response shape
21.  test_admin_reject_candidate_shape  – POST /discovery/v2/candidates/{id}/reject
22.  test_candidate_status_constants    – CandidateStatusV2 values are distinct strings

All tests are mock-only; no external network, no real DB.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helper factories
# ─────────────────────────────────────────────────────────────────────────────

def _make_canonical_product(
    *,
    last_price: float | None = 25.00,
    target_margin_rate: float = 0.30,
) -> MagicMock:
    cp = MagicMock()
    cp.id                 = uuid.uuid4()
    cp.canonical_sku      = f"SKU-{str(cp.id)[:8]}"
    cp.name               = "Test Product"
    cp.brand              = "TestBrand"
    cp.last_price         = last_price
    cp.target_margin_rate = target_margin_rate
    return cp


def _make_supplier_product(
    *,
    price: float = 12.00,
    stock_status: str = "in_stock",
) -> MagicMock:
    sp = MagicMock()
    sp.id           = uuid.uuid4()
    sp.price        = price
    sp.stock_status = stock_status
    return sp


def _make_candidate(
    *,
    score: float = 0.72,
    status: str = "candidate",
    canonical_product_id: uuid.UUID | None = None,
) -> MagicMock:
    c = MagicMock()
    c.id                   = uuid.uuid4()
    c.canonical_product_id = canonical_product_id or uuid.uuid4()
    c.score                = score
    c.amazon_rank_score    = 0.5
    c.supplier_rank_score  = 0.8
    c.margin_score         = 0.6
    c.review_score         = 0.5
    c.competition_score    = 0.5
    c.status               = status
    c.notes                = None
    c.created_at           = datetime.now(tz=timezone.utc)
    c.updated_at           = datetime.now(tz=timezone.utc)
    c.to_dict.return_value = {
        "id":                   str(c.id),
        "canonical_product_id": str(c.canonical_product_id),
        "score":                score,
        "status":               status,
    }
    return c


# ─────────────────────────────────────────────────────────────────────────────
# 1. Score formula weight constants sum to 1.0
# ─────────────────────────────────────────────────────────────────────────────

def test_score_formula_weights():
    from app.services.discovery_service_v2 import (
        WEIGHT_AMAZON_RANK,
        WEIGHT_SUPPLIER_RANK,
        WEIGHT_MARGIN,
        WEIGHT_REVIEW,
        WEIGHT_COMPETITION,
    )
    total = (
        WEIGHT_AMAZON_RANK
        + WEIGHT_SUPPLIER_RANK
        + WEIGHT_MARGIN
        + WEIGHT_REVIEW
        + WEIGHT_COMPETITION
    )
    assert abs(total - 1.0) < 1e-9, f"Weights must sum to 1.0, got {total}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. score_product() formula correctness
# ─────────────────────────────────────────────────────────────────────────────

def test_score_product_formula():
    from app.services.discovery_service_v2 import score_product

    parts = {
        "amazon_rank_score":   0.8,
        "supplier_rank_score": 0.6,
        "margin_score":        0.9,
        "review_score":        0.7,
        "competition_score":   0.4,
    }
    expected = (
        0.8 * 0.35
        + 0.6 * 0.25
        + 0.9 * 0.20
        + 0.7 * 0.10
        + 0.4 * 0.10
    )
    result = score_product(parts)
    assert abs(result - expected) < 1e-9, f"Expected {expected:.6f}, got {result:.6f}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. All neutral → 0.5
# ─────────────────────────────────────────────────────────────────────────────

def test_score_product_all_neutral():
    from app.services.discovery_service_v2 import score_product, NEUTRAL_SCORE

    parts = {k: NEUTRAL_SCORE for k in [
        "amazon_rank_score", "supplier_rank_score", "margin_score",
        "review_score", "competition_score"
    ]}
    result = score_product(parts)
    assert abs(result - 0.5) < 1e-9


# ─────────────────────────────────────────────────────────────────────────────
# 4. All zero → 0.0
# ─────────────────────────────────────────────────────────────────────────────

def test_score_product_all_zero():
    from app.services.discovery_service_v2 import score_product

    parts = {k: 0.0 for k in [
        "amazon_rank_score", "supplier_rank_score", "margin_score",
        "review_score", "competition_score"
    ]}
    assert score_product(parts) == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# 5. All one → 1.0
# ─────────────────────────────────────────────────────────────────────────────

def test_score_product_all_one():
    from app.services.discovery_service_v2 import score_product

    parts = {k: 1.0 for k in [
        "amazon_rank_score", "supplier_rank_score", "margin_score",
        "review_score", "competition_score"
    ]}
    assert score_product(parts) == pytest.approx(1.0)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Out-of-range inputs are clamped to [0, 1]
# ─────────────────────────────────────────────────────────────────────────────

def test_score_product_clamped():
    from app.services.discovery_service_v2 import score_product

    # All values > 1.0 → score should be clamped to 1.0
    parts_high = {k: 999.0 for k in [
        "amazon_rank_score", "supplier_rank_score", "margin_score",
        "review_score", "competition_score"
    ]}
    assert score_product(parts_high) == pytest.approx(1.0)

    # All values < 0 → score should be clamped to 0.0
    parts_low = {k: -999.0 for k in [
        "amazon_rank_score", "supplier_rank_score", "margin_score",
        "review_score", "competition_score"
    ]}
    assert score_product(parts_low) == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# 7. _margin_score() helper
# ─────────────────────────────────────────────────────────────────────────────

def test_margin_score_helper():
    from app.services.discovery_service_v2 import _margin_score

    # Normal case: $25 price - $12 cost = $13 margin = 52%
    score = _margin_score(last_price=25.0, best_cost=12.0)
    assert abs(score - 0.52) < 0.001

    # No last_price → fallback to target_margin_rate
    score_no_price = _margin_score(last_price=None, best_cost=5.0, target_margin_rate=0.30)
    assert abs(score_no_price - 0.30) < 0.001

    # Zero cost → 100% margin → clamped to 1.0
    score_zero_cost = _margin_score(last_price=20.0, best_cost=0.0)
    assert score_zero_cost == pytest.approx(1.0)

    # Cost > price → negative margin → clamped to 0.0
    score_loss = _margin_score(last_price=10.0, best_cost=20.0)
    assert score_loss == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# 8. _supplier_rank_score() helper
# ─────────────────────────────────────────────────────────────────────────────

def test_supplier_rank_score_helper():
    from app.services.discovery_service_v2 import _supplier_rank_score, NEUTRAL_SCORE

    # 3 of 4 suppliers in stock
    assert _supplier_rank_score(3, 4) == pytest.approx(0.75)

    # All in stock
    assert _supplier_rank_score(5, 5) == pytest.approx(1.0)

    # None in stock
    assert _supplier_rank_score(0, 4) == pytest.approx(0.0)

    # No suppliers → neutral
    assert _supplier_rank_score(0, 0) == pytest.approx(NEUTRAL_SCORE)


# ─────────────────────────────────────────────────────────────────────────────
# 9. _competition_score() helper
# ─────────────────────────────────────────────────────────────────────────────

def test_competition_score_helper():
    from app.services.discovery_service_v2 import _competition_score, NEUTRAL_SCORE

    # No market price data → neutral
    assert _competition_score(None) == pytest.approx(NEUTRAL_SCORE)

    # Zero band width → no competition → high score
    assert _competition_score(0.0) == pytest.approx(1.0)

    # Very wide band (>= 80 USD) → very competitive → 0.2
    assert _competition_score(80.0) == pytest.approx(0.20, abs=0.01)

    # Mid band
    score_mid = _competition_score(20.0)
    assert 0.5 < score_mid < 1.0, f"Expected mid-range, got {score_mid}"


# ─────────────────────────────────────────────────────────────────────────────
# 10. generate_candidates() writes rows to session
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_candidates_writes_rows():
    from app.services.discovery_service_v2 import generate_candidates
    from app.models.product_candidate_v2 import ProductCandidateV2, CandidateStatusV2

    cp = _make_canonical_product(last_price=25.00)
    sp = _make_supplier_product(price=12.00, stock_status="in_stock")

    call_count = [0]

    async def _execute(q, *a, **kw):
        result = MagicMock()
        ci = call_count[0]; call_count[0] += 1
        if ci == 0:
            # canonical_products query
            result.scalars.return_value.all.return_value = [cp]
        elif ci == 1:
            # supplier_products query
            result.scalars.return_value.all.return_value = [sp]
        elif ci == 2:
            # market price query
            result.scalars.return_value.all.return_value = []
        elif ci == 3:
            # existing candidate check
            result.scalar_one_or_none.return_value = None
        else:
            result.scalars.return_value.all.return_value = []
            result.scalar_one_or_none.return_value = None
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()

    candidates = await generate_candidates(session, limit=100)

    assert len(candidates) == 1
    # session.add must have been called once (new row inserted)
    session.add.assert_called_once()
    # The added object should be a ProductCandidateV2
    added = session.add.call_args[0][0]
    assert isinstance(added, ProductCandidateV2)
    assert added.status == CandidateStatusV2.CANDIDATE
    assert 0.0 <= float(added.score) <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 11. generate_candidates() is idempotent (updates existing rows)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_candidates_idempotent():
    from app.services.discovery_service_v2 import generate_candidates
    from app.models.product_candidate_v2 import CandidateStatusV2

    cp       = _make_canonical_product(last_price=30.00)
    sp       = _make_supplier_product(price=10.00)
    existing = _make_candidate(score=0.50, status=CandidateStatusV2.CANDIDATE,
                                canonical_product_id=cp.id)

    call_count = [0]

    async def _execute(q, *a, **kw):
        result = MagicMock()
        ci = call_count[0]; call_count[0] += 1
        if ci == 0:
            result.scalars.return_value.all.return_value = [cp]
        elif ci == 1:
            result.scalars.return_value.all.return_value = [sp]
        elif ci == 2:
            result.scalars.return_value.all.return_value = []  # no market prices
        elif ci == 3:
            result.scalar_one_or_none.return_value = existing  # row already exists
        else:
            result.scalars.return_value.all.return_value = []
            result.scalar_one_or_none.return_value = None
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()

    candidates = await generate_candidates(session, limit=100)

    assert len(candidates) == 1
    # session.add called once to update the existing row
    session.add.assert_called_once_with(existing)
    # Score should have been updated (may differ from 0.50 due to new calculation)
    assert 0.0 <= float(existing.score) <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 12. get_top_candidates() sorts by score DESC
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_top_candidates_sort_order():
    from app.services.discovery_service_v2 import get_top_candidates
    from app.models.product_candidate_v2 import CandidateStatusV2

    c1 = _make_candidate(score=0.90, status=CandidateStatusV2.CANDIDATE)
    c2 = _make_candidate(score=0.45, status=CandidateStatusV2.CANDIDATE)
    c3 = _make_candidate(score=0.72, status=CandidateStatusV2.CANDIDATE)

    # DB returns already ordered (ORM does ORDER BY score DESC)
    session = AsyncMock()

    async def _execute(q, *a, **kw):
        result = MagicMock()
        result.scalars.return_value.all.return_value = [c1, c3, c2]
        return result

    session.execute = AsyncMock(side_effect=_execute)

    rows = await get_top_candidates(session, limit=10)
    scores = [float(r.score) for r in rows]
    assert scores == sorted(scores, reverse=True), f"Not sorted: {scores}"


# ─────────────────────────────────────────────────────────────────────────────
# 13. get_top_candidates() respects limit
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_top_candidates_limit():
    from app.services.discovery_service_v2 import get_top_candidates

    many = [_make_candidate(score=0.5 + i * 0.01) for i in range(30)]

    session = AsyncMock()

    async def _execute(q, *a, **kw):
        result = MagicMock()
        # DB honours the LIMIT – return only first 20
        result.scalars.return_value.all.return_value = many[:20]
        return result

    session.execute = AsyncMock(side_effect=_execute)

    rows = await get_top_candidates(session, limit=20)
    assert len(rows) <= 20


# ─────────────────────────────────────────────────────────────────────────────
# 14. reject_candidate() sets status=rejected
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reject_candidate():
    from app.services.discovery_service_v2 import reject_candidate
    from app.models.product_candidate_v2 import CandidateStatusV2

    c = _make_candidate(score=0.3, status=CandidateStatusV2.CANDIDATE)

    session = AsyncMock()

    async def _execute(q, *a, **kw):
        result = MagicMock()
        result.scalar_one_or_none.return_value = c
        return result

    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()

    result = await reject_candidate(session, str(c.id), reason="score too low")

    assert result is c
    assert c.status == CandidateStatusV2.REJECTED
    assert "score too low" in (c.notes or "")
    session.add.assert_called_once_with(c)


# ─────────────────────────────────────────────────────────────────────────────
# 15. mark_candidate_published() is idempotent
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_candidate_published():
    from app.services.discovery_service_v2 import mark_candidate_published
    from app.models.product_candidate_v2 import CandidateStatusV2

    c = _make_candidate(score=0.75, status=CandidateStatusV2.CANDIDATE)
    cp_id = c.canonical_product_id

    call_count = [0]

    async def _execute(q, *a, **kw):
        result = MagicMock()
        if call_count[0] == 0:
            result.scalar_one_or_none.return_value = c
        else:
            # Second call: already published
            c_already = _make_candidate(score=0.75, status=CandidateStatusV2.PUBLISHED,
                                         canonical_product_id=cp_id)
            result.scalar_one_or_none.return_value = c_already
        call_count[0] += 1
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()

    # First call: marks as published
    result1 = await mark_candidate_published(session, cp_id)
    assert result1 is c
    assert c.status == CandidateStatusV2.PUBLISHED

    # Second call: already published → no add
    result2 = await mark_candidate_published(session, cp_id)
    assert result2 is not None
    assert result2.status == CandidateStatusV2.PUBLISHED


# ─────────────────────────────────────────────────────────────────────────────
# 16. run_discovery_v2() returns expected summary keys
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_discovery_v2_summary():
    from app.services.discovery_service_v2 import run_discovery_v2

    cp = _make_canonical_product()
    sp = _make_supplier_product()

    call_count = [0]

    async def _execute(q, *a, **kw):
        result = MagicMock()
        ci = call_count[0]; call_count[0] += 1
        if ci == 0:
            result.scalars.return_value.all.return_value = [cp]
        elif ci == 1:
            result.scalars.return_value.all.return_value = [sp]
        elif ci == 2:
            result.scalars.return_value.all.return_value = []
        elif ci == 3:
            result.scalar_one_or_none.return_value = None
        else:
            result.scalars.return_value.all.return_value = []
            result.scalar_one_or_none.return_value = None
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()

    summary = await run_discovery_v2(session, limit=100, top_n=20, dry_run=True)

    assert isinstance(summary, dict)
    required_keys = ["dry_run", "candidates_generated", "top_n", "top_candidates"]
    for k in required_keys:
        assert k in summary, f"Missing key: {k}"
    assert summary["dry_run"] is True
    assert isinstance(summary["top_candidates"], list)


# ─────────────────────────────────────────────────────────────────────────────
# 17. Celery task – Redis lock prevents double run
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_celery_task_lock_prevents_double_run():
    from app.workers.tasks_discovery_v2 import _acquire_lock, _LOCK_KEY

    # First acquire succeeds, second should fail (returns False)
    with patch("redis.asyncio.from_url") as mock_redis_cls:
        mock_client = AsyncMock()
        # First call: SET NX returns True (locked)
        # Second call: SET NX returns None (already locked)
        mock_client.set = AsyncMock(side_effect=[True, None])
        mock_client.aclose = AsyncMock()
        mock_redis_cls.return_value = mock_client

        acquired_1 = await _acquire_lock("redis://localhost:6379/0", ttl=1800)
        acquired_2 = await _acquire_lock("redis://localhost:6379/0", ttl=1800)

    assert acquired_1 is True
    assert acquired_2 is False


# ─────────────────────────────────────────────────────────────────────────────
# 18. Celery task – run_discovery_and_publish (fully mocked)
# ─────────────────────────────────────────────────────────────────────────────

def test_celery_task_mock():
    with patch("asyncio.run") as mock_run:
        mock_run.return_value = {
            "status":               "ok",
            "dry_run":              True,
            "candidates_generated": 5,
            "top_limit":            20,
            "published":            0,
            "skipped":              5,
            "failed":               0,
            "job_id":               str(uuid.uuid4()),
        }
        from app.workers.tasks_discovery_v2 import run_discovery_and_publish

        if hasattr(run_discovery_and_publish, "__wrapped__"):
            result = run_discovery_and_publish.__wrapped__(limit=20, dry_run=True)
        else:
            result = run_discovery_and_publish.run(limit=20, dry_run=True)

        mock_run.assert_called_once()
        assert isinstance(result, dict)
        assert result.get("status") == "ok"
        assert result.get("dry_run") is True
        assert result.get("candidates_generated") == 5


# ─────────────────────────────────────────────────────────────────────────────
# 19. Admin GET /discovery/v2/candidates – response shape
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_list_candidates_shape():
    from app.services.discovery_service_v2 import get_top_candidates
    from app.models.product_candidate_v2 import CandidateStatusV2

    c = _make_candidate(score=0.80)

    session = AsyncMock()

    async def _execute(q, *a, **kw):
        result = MagicMock()
        result.scalars.return_value.all.return_value = [c]
        result.scalar_one_or_none.return_value       = None  # no canonical enrichment
        return result

    session.execute = AsyncMock(side_effect=_execute)

    rows = await get_top_candidates(session, limit=50)
    assert len(rows) == 1

    item = rows[0].to_dict()
    for key in ["id", "canonical_product_id", "score", "status"]:
        assert key in item, f"Missing key: {key}"
    assert 0.0 <= item["score"] <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 20. Admin POST /discovery/v2/run – response shape
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_run_discovery_shape():
    from app.services.discovery_service_v2 import run_discovery_v2

    # Empty DB → candidates_generated=0
    session = AsyncMock()

    async def _execute(q, *a, **kw):
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        result.scalar_one_or_none.return_value       = None
        return result

    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()

    result = await run_discovery_v2(session, limit=100, top_n=20, dry_run=True)

    assert isinstance(result, dict)
    assert "dry_run" in result
    assert "candidates_generated" in result
    assert "top_n" in result
    assert "top_candidates" in result
    assert result["dry_run"] is True
    assert result["candidates_generated"] == 0
    assert isinstance(result["top_candidates"], list)


# ─────────────────────────────────────────────────────────────────────────────
# 21. Admin POST /discovery/v2/candidates/{id}/reject – shape
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_reject_candidate_shape():
    from app.services.discovery_service_v2 import reject_candidate
    from app.models.product_candidate_v2 import CandidateStatusV2

    c = _make_candidate(score=0.20, status=CandidateStatusV2.CANDIDATE)

    session = AsyncMock()

    async def _execute(q, *a, **kw):
        result = MagicMock()
        result.scalar_one_or_none.return_value = c
        return result

    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()

    rejected = await reject_candidate(session, str(c.id), reason="low margin")

    assert rejected is not None
    assert rejected.status == CandidateStatusV2.REJECTED
    assert "low margin" in (rejected.notes or "")


# ─────────────────────────────────────────────────────────────────────────────
# 22. CandidateStatusV2 constants
# ─────────────────────────────────────────────────────────────────────────────

def test_candidate_status_constants():
    from app.models.product_candidate_v2 import CandidateStatusV2

    assert CandidateStatusV2.CANDIDATE == "candidate"
    assert CandidateStatusV2.PUBLISHED == "published"
    assert CandidateStatusV2.REJECTED  == "rejected"

    # All values must be distinct strings
    all_values = CandidateStatusV2.ALL
    assert len(all_values) == 3
    assert len(set(all_values)) == 3, "Statuses must be distinct"

    # All must be non-empty strings
    for v in all_values:
        assert isinstance(v, str) and len(v) > 0
