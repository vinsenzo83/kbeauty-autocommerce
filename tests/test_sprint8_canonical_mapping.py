from __future__ import annotations

"""
tests/test_sprint8_canonical_mapping.py
────────────────────────────────────────
Sprint 8 – mock-only tests for the canonical mapping layer.

Coverage
--------
1.  make_canonical_sku – basic brand+name+size
2.  make_canonical_sku – name only (no brand)
3.  make_canonical_sku – slugifies special characters
4.  make_canonical_sku – fallback used when name is empty
5.  make_canonical_sku – raises ValueError when all empty
6.  get_or_create_canonical_from_product – creates new row
7.  get_or_create_canonical_from_product – returns existing row (no duplicate)
8.  get_or_create_canonical_from_product – links product.canonical_product_id
9.  get_or_create_canonical_from_product – reuses if already linked
10. attach_supplier_to_canonical – creates new SupplierProduct row
11. attach_supplier_to_canonical – updates existing row (idempotent)
12. Backfill: multiple products → unique canonical rows per slug
"""

from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Import models (use shared metadata trick for SQLite test DB)
from app.models.canonical_product import CanonicalProduct
from app.models.supplier_product import SupplierProduct
from app.services.canonical_service import (
    attach_supplier_to_canonical,
    get_or_create_canonical_from_product,
    make_canonical_sku,
)

# ── Shared SQLAlchemy metadata for all Sprint 8 models ───────────────────────
from sqlalchemy.orm import DeclarativeBase


class _TestBase(DeclarativeBase):
    pass


# Monkey-patch the models to use our test Base (re-create table metadata)
from sqlalchemy import Column, DateTime, Boolean, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID

# We'll register the models on the shared test metadata instead
from app.models.canonical_product import CanonicalProduct as _CP
from app.models.supplier_product import SupplierProduct as _SP

# Use each model's own Base metadata; create engine with all
import app.models.canonical_product as _cp_mod
import app.models.supplier_product as _sp_mod

# Combine metadata
from sqlalchemy import MetaData as _Meta

TEST_DB_URL  = "sqlite+aiosqlite:///:memory:"
test_engine  = create_async_engine(TEST_DB_URL, echo=False)


