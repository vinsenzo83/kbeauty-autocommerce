from __future__ import annotations

"""
tests/test_sprint7_supplier_router.py
───────────────────────────────────────
Sprint 7 – mock-only tests for the supplier router.

Coverage
--------
1.  choose_best_supplier – cheapest IN_STOCK chosen
2.  choose_best_supplier – OOS supplier ignored
3.  choose_best_supplier – returns None when all OOS
4.  choose_best_supplier – tie-breaker deterministic
5.  choose_best_supplier – result dict has correct keys
6.  choose_supplier      – legacy function returns correct client type
7.  choose_supplier      – unknown supplier falls back to StyleKorean
8.  _make_client         – JOLSE returns JolseClient
9.  _make_client         – OLIVEYOUNG returns OliveYoungClient
10. jolse_inventory      – fetch_inventory mock in_stock
11. jolse_inventory      – fetch_inventory mock oos
12. oliveyoung_inventory – fetch_inventory mock in_stock
13. oliveyoung_inventory – _normalise_price helpers
14. celery beat schedule – sync_supplier_products registered at 3600s
15. tasks_supplier_products – _run_sync end-to-end mock
"""

from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.supplier_product import Base as SpBase, SupplierProduct

# ── In-memory SQLite DB ───────────────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture(autouse=True)
async def create_tables():
    async with test_engine.begin() as conn:
        await conn.run_sync(SpBase.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(SpBase.metadata.drop_all)


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as s:
        yield s


# ── Seed helper ───────────────────────────────────────────────────────────────

async def _seed(
    session: AsyncSession,
    product_id: object,
    *rows: dict,
) -> None:
    """Seed supplier_products rows then commit."""
    for r in rows:
        sp = SupplierProduct(
            product_id          = product_id,
            supplier            = r["supplier"],
            supplier_product_id = r.get("sp_id", "test-sku"),
            price               = r.get("price"),
            stock_status        = r.get("stock_status", "IN_STOCK"),
        )
        session.add(sp)
    await session.commit()


# ═════════════════════════════════════════════════════════════════════════════
# 1. choose_best_supplier – cheapest IN_STOCK chosen
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_choose_best_supplier_cheapest(session: AsyncSession):
    from app.services.supplier_router import choose_best_supplier

    pid = uuid4()
    await _seed(session, pid,
        {"supplier": "STYLEKOREAN", "price": 25.00, "stock_status": "IN_STOCK"},
        {"supplier": "JOLSE",       "price": 12.00, "stock_status": "IN_STOCK"},
        {"supplier": "OLIVEYOUNG",  "price": 18.00, "stock_status": "IN_STOCK"},
    )

    result = await choose_best_supplier(pid, session)
    assert result is not None
    assert result["supplier"] == "JOLSE"
    assert result["price"] == pytest.approx(12.00)


# ═════════════════════════════════════════════════════════════════════════════
# 2. choose_best_supplier – OOS supplier ignored
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_choose_best_supplier_ignores_oos(session: AsyncSession):
    from app.services.supplier_router import choose_best_supplier

    pid = uuid4()
    await _seed(session, pid,
        {"supplier": "JOLSE",       "price": 8.00,  "stock_status": "OUT_OF_STOCK"},
        {"supplier": "STYLEKOREAN", "price": 15.00, "stock_status": "IN_STOCK"},
    )

    result = await choose_best_supplier(pid, session)
    assert result is not None
    assert result["supplier"] == "STYLEKOREAN"
    assert result["price"] == pytest.approx(15.00)


# ═════════════════════════════════════════════════════════════════════════════
# 3. choose_best_supplier – None when all OOS
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_choose_best_supplier_all_oos_returns_none(session: AsyncSession):
    from app.services.supplier_router import choose_best_supplier

    pid = uuid4()
    await _seed(session, pid,
        {"supplier": "STYLEKOREAN", "price": 15.00, "stock_status": "OUT_OF_STOCK"},
        {"supplier": "JOLSE",       "price": 12.00, "stock_status": "OUT_OF_STOCK"},
    )

    result = await choose_best_supplier(pid, session)
    assert result is None


# ═════════════════════════════════════════════════════════════════════════════
# 4. choose_best_supplier – tie-breaker deterministic
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_choose_best_supplier_tie_breaker(session: AsyncSession):
    from app.services.supplier_router import choose_best_supplier

    pid = uuid4()
    await _seed(session, pid,
        {"supplier": "STYLEKOREAN", "price": 10.00, "stock_status": "IN_STOCK"},
        {"supplier": "JOLSE",       "price": 10.00, "stock_status": "IN_STOCK"},
        {"supplier": "OLIVEYOUNG",  "price": 10.00, "stock_status": "IN_STOCK"},
    )

    result = await choose_best_supplier(pid, session)
    # JOLSE wins (lowest _SUPPLIER_PRIORITY = 1)
    assert result is not None
    assert result["supplier"] == "JOLSE"


# ═════════════════════════════════════════════════════════════════════════════
# 5. choose_best_supplier – result dict shape
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_choose_best_supplier_result_keys(session: AsyncSession):
    from app.services.supplier_router import choose_best_supplier

    pid = uuid4()
    await _seed(session, pid,
        {"supplier": "STYLEKOREAN", "sp_id": "sk-xyz", "price": 20.00, "stock_status": "IN_STOCK"},
    )

    result = await choose_best_supplier(pid, session)
    assert result is not None
    assert set(result.keys()) == {"supplier", "supplier_product_id", "price"}
    assert result["supplier_product_id"] == "sk-xyz"


# ═════════════════════════════════════════════════════════════════════════════
# 6. choose_supplier (legacy) – returns StyleKoreanClient by default
# ═════════════════════════════════════════════════════════════════════════════

def test_choose_supplier_legacy_returns_stylekorean():
    from app.services.supplier_router import choose_supplier
    from app.suppliers.stylekorean import StyleKoreanClient

    order = MagicMock()
    order.id       = uuid4()
    order.supplier = None   # not set → fall back to stylekorean

    client = choose_supplier(order)
    assert isinstance(client, StyleKoreanClient)


# ═════════════════════════════════════════════════════════════════════════════
# 7. choose_supplier (legacy) – explicit supplier on order
# ═════════════════════════════════════════════════════════════════════════════

def test_choose_supplier_honours_order_supplier():
    from app.services.supplier_router import choose_supplier
    from app.suppliers.jolse import JolseClient

    order = MagicMock()
    order.id       = uuid4()
    order.supplier = "jolse"

    client = choose_supplier(order)
    assert isinstance(client, JolseClient)


# ═════════════════════════════════════════════════════════════════════════════
# 8. _make_client – JOLSE
# ═════════════════════════════════════════════════════════════════════════════

def test_make_client_jolse():
    from app.services.supplier_router import _make_client
    from app.suppliers.jolse import JolseClient

    client = _make_client("JOLSE")
    assert isinstance(client, JolseClient)


# ═════════════════════════════════════════════════════════════════════════════
# 9. _make_client – OLIVEYOUNG
# ═════════════════════════════════════════════════════════════════════════════

def test_make_client_oliveyoung():
    from app.services.supplier_router import _make_client
    from app.suppliers.oliveyoung import OliveYoungClient

    client = _make_client("OLIVEYOUNG")
    assert isinstance(client, OliveYoungClient)


# ═════════════════════════════════════════════════════════════════════════════
# 10. jolse_inventory – fetch_inventory in_stock via mock page
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_jolse_inventory_in_stock():
    from app.crawlers.jolse_inventory import fetch_inventory

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    # No OOS selectors found
    mock_page.query_selector = AsyncMock(return_value=None)

    # Price element
    price_el = AsyncMock()
    price_el.get_attribute = AsyncMock(return_value="14.99")
    price_el.inner_text    = AsyncMock(return_value="$14.99")

    async def _qs(sel):
        if "[itemprop='price']" in sel:
            return price_el
        return None

    mock_page.query_selector = _qs

    result = await fetch_inventory("https://jolse.com/product/test", page=mock_page)
    assert result["in_stock"] is True
    assert result["price"] == pytest.approx(14.99)


# ═════════════════════════════════════════════════════════════════════════════
# 11. jolse_inventory – fetch_inventory OOS
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_jolse_inventory_oos():
    from app.crawlers.jolse_inventory import fetch_inventory

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()

    async def _qs(sel):
        if sel == ".sold-out":
            return MagicMock()   # presence selector truthy
        return None

    mock_page.query_selector = _qs

    result = await fetch_inventory("https://jolse.com/product/test", page=mock_page)
    assert result["in_stock"] is False


# ═════════════════════════════════════════════════════════════════════════════
# 12. oliveyoung_inventory – fetch_inventory in_stock
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_oliveyoung_inventory_in_stock():
    from app.crawlers.oliveyoung_inventory import fetch_inventory

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.query_selector = AsyncMock(return_value=None)

    price_el = AsyncMock()
    price_el.get_attribute = AsyncMock(return_value="22.50")
    price_el.inner_text    = AsyncMock(return_value="$22.50")

    async def _qs(sel):
        if "[itemprop='price']" in sel:
            return price_el
        return None

    mock_page.query_selector = _qs

    result = await fetch_inventory("https://global.oliveyoung.com/product/test", page=mock_page)
    assert result["in_stock"] is True
    assert result["price"] == pytest.approx(22.50)


# ═════════════════════════════════════════════════════════════════════════════
# 13. oliveyoung _normalise_price
# ═════════════════════════════════════════════════════════════════════════════

def test_oliveyoung_normalise_price():
    from app.crawlers.oliveyoung_inventory import _normalise_price
    assert _normalise_price("$12.50")   == pytest.approx(12.50)
    assert _normalise_price("₩15,000")  == pytest.approx(15000.0)
    assert _normalise_price("22.00 USD") == pytest.approx(22.00)
    assert _normalise_price("N/A") is None
    assert _normalise_price("") is None


def test_jolse_normalise_price():
    from app.crawlers.jolse_inventory import _normalise_price
    assert _normalise_price("$9.99")   == pytest.approx(9.99)
    assert _normalise_price("12,500")  == pytest.approx(12500.0)
    assert _normalise_price("") is None


# ═════════════════════════════════════════════════════════════════════════════
# 14. Celery beat schedule – sync_supplier_products at 3600s
# ═════════════════════════════════════════════════════════════════════════════

def test_celery_beat_schedule_supplier_sync():
    from app.workers.celery_app import celery_app

    schedule   = celery_app.conf.beat_schedule
    task_names = [e["task"] for e in schedule.values()]
    assert "workers.tasks_supplier_products.sync_supplier_products" in task_names


def test_celery_beat_schedule_supplier_interval():
    from app.workers.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    entry    = schedule.get("sync-supplier-products-every-60m", {})
    assert entry.get("schedule") == 3600   # 60 min


# ═════════════════════════════════════════════════════════════════════════════
# 15. tasks_supplier_products – _run_sync end-to-end with mocks
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_run_sync_end_to_end():
    """
    _run_sync fetches inventory for all products and upserts supplier_products rows.
    Uses patched AsyncSessionLocal + fake fetch_fns (no real DB, no real network).

    Sprint 8 note: _run_sync now iterates canonical_products first.
    When canonical_products is empty, it falls back to legacy product-based sync.
    """
    # We need Product + CanonicalProduct model in the same SQLite DB
    from app.models.product import Base as PBase, Product
    import app.models.canonical_product as _cp_mod2

    # Use a dedicated engine per test run to avoid cross-backend UNIQUE conflicts
    from sqlalchemy.ext.asyncio import create_async_engine as _cae, async_sessionmaker as _asm
    local_engine  = _cae("sqlite+aiosqlite:///:memory:", echo=False)
    LocalSession  = _asm(local_engine, expire_on_commit=False, class_=AsyncSession)

    from app.models.supplier_product import Base as SpBase2
    async with local_engine.begin() as conn:
        await conn.run_sync(SpBase2.metadata.create_all)
        await conn.run_sync(PBase.metadata.create_all)
        await conn.run_sync(_cp_mod2.Base.metadata.create_all)

    pid = uuid4()
    product_url = "https://stylekorean.com/products/some-cream"

    async with LocalSession() as s:
        p = Product(
            id=pid,
            supplier="stylekorean",
            supplier_product_id=f"sk-run-sync-{pid.hex[:8]}",
            supplier_product_url=product_url,
            name="Test Cream",
            price="15.00",
        )
        s.add(p)
        await s.commit()

    # Fake fetch functions: all IN_STOCK with distinct prices
    fake_fetch_fns = {
        "STYLEKOREAN": AsyncMock(return_value={"in_stock": True,  "price": 15.00}),
        "JOLSE":       AsyncMock(return_value={"in_stock": True,  "price": 12.00}),
        "OLIVEYOUNG":  AsyncMock(return_value={"in_stock": False, "price": 11.00}),
    }

    from app.workers.tasks_supplier_products import _run_sync

    # canonical_products is empty → falls back to legacy product-based sync
    result = await _run_sync(fetch_fns=fake_fetch_fns, session_factory=LocalSession)

    # Legacy fallback returns total_canonicals=0 and uses rows_updated key
    assert result["total_canonicals"] == 0
    assert result["rows_updated"] == 3    # one per supplier (via legacy path)
    assert result["errors"]       == 0

    # Verify OliveYoung row is OUT_OF_STOCK
    async with LocalSession() as s:
        from sqlalchemy import select
        rows = (await s.execute(
            select(SupplierProduct).where(SupplierProduct.product_id == pid)
        )).scalars().all()

    by_supplier = {r.supplier: r for r in rows}
    assert by_supplier["OLIVEYOUNG"].stock_status == "OUT_OF_STOCK"
    assert by_supplier["JOLSE"].stock_status      == "IN_STOCK"
    assert float(by_supplier["JOLSE"].price)      == pytest.approx(12.00)

    await local_engine.dispose()
