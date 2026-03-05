from __future__ import annotations

"""
tests/test_sprint8_pricing_service.py
───────────────────────────────────────
Sprint 8 – mock-only tests for pricing_service.py.

Coverage
--------
1.  generate_quote – creates PriceQuote for cheapest IN_STOCK supplier
2.  generate_quote – returns None when no IN_STOCK supplier
3.  generate_quote – returns None when pricing_enabled=False
4.  generate_quote – returns None when canonical not found
5.  generate_quote – updates canonical_product.last_price
6.  generate_quote – best supplier with None price skipped
7.  apply_quote_to_shopify – calls Shopify update and returns True
8.  apply_quote_to_shopify – returns False when no ShopifyMapping
9.  apply_quote_to_shopify – returns False when no quote exists
10. apply_quote_to_shopify – idempotent (still calls Shopify)
"""

from decimal import Decimal
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models.canonical_product as _cp_mod
import app.models.supplier_product as _sp_mod
import app.models.shopify_mapping as _sm_mod
import app.models.price_quote as _pq_mod

from app.models.canonical_product import CanonicalProduct
from app.models.shopify_mapping import ShopifyMapping
from app.models.supplier_product import SupplierProduct
from app.models.price_quote import PriceQuote

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture(autouse=True)
async def create_tables():
    async with test_engine.begin() as conn:
        await conn.run_sync(_cp_mod.Base.metadata.create_all)
        await conn.run_sync(_sp_mod.Base.metadata.create_all)
        await conn.run_sync(_sm_mod.Base.metadata.create_all)
        await conn.run_sync(_pq_mod.Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(_pq_mod.Base.metadata.drop_all)
        await conn.run_sync(_sm_mod.Base.metadata.drop_all)
        await conn.run_sync(_sp_mod.Base.metadata.drop_all)
        await conn.run_sync(_cp_mod.Base.metadata.drop_all)


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as s:
        yield s


# ── Seed helpers ─────────────────────────────────────────────────────────────

async def _make_cp(
    session,
    sku="test-product",
    pricing_enabled=True,
    target_margin_rate="0.30",
    min_margin_abs="3.00",
    shipping_cost="3.00",
) -> CanonicalProduct:
    cp = CanonicalProduct(
        canonical_sku         = sku,
        name                  = "Test Product",
        pricing_enabled       = pricing_enabled,
        target_margin_rate    = Decimal(target_margin_rate),
        min_margin_abs        = Decimal(min_margin_abs),
        shipping_cost_default = Decimal(shipping_cost),
    )
    session.add(cp)
    await session.flush()
    return cp


async def _make_sp(session, canonical_id, supplier, price, stock_status="IN_STOCK"):
    sp = SupplierProduct(
        canonical_product_id = canonical_id,
        supplier             = supplier,
        supplier_product_id  = f"{supplier}-{uuid4().hex[:6]}",
        price                = Decimal(str(price)) if price is not None else None,
        stock_status         = stock_status,
    )
    session.add(sp)
    await session.flush()
    return sp


async def _make_sm(session, canonical_id, variant_id="var-001") -> ShopifyMapping:
    sm = ShopifyMapping(
        canonical_product_id = canonical_id,
        shopify_product_id   = "prod-001",
        shopify_variant_id   = variant_id,
    )
    session.add(sm)
    await session.flush()
    return sm


# ─────────────────────────────────────────────────────────────────────────────
# generate_quote tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_generate_quote_creates_row(session):
    from app.services.pricing_service import generate_quote

    cp = await _make_cp(session, "product-a")
    await _make_sp(session, cp.id, "JOLSE", 10.00)

    quote = await generate_quote(cp.id, session)
    assert quote is not None
    assert isinstance(quote, PriceQuote)
    assert quote.supplier == "JOLSE"
    assert float(quote.supplier_price) == pytest.approx(10.00)
    # rounded_price must be *.99
    assert str(quote.rounded_price).endswith(".99")


@pytest.mark.anyio
async def test_generate_quote_no_in_stock_returns_none(session):
    from app.services.pricing_service import generate_quote

    cp = await _make_cp(session, "product-b")
    await _make_sp(session, cp.id, "JOLSE", 10.00, "OUT_OF_STOCK")

    quote = await generate_quote(cp.id, session)
    assert quote is None


@pytest.mark.anyio
async def test_generate_quote_pricing_disabled_returns_none(session):
    from app.services.pricing_service import generate_quote

    cp = await _make_cp(session, "product-c", pricing_enabled=False)
    await _make_sp(session, cp.id, "JOLSE", 10.00)

    quote = await generate_quote(cp.id, session)
    assert quote is None


@pytest.mark.anyio
async def test_generate_quote_canonical_not_found_returns_none(session):
    from app.services.pricing_service import generate_quote

    fake_id = uuid4()
    quote   = await generate_quote(fake_id, session)
    assert quote is None


@pytest.mark.anyio
async def test_generate_quote_updates_last_price(session):
    from app.services.pricing_service import generate_quote
    from sqlalchemy import select

    cp = await _make_cp(session, "product-d")
    await _make_sp(session, cp.id, "STYLEKOREAN", 12.00)

    await generate_quote(cp.id, session)

    result = await session.execute(
        select(CanonicalProduct).where(CanonicalProduct.id == cp.id)
    )
    cp_refreshed = result.scalar_one()
    assert cp_refreshed.last_price is not None
    assert str(cp_refreshed.last_price).endswith(".99")


@pytest.mark.anyio
async def test_generate_quote_picks_cheapest_supplier(session):
    from app.services.pricing_service import generate_quote

    cp = await _make_cp(session, "product-e")
    await _make_sp(session, cp.id, "STYLEKOREAN", 15.00)
    await _make_sp(session, cp.id, "JOLSE",       11.00)  # cheapest

    quote = await generate_quote(cp.id, session)
    assert quote is not None
    assert quote.supplier == "JOLSE"


@pytest.mark.anyio
async def test_generate_quote_skips_supplier_with_none_price(session):
    from app.services.pricing_service import generate_quote

    cp = await _make_cp(session, "product-f")
    await _make_sp(session, cp.id, "JOLSE",       None)   # no price → skipped as cheapest
    await _make_sp(session, cp.id, "STYLEKOREAN", 18.00)  # has price

    quote = await generate_quote(cp.id, session)
    assert quote is not None
    assert quote.supplier == "STYLEKOREAN"


# ─────────────────────────────────────────────────────────────────────────────
# apply_quote_to_shopify tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_apply_quote_calls_shopify(session):
    from app.services.pricing_service import apply_quote_to_shopify, generate_quote

    cp = await _make_cp(session, "shopify-a")
    await _make_sp(session, cp.id, "JOLSE", 10.00)
    await _make_sm(session, cp.id, variant_id="var-shopify-a")

    await generate_quote(cp.id, session)

    mock_shopify = MagicMock()
    mock_shopify.update_variant_price_by_id = AsyncMock(return_value=True)

    ok = await apply_quote_to_shopify(cp.id, session, mock_shopify)
    assert ok is True
    mock_shopify.update_variant_price_by_id.assert_awaited_once()
    call_args = mock_shopify.update_variant_price_by_id.call_args
    assert call_args[0][0] == "var-shopify-a"  # variant_id


@pytest.mark.anyio
async def test_apply_quote_no_mapping_returns_false(session):
    from app.services.pricing_service import apply_quote_to_shopify, generate_quote

    cp = await _make_cp(session, "shopify-b")
    await _make_sp(session, cp.id, "JOLSE", 10.00)
    # No ShopifyMapping row
    await generate_quote(cp.id, session)

    mock_shopify = MagicMock()
    mock_shopify.update_variant_price_by_id = AsyncMock(return_value=True)

    ok = await apply_quote_to_shopify(cp.id, session, mock_shopify)
    assert ok is False
    mock_shopify.update_variant_price_by_id.assert_not_awaited()


@pytest.mark.anyio
async def test_apply_quote_no_quote_returns_false(session):
    from app.services.pricing_service import apply_quote_to_shopify

    cp = await _make_cp(session, "shopify-c")
    await _make_sm(session, cp.id, variant_id="var-shopify-c")
    # No quote generated

    mock_shopify = MagicMock()
    mock_shopify.update_variant_price_by_id = AsyncMock(return_value=True)

    ok = await apply_quote_to_shopify(cp.id, session, mock_shopify)
    assert ok is False
    mock_shopify.update_variant_price_by_id.assert_not_awaited()


@pytest.mark.anyio
async def test_apply_quote_idempotent_still_calls_shopify(session):
    """Even if last_price matches, we still call Shopify (idempotent on their side)."""
    from app.services.pricing_service import apply_quote_to_shopify, generate_quote

    cp = await _make_cp(session, "shopify-d")
    await _make_sp(session, cp.id, "STYLEKOREAN", 15.00)
    await _make_sm(session, cp.id, variant_id="var-shopify-d")

    # Generate quote once (sets last_price)
    await generate_quote(cp.id, session)

    mock_shopify = MagicMock()
    mock_shopify.update_variant_price_by_id = AsyncMock(return_value=True)

    # Apply twice → should call Shopify both times
    ok1 = await apply_quote_to_shopify(cp.id, session, mock_shopify)
    ok2 = await apply_quote_to_shopify(cp.id, session, mock_shopify)
    assert ok1 is True
    assert ok2 is True
    assert mock_shopify.update_variant_price_by_id.await_count == 2
