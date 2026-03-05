"""
tests/test_sprint9_inventory_sync.py
──────────────────────────────────────
Sprint 9 – Mock-only tests for inventory sync channel tasks.

Covers:
  * _run_sync_inventory_channels() sends quantity=99 when IN_STOCK
  * Sends quantity=0 when all suppliers are OUT_OF_STOCK
  * Sends quantity=0 when no supplier rows exist
  * Skips channel_products with no external_variant_id
  * Result dict has correct keys

Also covers:
  * _run_import_channel_orders() fetches and stores orders
  * Channels returning empty list are handled
  * Result dict keys for import

No real DB or network.  Uses in-memory SQLite + mock channel clients.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_engine():
    from app.models.canonical_product import Base as CpBase
    from app.models.sales_channel import Base as ScBase
    from app.models.supplier_product import Base as SpBase

    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(CpBase.metadata.create_all)
        await conn.run_sync(ScBase.metadata.create_all)
        await conn.run_sync(SpBase.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


def _mock_clients(update_inventory_ok: bool = True):
    def _make(slug):
        c = MagicMock()
        c.channel_name = slug
        c.update_inventory = AsyncMock(return_value=update_inventory_ok)
        c.fetch_orders     = AsyncMock(return_value=[])
        return c

    return {
        "shopify":     _make("shopify"),
        "shopee":      _make("shopee"),
        "tiktok_shop": _make("tiktok_shop"),
    }


async def _seed_channel_product(factory, canonical_id, channel: str, vid: str = "test-vid-001"):
    from app.models.sales_channel import ChannelProduct

    async with factory() as session:
        row = ChannelProduct(
            canonical_product_id = canonical_id,
            channel              = channel,
            external_product_id  = "test-pid",
            external_variant_id  = vid,
            price                = Decimal("19.99"),
            status               = "active",
        )
        session.add(row)
        await session.commit()


async def _seed_supplier_product(factory, canonical_id, stock_status: str = "IN_STOCK"):
    from app.models.supplier_product import SupplierProduct

    async with factory() as session:
        row = SupplierProduct(
            id                   = uuid.uuid4(),
            canonical_product_id = canonical_id,
            supplier             = "STYLEKOREAN",
            supplier_product_id  = f"sk-{uuid.uuid4().hex[:8]}",
            stock_status         = stock_status,
        )
        session.add(row)
        await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Inventory Sync Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_sync_inventory_in_stock_sends_99(session_factory):
    from app.workers.tasks_channels import _run_sync_inventory_channels

    cid = uuid.uuid4()
    await _seed_channel_product(session_factory, cid, "shopify", "shopify-vid-001")
    await _seed_supplier_product(session_factory, cid, "IN_STOCK")

    clients = _mock_clients()
    result  = await _run_sync_inventory_channels(session_factory=session_factory, clients=clients)

    assert result["total_channel_products"] == 1
    assert result["updated"] == 1
    assert result["errors"] == 0

    clients["shopify"].update_inventory.assert_called_once_with("shopify-vid-001", 99)


@pytest.mark.anyio
async def test_sync_inventory_out_of_stock_sends_zero(session_factory):
    from app.workers.tasks_channels import _run_sync_inventory_channels

    cid = uuid.uuid4()
    await _seed_channel_product(session_factory, cid, "shopee", "shopee-vid-001")
    await _seed_supplier_product(session_factory, cid, "OUT_OF_STOCK")

    clients = _mock_clients()
    await _run_sync_inventory_channels(session_factory=session_factory, clients=clients)

    clients["shopee"].update_inventory.assert_called_once_with("shopee-vid-001", 0)


@pytest.mark.anyio
async def test_sync_inventory_no_supplier_sends_zero(session_factory):
    """When no supplier row exists, treat as out of stock."""
    from app.workers.tasks_channels import _run_sync_inventory_channels

    cid = uuid.uuid4()
    await _seed_channel_product(session_factory, cid, "tiktok_shop", "tt-vid-001")
    # No supplier_product row seeded

    clients = _mock_clients()
    await _run_sync_inventory_channels(session_factory=session_factory, clients=clients)

    clients["tiktok_shop"].update_inventory.assert_called_once_with("tt-vid-001", 0)


@pytest.mark.anyio
async def test_sync_inventory_skips_no_variant_id(session_factory):
    from app.workers.tasks_channels import _run_sync_inventory_channels

    cid = uuid.uuid4()
    await _seed_channel_product(session_factory, cid, "shopify", vid=None)
    await _seed_supplier_product(session_factory, cid, "IN_STOCK")

    clients = _mock_clients()
    result  = await _run_sync_inventory_channels(session_factory=session_factory, clients=clients)

    assert result["updated"] == 0
    clients["shopify"].update_inventory.assert_not_called()


@pytest.mark.anyio
async def test_sync_inventory_result_keys(session_factory):
    from app.workers.tasks_channels import _run_sync_inventory_channels

    result = await _run_sync_inventory_channels(session_factory=session_factory, clients=_mock_clients())
    assert "total_channel_products" in result
    assert "updated"                in result
    assert "errors"                 in result


@pytest.mark.anyio
async def test_sync_inventory_empty_db(session_factory):
    from app.workers.tasks_channels import _run_sync_inventory_channels

    result = await _run_sync_inventory_channels(session_factory=session_factory, clients=_mock_clients())
    assert result["total_channel_products"] == 0
    assert result["updated"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Import Channel Orders Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_import_orders_empty_returns_zero(session_factory):
    from app.workers.tasks_channels import _run_import_channel_orders

    clients = _mock_clients()  # all fetch_orders return []
    result = await _run_import_channel_orders(
        clients=clients,
        session_factory=session_factory,
    )
    assert result["total_fetched"]  == 0
    assert result["total_upserted"] == 0
    assert result["errors"]         == 0


@pytest.mark.anyio
async def test_import_orders_fetches_from_all_channels(session_factory):
    from app.workers.tasks_channels import _run_import_channel_orders

    clients = _mock_clients()
    # shopify returns 2 orders
    clients["shopify"].fetch_orders = AsyncMock(
        return_value=[
            {
                "external_order_id":   "ord-001",
                "external_product_id": "pid-001",
                "external_variant_id": "vid-001",
                "quantity":            2,
                "price":               19.99,
                "currency":            "USD",
                "status":              "pending",
            },
            {
                "external_order_id":   "ord-002",
                "external_product_id": "pid-001",
                "external_variant_id": "vid-001",
                "quantity":            1,
                "price":               19.99,
                "currency":            "USD",
                "status":              "pending",
            },
        ]
    )

    result = await _run_import_channel_orders(
        clients=clients,
        session_factory=session_factory,
    )

    assert result["total_fetched"] == 2
    assert result["errors"] == 0


@pytest.mark.anyio
async def test_import_orders_result_keys(session_factory):
    from app.workers.tasks_channels import _run_import_channel_orders

    result = await _run_import_channel_orders(
        clients=_mock_clients(),
        session_factory=session_factory,
    )
    assert "total_fetched"  in result
    assert "total_upserted" in result
    assert "errors"         in result


@pytest.mark.anyio
async def test_import_orders_channel_error_increments_errors(session_factory):
    from app.workers.tasks_channels import _run_import_channel_orders

    clients = _mock_clients()
    clients["shopify"].fetch_orders = AsyncMock(side_effect=RuntimeError("network error"))

    result = await _run_import_channel_orders(
        clients=clients,
        session_factory=session_factory,
    )

    assert result["errors"] == 1
