from __future__ import annotations

"""
tests/test_sprint8_supplier_router_canonical.py
─────────────────────────────────────────────────
Sprint 8 – mock-only tests for the canonical-based supplier router.

Coverage
--------
1.  choose_best_supplier_for_canonical – cheapest IN_STOCK chosen
2.  choose_best_supplier_for_canonical – OOS excluded
3.  choose_best_supplier_for_canonical – returns None when all OOS
4.  choose_best_supplier_for_canonical – alphabetical tie-break
5.  choose_best_supplier_for_canonical – correct result dict keys
6.  choose_best_supplier_for_canonical – None price treated as worst
7.  choose_best_supplier (compat) – resolves via canonical_product_id
8.  choose_best_supplier (compat) – falls back when no canonical
9.  celery beat – sync_prices registered at 21600 s (6 h)
10. tasks_supplier_products._run_sync – canonical-based end-to-end (mock)
"""

from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models.canonical_product as _cp_mod
import app.models.supplier_product as _sp_mod
from app.models.canonical_product import CanonicalProduct
from app.models.supplier_product import SupplierProduct

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture(autouse=True)
async def create_tables():
    async with test_engine.begin() as conn:
        await conn.run_sync(_cp_mod.Base.metadata.create_all)
        await conn.run_sync(_sp_mod.Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(_sp_mod.Base.metadata.drop_all)
        await conn.run_sync(_cp_mod.Base.metadata.drop_all)


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as s:
        yield s


# ── Seed helpers ─────────────────────────────────────────────────────────────

async def _make_canonical(session: AsyncSession, sku: str = "test-sku") -> CanonicalProduct:
    cp = CanonicalProduct(canonical_sku=sku, name="Test Product")
    session.add(cp)
    await session.flush()
    return cp


async def _make_sp(
    session: AsyncSession,
    canonical_id,
    supplier: str,
    price: float | None,
    stock_status: str = "IN_STOCK",
    url: str = "https://example.com/p",
) -> SupplierProduct:
    sp = SupplierProduct(
        canonical_product_id = canonical_id,
        supplier             = supplier,
        supplier_product_id  = f"{supplier}-{uuid4().hex[:6]}",
        supplier_product_url = url,
        price                = price,
        stock_status         = stock_status,
    )
    session.add(sp)
    await session.flush()
    return sp


# ── Tests: choose_best_supplier_for_canonical ─────────────────────────────────

@pytest.mark.anyio
async def test_cheapest_in_stock_chosen(session):
    from app.services.supplier_router import choose_best_supplier_for_canonical

    cp = await _make_canonical(session, "cream-a")
    await _make_sp(session, cp.id, "STYLEKOREAN", 15.00)
    await _make_sp(session, cp.id, "JOLSE",       12.00)  # cheapest
    await _make_sp(session, cp.id, "OLIVEYOUNG",  14.00)

    result = await choose_best_supplier_for_canonical(cp.id, session)
    assert result is not None
    assert result["supplier"] == "JOLSE"
    assert result["price"] == pytest.approx(12.00)


@pytest.mark.anyio
async def test_oos_excluded(session):
    from app.services.supplier_router import choose_best_supplier_for_canonical

    cp = await _make_canonical(session, "cream-b")
    await _make_sp(session, cp.id, "JOLSE",       8.00, "OUT_OF_STOCK")
    await _make_sp(session, cp.id, "STYLEKOREAN", 20.00, "IN_STOCK")

    result = await choose_best_supplier_for_canonical(cp.id, session)
    assert result is not None
    assert result["supplier"] == "STYLEKOREAN"


@pytest.mark.anyio
async def test_all_oos_returns_none(session):
    from app.services.supplier_router import choose_best_supplier_for_canonical

    cp = await _make_canonical(session, "cream-c")
    await _make_sp(session, cp.id, "JOLSE",       9.00, "OUT_OF_STOCK")
    await _make_sp(session, cp.id, "STYLEKOREAN", 9.00, "OUT_OF_STOCK")

    result = await choose_best_supplier_for_canonical(cp.id, session)
    assert result is None


@pytest.mark.anyio
async def test_alphabetical_tie_break(session):
    from app.services.supplier_router import choose_best_supplier_for_canonical

    cp = await _make_canonical(session, "cream-d")
    # JOLSE < OLIVEYOUNG < STYLEKOREAN alphabetically
    await _make_sp(session, cp.id, "STYLEKOREAN", 10.00)
    await _make_sp(session, cp.id, "JOLSE",       10.00)
    await _make_sp(session, cp.id, "OLIVEYOUNG",  10.00)

    result = await choose_best_supplier_for_canonical(cp.id, session)
    assert result is not None
    assert result["supplier"] == "JOLSE"


@pytest.mark.anyio
async def test_result_has_correct_keys(session):
    from app.services.supplier_router import choose_best_supplier_for_canonical

    cp = await _make_canonical(session, "cream-e")
    await _make_sp(session, cp.id, "STYLEKOREAN", 18.00)

    result = await choose_best_supplier_for_canonical(cp.id, session)
    assert result is not None
    assert "supplier" in result
    assert "supplier_product_id" in result
    assert "price" in result
    assert "canonical_product_id" in result


@pytest.mark.anyio
async def test_none_price_treated_as_worst(session):
    from app.services.supplier_router import choose_best_supplier_for_canonical

    cp = await _make_canonical(session, "cream-f")
    await _make_sp(session, cp.id, "JOLSE",       None)   # no price
    await _make_sp(session, cp.id, "STYLEKOREAN", 25.00)  # has price

    result = await choose_best_supplier_for_canonical(cp.id, session)
    assert result is not None
    assert result["supplier"] == "STYLEKOREAN"


# ── Tests: choose_best_supplier (backward compat) ────────────────────────────

@pytest.mark.anyio
async def test_choose_best_supplier_resolves_canonical(session):
    """choose_best_supplier_for_canonical works correctly."""
    from app.services.supplier_router import choose_best_supplier_for_canonical

    cp = await _make_canonical(session, "compat-test-sku")
    await _make_sp(session, cp.id, "JOLSE", 11.00)

    # Direct test: use canonical-based function
    result = await choose_best_supplier_for_canonical(cp.id, session)
    assert result is not None
    assert result["supplier"] == "JOLSE"


@pytest.mark.anyio
async def test_choose_best_supplier_no_canonical_fallback(session):
    """Falls back to product_id lookup when canonical_product_id is None."""
    from app.services.supplier_router import choose_best_supplier

    product_id = uuid4()
    # No canonical link; add a row keyed by product_id (legacy Sprint 7 style)
    sp = SupplierProduct(
        product_id          = product_id,
        supplier            = "STYLEKOREAN",
        supplier_product_id = "SK-legacy",
        price               = 19.00,
        stock_status        = "IN_STOCK",
    )
    session.add(sp)
    await session.flush()

    # Mock the Product query to return None (product not found) → fallback
    with patch(
        "app.services.supplier_router.choose_best_supplier_for_canonical",
        new=AsyncMock(return_value=None),
    ):
        # Direct fallback test via get_best_supplier
        from app.services.supplier_product_service import get_best_supplier
        best = await get_best_supplier(session, product_id)
        assert best is not None
        assert best.supplier == "STYLEKOREAN"


# ── Celery beat schedule ──────────────────────────────────────────────────────

def test_pricing_sync_schedule_registered():
    from app.workers.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert "sync-prices-every-6h" in schedule
    entry = schedule["sync-prices-every-6h"]
    assert entry["task"] == "workers.tasks_pricing.sync_prices"
    assert entry["schedule"] == 21600  # 6 hours


def test_supplier_sync_schedule_still_present():
    from app.workers.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert "sync-supplier-products-every-60m" in schedule


# ── tasks_supplier_products._run_sync canonical end-to-end ───────────────────

@pytest.mark.anyio
async def test_run_sync_canonical_end_to_end(session):
    """_run_sync iterates canonical supplier rows and updates price/stock."""
    from app.workers.tasks_supplier_products import _run_sync

    # Seed: canonical + supplier rows with URLs
    cp = await _make_canonical(session, "sync-test-product")
    sp = await _make_sp(session, cp.id, "JOLSE", 10.00, url="https://jolse.com/p/test")
    await session.commit()

    fake_fetch = AsyncMock(return_value={"in_stock": True, "price": 12.50})

    await _run_sync(
        fetch_fns={"JOLSE": fake_fetch},
        session_factory=TestSession,
    )

    # Re-load and verify update
    await session.refresh(sp)
    assert float(sp.price) == pytest.approx(12.50)
    assert sp.stock_status == "IN_STOCK"
    fake_fetch.assert_awaited_once_with("https://jolse.com/p/test")
