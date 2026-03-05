from __future__ import annotations

"""
tests/test_sprint7_supplier_products.py
────────────────────────────────────────
Sprint 7 – mock-only tests for the supplier_products table service.

All tests use an in-memory SQLite DB (aiosqlite) – no PostgreSQL, no network.

Coverage
--------
1.  upsert_supplier_product – creates a new row
2.  upsert_supplier_product – updates an existing row (idempotent)
3.  get_supplier_products   – returns rows ordered cheapest first
4.  get_best_supplier       – returns cheapest IN_STOCK row
5.  get_best_supplier       – ignores OUT_OF_STOCK rows
6.  get_best_supplier       – returns None when all OOS
7.  get_best_supplier       – deterministic tie-breaker (alphabetical supplier name)
8.  update_supplier_price   – updates price and returns True
9.  update_supplier_price   – returns False when row missing
10. update_supplier_stock   – flips stock_status
11. update_supplier_stock   – returns False when row missing
12. save_supplier_product   – backward-compat alias works
"""

from datetime import datetime, timezone
from typing import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.supplier_product import Base as SpBase, SupplierProduct

# ── In-memory SQLite DB ───────────────────────────────────────────────────────

TEST_DB_URL  = "sqlite+aiosqlite:///:memory:"
test_engine  = create_async_engine(TEST_DB_URL, echo=False)
TestSession  = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pid() -> object:
    """Generate a fresh UUID for product_id."""
    return uuid4()


# ═════════════════════════════════════════════════════════════════════════════
# 1. upsert creates new row
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_upsert_creates_new_row(session: AsyncSession):
    from app.services.supplier_product_service import upsert_supplier_product, get_supplier_products

    pid = _pid()
    sp  = await upsert_supplier_product(
        session,
        product_id=pid,
        supplier="STYLEKOREAN",
        supplier_product_id="sk-001",
        price=15.00,
        stock_status="IN_STOCK",
    )
    await session.commit()

    rows = await get_supplier_products(session, pid)
    assert len(rows) == 1
    assert rows[0].supplier == "STYLEKOREAN"
    assert float(rows[0].price) == pytest.approx(15.00)
    assert rows[0].stock_status == "IN_STOCK"


# ═════════════════════════════════════════════════════════════════════════════
# 2. upsert updates existing row (idempotent)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_upsert_updates_existing_row(session: AsyncSession):
    from app.services.supplier_product_service import upsert_supplier_product, get_supplier_products

    pid = _pid()
    await upsert_supplier_product(session, product_id=pid, supplier="JOLSE",
                                  supplier_product_id="j-001", price=20.00, stock_status="IN_STOCK")
    await session.commit()

    # Same product_id + supplier → update
    await upsert_supplier_product(session, product_id=pid, supplier="JOLSE",
                                  supplier_product_id="j-001", price=18.00, stock_status="OUT_OF_STOCK")
    await session.commit()

    rows = await get_supplier_products(session, pid)
    assert len(rows) == 1
    assert float(rows[0].price) == pytest.approx(18.00)
    assert rows[0].stock_status == "OUT_OF_STOCK"


# ═════════════════════════════════════════════════════════════════════════════
# 3. get_supplier_products ordered cheapest first
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_get_supplier_products_ordered(session: AsyncSession):
    from app.services.supplier_product_service import upsert_supplier_product, get_supplier_products

    pid = _pid()
    await upsert_supplier_product(session, product_id=pid, supplier="STYLEKOREAN",
                                  supplier_product_id="sk-001", price=25.00, stock_status="IN_STOCK")
    await upsert_supplier_product(session, product_id=pid, supplier="JOLSE",
                                  supplier_product_id="j-001", price=12.00, stock_status="IN_STOCK")
    await upsert_supplier_product(session, product_id=pid, supplier="OLIVEYOUNG",
                                  supplier_product_id="oy-001", price=18.00, stock_status="IN_STOCK")
    await session.commit()

    rows = await get_supplier_products(session, pid)
    prices = [float(r.price) for r in rows]
    assert prices == sorted(prices)  # ascending


# ═════════════════════════════════════════════════════════════════════════════
# 4. get_best_supplier – returns cheapest IN_STOCK
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_get_best_supplier_cheapest_in_stock(session: AsyncSession):
    from app.services.supplier_product_service import upsert_supplier_product, get_best_supplier

    pid = _pid()
    await upsert_supplier_product(session, product_id=pid, supplier="STYLEKOREAN",
                                  supplier_product_id="sk-001", price=25.00, stock_status="IN_STOCK")
    await upsert_supplier_product(session, product_id=pid, supplier="JOLSE",
                                  supplier_product_id="j-001", price=12.00, stock_status="IN_STOCK")
    await session.commit()

    best = await get_best_supplier(session, pid)
    assert best is not None
    assert best.supplier == "JOLSE"
    assert float(best.price) == pytest.approx(12.00)


