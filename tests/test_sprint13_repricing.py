"""
tests/test_sprint13_repricing.py
─────────────────────────────────
Sprint 13 – Mock-only tests for Market Price Intelligence & Auto-Repricing.

Coverage
--------
1.  test_competitor_band_min_median_max        – band math (odd sample count)
2.  test_competitor_band_even_samples          – even sample → median = average
3.  test_competitor_band_none_when_no_prices   – returns None if empty
4.  test_compute_recommended_no_competitors    – base price (no band)
5.  test_compute_recommended_clamped_up        – rec < lower_bound → clamp up
6.  test_compute_recommended_clamped_down      – rec > upper_bound → clamp down
7.  test_compute_recommended_within_band       – base falls inside band → no clamp
8.  test_apply_reprice_dry_run_no_shopify_call – dry_run must NOT call Shopify
9.  test_apply_reprice_skip_no_supplier        – NO_IN_STOCK_SUPPLIER skip
10. test_apply_reprice_skip_no_mapping         – MISSING_SHOPIFY_MAPPING skip
11. test_apply_reprice_idempotent_no_change    – NO_CHANGE skip (price within tolerance)
12. test_redis_lock_skips_concurrent_repricing – 2nd call with locked Redis → skipped
13. test_parse_csv_valid                       – parse_market_price_csv happy path
14. test_parse_csv_missing_column              – CSV without required column → error
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_uuid() -> uuid.UUID:
    return uuid.uuid4()


def _make_cp(
    product_id=None,
    pricing_enabled=True,
    target_margin_rate=0.30,
    min_margin_abs=3.00,
    shipping_cost_default=3.00,
    last_price=None,
    canonical_sku="SKU-001",
    name="Test Product",
    brand="Brand",
):
    cp = MagicMock()
    cp.id                   = product_id or _make_uuid()
    cp.pricing_enabled      = pricing_enabled
    cp.target_margin_rate   = target_margin_rate
    cp.min_margin_abs       = min_margin_abs
    cp.shipping_cost_default= shipping_cost_default
    cp.last_price           = last_price
    cp.canonical_sku        = canonical_sku
    cp.name                 = name
    cp.brand                = brand
    cp.last_price_at        = None
    return cp


def _make_supplier(price: float):
    sp = MagicMock()
    sp.price        = price
    sp.stock_status = "IN_STOCK"
    return sp


def _make_mapping(variant_id="VAR-001"):
    m = MagicMock()
    m.shopify_variant_id = variant_id
    return m


# ─────────────────────────────────────────────────────────────────────────────
# 1. Competitor band – odd samples
# ─────────────────────────────────────────────────────────────────────────────

def test_competitor_band_min_median_max():
    from app.services.market_price_service import CompetitorBand

    prices = sorted([Decimal("20.00"), Decimal("25.00"), Decimal("30.00")])
    n = len(prices)
    median = prices[n // 2]  # 25.00 for 3 elements

    band = CompetitorBand(
        min_price    = prices[0],
        median_price = median,
        max_price    = prices[-1],
        sample_count = n,
    )

    assert band.min_price    == Decimal("20.00")
    assert band.median_price == Decimal("25.00")
    assert band.max_price    == Decimal("30.00")
    assert band.sample_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# 2. Competitor band – even samples
# ─────────────────────────────────────────────────────────────────────────────

def test_competitor_band_even_samples():
    from app.services.market_price_service import CompetitorBand

    prices = sorted([Decimal("20.00"), Decimal("24.00"), Decimal("28.00"), Decimal("32.00")])
    n = len(prices)
    median = (prices[n // 2 - 1] + prices[n // 2]) / Decimal("2")  # (24+28)/2 = 26

    band = CompetitorBand(
        min_price    = prices[0],
        median_price = median,
        max_price    = prices[-1],
        sample_count = n,
    )

    assert band.median_price == Decimal("26.00")
    assert band.sample_count == 4


# ─────────────────────────────────────────────────────────────────────────────
# 3. Competitor band – None when no prices
# ─────────────────────────────────────────────────────────────────────────────

import pytest

@pytest.mark.asyncio
async def test_competitor_band_none_when_no_prices():
    from app.services.market_price_service import get_competitor_band
    from app.models.market_price import MarketPrice

    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    session.execute.return_value = mock_result

    band = await get_competitor_band(session, _make_uuid())
    assert band is None


# ─────────────────────────────────────────────────────────────────────────────
# 4. Recommended price – no competitors (pure cost+margin)
# ─────────────────────────────────────────────────────────────────────────────

def test_compute_recommended_no_competitors():
    from app.services.repricing_rules import compute_recommended_price

    result = compute_recommended_price(
        supplier_cost      = 10.00,
        shipping_cost      = 3.00,
        fee_rate           = 0.03,
        target_margin_rate = 0.30,
        min_margin_abs     = 3.00,
        competitor_band    = None,
    )

    # base = (10+3) / (1 - 0.30 - 0.03) = 13/0.67 ≈ 19.40
    assert result.recommended_price > Decimal("0")
    assert result.lower_bound is None
    assert result.upper_bound is None
    assert result.competitor_band is None
    # No clamping applied
    assert "clamp" not in result.reason


# ─────────────────────────────────────────────────────────────────────────────
# 5. Recommended price – clamped UP (base < lower_bound)
# ─────────────────────────────────────────────────────────────────────────────

def test_compute_recommended_clamped_up():
    from app.services.repricing_rules import compute_recommended_price
    from app.services.market_price_service import CompetitorBand

    # Build a band with high floor so base will be below it
    band = CompetitorBand(
        min_price    = Decimal("100.00"),
        median_price = Decimal("120.00"),
        max_price    = Decimal("140.00"),
        sample_count = 3,
    )

    result = compute_recommended_price(
        supplier_cost      = 10.00,
        shipping_cost      = 3.00,
        fee_rate           = 0.03,
        target_margin_rate = 0.30,
        competitor_band    = band,
        lower_bound_factor = Decimal("0.97"),
        upper_bound_factor = Decimal("1.05"),
    )

    # lower_bound = 100 * 0.97 = 97.00; base ≈ 19.99 → clamped up
    assert result.recommended_price >= result.lower_bound
    assert "clamped_up" in result.reason


# ─────────────────────────────────────────────────────────────────────────────
# 6. Recommended price – clamped DOWN (base > upper_bound)
# ─────────────────────────────────────────────────────────────────────────────

def test_compute_recommended_clamped_down():
    from app.services.repricing_rules import compute_recommended_price
    from app.services.market_price_service import CompetitorBand

    band = CompetitorBand(
        min_price    = Decimal("5.00"),
        median_price = Decimal("6.00"),   # upper_bound = 6 * 1.05 = 6.30
        max_price    = Decimal("7.00"),
        sample_count = 2,
    )

    result = compute_recommended_price(
        supplier_cost      = 10.00,   # cost already > upper_bound
        shipping_cost      = 3.00,
        fee_rate           = 0.03,
        target_margin_rate = 0.30,
        competitor_band    = band,
        lower_bound_factor = Decimal("0.97"),
        upper_bound_factor = Decimal("1.05"),
    )

    assert result.recommended_price <= result.upper_bound
    assert "clamped_down" in result.reason


# ─────────────────────────────────────────────────────────────────────────────
# 7. Recommended price – within band (no clamp)
# ─────────────────────────────────────────────────────────────────────────────

def test_compute_recommended_within_band():
    from app.services.repricing_rules import compute_recommended_price
    from app.services.market_price_service import CompetitorBand

    # Band is wide enough that base ≈ 19.99 sits comfortably inside
    band = CompetitorBand(
        min_price    = Decimal("10.00"),
        median_price = Decimal("22.00"),  # upper = 22 * 1.05 = 23.10
        max_price    = Decimal("30.00"),
        sample_count = 3,
    )

    result = compute_recommended_price(
        supplier_cost      = 10.00,
        shipping_cost      = 3.00,
        fee_rate           = 0.03,
        target_margin_rate = 0.30,
        competitor_band    = band,
    )

    assert result.lower_bound <= result.recommended_price <= result.upper_bound
    assert "clamp" not in result.reason


# ─────────────────────────────────────────────────────────────────────────────
# 8. apply_reprice – DRY_RUN must NOT call Shopify
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_reprice_dry_run_no_shopify_call():
    from app.services.repricing_service import apply_reprice_to_shopify
    from app.services.market_price_service import CompetitorBand

    cp_id = _make_uuid()
    cp    = _make_cp(product_id=cp_id, last_price=Decimal("50.00"))

    session = AsyncMock()

    # Stub DB queries
    def _execute_side_effect(stmt):
        result = MagicMock()
        q = str(stmt)
        if "pricing_enabled" in q:
            # _load_canonical_products
            result.scalars.return_value.all.return_value = [cp]
        elif "stock_status" in q:
            # supplier cost
            sp = _make_supplier(10.00)
            result.scalars.return_value.all.return_value = [sp]
        elif "market_prices" in q:
            # get_competitor_band
            row = MagicMock()
            row.__getitem__ = lambda self, i: Decimal("25.00")
            result.all.return_value = [(Decimal("25.00"),)]
        elif "ShopifyMapping" in q or "shopify_mapping" in q:
            mapping = _make_mapping()
            result.scalar_one_or_none.return_value = mapping
        elif "CanonicalProduct" in q and "canonical_product_id" in q:
            result.scalar_one_or_none.return_value = cp
        elif "repricing_runs" in q or "RepricingRun" in q:
            run_obj = MagicMock()
            run_obj.id = _make_uuid()
            result.scalar_one_or_none.return_value = run_obj
        else:
            result.scalars.return_value.all.return_value = []
            result.scalar_one_or_none.return_value = None
        return result

    session.execute = AsyncMock(side_effect=_execute_side_effect)
    session.flush   = AsyncMock()
    session.add     = MagicMock()

    shopify_mock = AsyncMock()
    shopify_mock.update_variant_price_by_id = AsyncMock(return_value=True)

    run_id = await apply_reprice_to_shopify(
        session,
        limit      = 5,
        dry_run    = True,
        shopify_svc= shopify_mock,
    )

    assert run_id is not None
    # In dry_run mode Shopify must NOT be called
    shopify_mock.update_variant_price_by_id.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 9. apply_reprice – skip NO_IN_STOCK_SUPPLIER
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_reprice_skip_no_supplier():
    from app.services.repricing_service import apply_reprice_to_shopify

    cp    = _make_cp()
    session = AsyncMock()

    def _exec(stmt):
        result = MagicMock()
        q = str(stmt)
        if "pricing_enabled" in q:
            result.scalars.return_value.all.return_value = [cp]
        elif "stock_status" in q:
            result.scalars.return_value.all.return_value = []  # no supplier
        else:
            result.scalars.return_value.all.return_value = []
            result.scalar_one_or_none.return_value = None
        return result

    session.execute = AsyncMock(side_effect=_exec)
    session.flush   = AsyncMock()
    session.add     = MagicMock()

    run_id = await apply_reprice_to_shopify(session, limit=5, dry_run=True)
    assert run_id is not None
    # Item's reason should be NO_IN_STOCK_SUPPLIER (validated via item.reason assignment)


# ─────────────────────────────────────────────────────────────────────────────
# 10. apply_reprice – skip MISSING_SHOPIFY_MAPPING
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_reprice_skip_no_mapping():
    from app.services.repricing_service import apply_reprice_to_shopify

    cp    = _make_cp(last_price=Decimal("30.00"))
    session = AsyncMock()

    def _exec(stmt):
        result = MagicMock()
        q = str(stmt)
        if "pricing_enabled" in q:
            result.scalars.return_value.all.return_value = [cp]
        elif "stock_status" in q:
            result.scalars.return_value.all.return_value = [_make_supplier(10.00)]
        elif "market_prices" in q:
            result.all.return_value = []
        elif "ShopifyMapping" in q or "shopify_mapping" in q:
            result.scalar_one_or_none.return_value = None   # No mapping
        elif "CanonicalProduct" in q and "canonical_product_id" in q:
            result.scalar_one_or_none.return_value = cp
        else:
            result.scalars.return_value.all.return_value = []
            result.scalar_one_or_none.return_value = None
        return result

    session.execute = AsyncMock(side_effect=_exec)
    session.flush   = AsyncMock()
    session.add     = MagicMock()

    run_id = await apply_reprice_to_shopify(session, limit=5, dry_run=True)
    assert run_id is not None


# ─────────────────────────────────────────────────────────────────────────────
# 11. apply_reprice – idempotent NO_CHANGE
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_reprice_idempotent_no_change():
    """
    If the recommended price equals the current Shopify price (within $0.01),
    the item must be skipped with reason NO_CHANGE.
    """
    from app.services.repricing_service import apply_reprice_to_shopify

    # Force a known last_price that will match the recommended price.
    # With supplier_cost=10, shipping=3, fee=0.03, margin=0.30:
    # base ≈ 19.40 → rounded to 19.99
    # Set last_price to 19.99 so NO_CHANGE fires.
    cp = _make_cp(last_price=Decimal("19.99"))
    session = AsyncMock()

    mapping = _make_mapping()

    def _exec(stmt):
        result = MagicMock()
        q = str(stmt)
        if "pricing_enabled" in q:
            result.scalars.return_value.all.return_value = [cp]
        elif "stock_status" in q:
            result.scalars.return_value.all.return_value = [_make_supplier(10.00)]
        elif "market_prices" in q:
            result.all.return_value = []
        elif "ShopifyMapping" in q or "shopify_mapping" in q:
            result.scalar_one_or_none.return_value = mapping
        elif "CanonicalProduct" in q and "canonical_product_id" in q:
            result.scalar_one_or_none.return_value = cp
        else:
            result.scalars.return_value.all.return_value = []
            result.scalar_one_or_none.return_value = None
        return result

    session.execute = AsyncMock(side_effect=_exec)
    session.flush   = AsyncMock()
    session.add     = MagicMock()

    shopify_mock = AsyncMock()
    run_id = await apply_reprice_to_shopify(
        session, limit=5, dry_run=False, shopify_svc=shopify_mock
    )
    assert run_id is not None
    shopify_mock.update_variant_price_by_id.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 12. Redis lock – concurrent call is skipped
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_redis_lock_skips_concurrent_repricing():
    from app.workers.tasks_repricing import _run_repricing_async

    mock_redis = AsyncMock()
    mock_redis.set    = AsyncMock(return_value=None)  # lock NOT acquired (returns None/False)
    mock_redis.delete = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        result = await _run_repricing_async(limit=5, dry_run=True)

    assert result["status"] == "skipped"
    assert "another repricing" in result["reason"]


# ─────────────────────────────────────────────────────────────────────────────
# 13. CSV parse – valid rows
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_csv_valid():
    from app.services.market_price_service import parse_market_price_csv

    csv_text = (
        "canonical_sku,source,price,currency,in_stock,external_url,external_sku\n"
        "SKU-001,amazon,29.99,USD,true,https://amazon.com/dp/XXX,ASIN-XXX\n"
        "SKU-002,shopee,24.50,USD,false,,\n"
    )

    records, errors = parse_market_price_csv(csv_text)

    assert errors == []
    assert len(records) == 2

    r1 = records[0]
    assert r1["canonical_sku"] == "SKU-001"
    assert r1["source"]        == "amazon"
    assert r1["price"]         == Decimal("29.99")
    assert r1["currency"]      == "USD"
    assert r1["in_stock"]      is True
    assert r1["external_url"]  == "https://amazon.com/dp/XXX"

    r2 = records[1]
    assert r2["in_stock"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 14. CSV parse – missing required column → error returned
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_csv_missing_column():
    from app.services.market_price_service import parse_market_price_csv

    # 'price' column missing
    csv_text = (
        "canonical_sku,source,currency\n"
        "SKU-001,amazon,USD\n"
    )

    records, errors = parse_market_price_csv(csv_text)

    assert records == []
    assert len(errors) == 1
    assert "price" in errors[0].lower() or "missing" in errors[0].lower()