@pytest_asyncio.fixture(autouse=True)
async def create_tables():
    async with test_engine.begin() as conn:
        await conn.run_sync(_cp_mod.Base.metadata.create_all)
        await conn.run_sync(_sp_mod.Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(_sp_mod.Base.metadata.drop_all)
        await conn.run_sync(_cp_mod.Base.metadata.drop_all)


TestSession = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as s:
        yield s


# ─────────────────────────────────────────────────────────────────────────────
# make_canonical_sku tests
# ─────────────────────────────────────────────────────────────────────────────

def test_make_canonical_sku_full():
    sku = make_canonical_sku("Some Cream", brand="Laneige", size_ml=100)
    assert sku == "laneige-some-cream-100"


def test_make_canonical_sku_no_brand():
    sku = make_canonical_sku("My Toner")
    assert sku == "my-toner"


def test_make_canonical_sku_special_chars():
    sku = make_canonical_sku("Glow & Go (2x)!", brand="Brand™")
    # All non-alnum become '-', collapsed and stripped
    assert "brand" in sku
    assert "glow" in sku
    assert " " not in sku
    assert sku == sku.lower()


def test_make_canonical_sku_fallback():
    sku = make_canonical_sku("", fallback="sku-abc-123")
    assert sku == "sku-abc-123"


def test_make_canonical_sku_raises_when_all_empty():
    with pytest.raises(ValueError):
        make_canonical_sku("", brand=None, size_ml=None, fallback="")


# ─────────────────────────────────────────────────────────────────────────────
# get_or_create_canonical_from_product tests
# ─────────────────────────────────────────────────────────────────────────────

def _make_product_mock(name="Test Cream", brand="BrandX", supplier_product_id="SK-001"):
    p = MagicMock()
    p.name               = name
    p.brand              = brand
    p.size_ml            = None
    p.supplier_product_id = supplier_product_id
    p.image_urls_json    = None
    p.canonical_product_id = None
    return p


@pytest.mark.anyio
async def test_get_or_create_creates_new(session):
    product = _make_product_mock()
    cid = await get_or_create_canonical_from_product(product, session)
    assert cid is not None
    # Verify row was created
    from sqlalchemy import select
    result = await session.execute(select(CanonicalProduct).where(CanonicalProduct.id == cid))
    cp = result.scalar_one_or_none()
    assert cp is not None
    assert cp.name == "Test Cream"
    assert cp.brand == "BrandX"


@pytest.mark.anyio
async def test_get_or_create_no_duplicate(session):
    p1 = _make_product_mock()
    p2 = _make_product_mock()  # same name/brand
    cid1 = await get_or_create_canonical_from_product(p1, session)
    cid2 = await get_or_create_canonical_from_product(p2, session)
    assert cid1 == cid2


@pytest.mark.anyio
async def test_get_or_create_links_product(session):
    product = _make_product_mock()
    cid = await get_or_create_canonical_from_product(product, session)
    # The mock object's canonical_product_id should be set
    assert product.canonical_product_id == cid


@pytest.mark.anyio
async def test_get_or_create_reuses_if_already_linked(session):
    existing_cid = uuid4()
    product = _make_product_mock()
    product.canonical_product_id = existing_cid  # already linked
    returned_cid = await get_or_create_canonical_from_product(product, session)
    assert returned_cid == existing_cid


# ─────────────────────────────────────────────────────────────────────────────
# attach_supplier_to_canonical tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_attach_supplier_creates_row(session):
    # First create a canonical product
    product = _make_product_mock(name="Serum X", brand="BrandA", supplier_product_id="JL-111")
    cid = await get_or_create_canonical_from_product(product, session)

    sp = await attach_supplier_to_canonical(
        cid,
        supplier="JOLSE",
        supplier_product_id="JL-111",
        supplier_product_url="https://jolse.com/products/serum-x",
        session=session,
    )
    assert sp is not None
    assert sp.supplier == "JOLSE"
    assert sp.supplier_product_url == "https://jolse.com/products/serum-x"
    assert sp.canonical_product_id == cid


@pytest.mark.anyio
async def test_attach_supplier_idempotent(session):
    product = _make_product_mock(name="Toner Y", brand="BrandB", supplier_product_id="OY-222")
    cid = await get_or_create_canonical_from_product(product, session)

    # Attach twice
    sp1 = await attach_supplier_to_canonical(
        cid, "OLIVEYOUNG", "OY-222", "https://oliveyoung.co.kr/p/OY-222", session
    )
    sp2 = await attach_supplier_to_canonical(
        cid, "OLIVEYOUNG", "OY-222-v2", "https://oliveyoung.co.kr/p/OY-222-new", session
    )
    # Should update, not create new row
    assert sp1.id == sp2.id
    assert sp2.supplier_product_id == "OY-222-v2"
    assert sp2.supplier_product_url == "https://oliveyoung.co.kr/p/OY-222-new"


# ─────────────────────────────────────────────────────────────────────────────
# Backfill: multiple products produce unique canonical rows
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_backfill_multiple_products_unique_canonicals(session):
    from sqlalchemy import select

    products = [
        _make_product_mock("Cream A", "Brand1", "SK-001"),
        _make_product_mock("Cream B", "Brand2", "SK-002"),
        _make_product_mock("Cream C", "Brand3", "SK-003"),
    ]
    cids = []
    for p in products:
        cid = await get_or_create_canonical_from_product(p, session)
        cids.append(cid)

    # All unique
    assert len(set(str(c) for c in cids)) == 3

    # Count in DB
    result = await session.execute(select(CanonicalProduct))
    rows = result.scalars().all()
    assert len(rows) == 3