# ═════════════════════════════════════════════════════════════════════════════
# 5. get_best_supplier – ignores OUT_OF_STOCK
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_get_best_supplier_ignores_oos(session: AsyncSession):
    from app.services.supplier_product_service import upsert_supplier_product, get_best_supplier

    pid = _pid()
    # Cheapest but OOS
    await upsert_supplier_product(session, product_id=pid, supplier="JOLSE",
                                  supplier_product_id="j-001", price=8.00, stock_status="OUT_OF_STOCK")
    # More expensive but IN_STOCK
    await upsert_supplier_product(session, product_id=pid, supplier="STYLEKOREAN",
                                  supplier_product_id="sk-001", price=15.00, stock_status="IN_STOCK")
    await session.commit()

    best = await get_best_supplier(session, pid)
    assert best is not None
    assert best.supplier == "STYLEKOREAN"
    assert float(best.price) == pytest.approx(15.00)


# ═════════════════════════════════════════════════════════════════════════════
# 6. get_best_supplier – returns None when all OOS
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_get_best_supplier_all_oos_returns_none(session: AsyncSession):
    from app.services.supplier_product_service import upsert_supplier_product, get_best_supplier

    pid = _pid()
    await upsert_supplier_product(session, product_id=pid, supplier="STYLEKOREAN",
                                  supplier_product_id="sk-001", price=15.00, stock_status="OUT_OF_STOCK")
    await upsert_supplier_product(session, product_id=pid, supplier="JOLSE",
                                  supplier_product_id="j-001", price=12.00, stock_status="OUT_OF_STOCK")
    await session.commit()

    best = await get_best_supplier(session, pid)
    assert best is None


# ═════════════════════════════════════════════════════════════════════════════
# 7. get_best_supplier – deterministic tie-breaker
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_get_best_supplier_tie_breaker(session: AsyncSession):
    """When prices are equal, supplier name alphabetical order breaks the tie."""
    from app.services.supplier_product_service import upsert_supplier_product, get_best_supplier

    pid = _pid()
    # All same price, all IN_STOCK
    await upsert_supplier_product(session, product_id=pid, supplier="STYLEKOREAN",
                                  supplier_product_id="sk-001", price=10.00, stock_status="IN_STOCK")
    await upsert_supplier_product(session, product_id=pid, supplier="JOLSE",
                                  supplier_product_id="j-001", price=10.00, stock_status="IN_STOCK")
    await upsert_supplier_product(session, product_id=pid, supplier="OLIVEYOUNG",
                                  supplier_product_id="oy-001", price=10.00, stock_status="IN_STOCK")
    await session.commit()

    best = await get_best_supplier(session, pid)
    assert best is not None
    # JOLSE has priority 1 (lowest = wins tie)
    assert best.supplier == "JOLSE"


# ═════════════════════════════════════════════════════════════════════════════
# 8. update_supplier_price – success
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_update_supplier_price_success(session: AsyncSession):
    from app.services.supplier_product_service import upsert_supplier_product, update_supplier_price, get_supplier_products

    pid = _pid()
    await upsert_supplier_product(session, product_id=pid, supplier="STYLEKOREAN",
                                  supplier_product_id="sk-001", price=20.00, stock_status="IN_STOCK")
    await session.commit()

    updated = await update_supplier_price(session, product_id=pid, supplier="STYLEKOREAN", new_price=17.50)
    await session.commit()

    assert updated is True
    rows = await get_supplier_products(session, pid)
    assert float(rows[0].price) == pytest.approx(17.50)


# ═════════════════════════════════════════════════════════════════════════════
# 9. update_supplier_price – row missing → False
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_update_supplier_price_missing(session: AsyncSession):
    from app.services.supplier_product_service import update_supplier_price

    result = await update_supplier_price(session, product_id=uuid4(), supplier="JOLSE", new_price=10.00)
    assert result is False


# ═════════════════════════════════════════════════════════════════════════════
# 10. update_supplier_stock – flips status
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_update_supplier_stock_flip(session: AsyncSession):
    from app.services.supplier_product_service import upsert_supplier_product, update_supplier_stock, get_supplier_products

    pid = _pid()
    await upsert_supplier_product(session, product_id=pid, supplier="OLIVEYOUNG",
                                  supplier_product_id="oy-001", price=14.00, stock_status="IN_STOCK")
    await session.commit()

    updated = await update_supplier_stock(session, product_id=pid, supplier="OLIVEYOUNG", in_stock=False)
    await session.commit()

    assert updated is True
    rows = await get_supplier_products(session, pid)
    assert rows[0].stock_status == "OUT_OF_STOCK"


# ═════════════════════════════════════════════════════════════════════════════
# 11. update_supplier_stock – missing → False
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_update_supplier_stock_missing(session: AsyncSession):
    from app.services.supplier_product_service import update_supplier_stock

    result = await update_supplier_stock(session, product_id=uuid4(), supplier="JOLSE", in_stock=True)
    assert result is False


# ═════════════════════════════════════════════════════════════════════════════
# 12. save_supplier_product backward-compat alias
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_save_supplier_product_alias(session: AsyncSession):
    from app.services.supplier_product_service import save_supplier_product, get_supplier_products

    pid = _pid()
    await save_supplier_product(
        session,
        product_id=pid,
        supplier="JOLSE",
        supplier_product_id="j-alias",
        price=9.99,
        stock_status="IN_STOCK",
    )
    await session.commit()

    rows = await get_supplier_products(session, pid)
    assert len(rows) == 1
    assert float(rows[0].price) == pytest.approx(9.99)
