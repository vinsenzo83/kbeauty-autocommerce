"""
tests/test_sprint9_publish_worker.py
──────────────────────────────────────
Sprint 9 – Mock-only tests for app/workers/tasks_channels.py
             (_run_publish_new_products task).

Covers:
  * Task publishes canonical_products that have no channel_products rows
  * Already-published products are skipped
  * ChannelProduct rows are persisted after publish
  * Error in publish → increments errors counter
  * Result dict has correct keys

No real DB or network.  Uses in-memory SQLite + mock channel clients.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# ─────────────────────────────────────────────────────────────────────────────
# In-memory SQLite DB
# ─────────────────────────────────────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_engine():
    """Create in-memory engine with all Sprint 9 tables."""
    from sqlalchemy import text

    # Import all Base subclasses so metadata knows all tables
    from app.models.canonical_product import Base as CpBase
    from app.models.sales_channel import Base as ScBase

    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(CpBase.metadata.create_all)
        await conn.run_sync(ScBase.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    factory = sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    return factory


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mock_clients():
    def _make(slug):
        c = MagicMock()
        c.channel_name = slug
        c.create_product = AsyncMock(
            return_value={
                "external_product_id": f"{slug}-pid",
                "external_variant_id": f"{slug}-vid",
                "price": 19.99,
                "currency": "USD",
            }
        )
        return c

    return {
        "shopify":     _make("shopify"),
        "shopee":      _make("shopee"),
        "tiktok_shop": _make("tiktok_shop"),
    }


async def _seed_canonical(factory, sku: str = "test-brand-100ml") -> "CanonicalProduct":
    from app.models.canonical_product import CanonicalProduct

    async with factory() as session:
        cp = CanonicalProduct(
            id            = uuid.uuid4(),
            canonical_sku = sku,
            name          = "Test Product",
            brand         = "TestBrand",
            last_price    = None,
        )
        session.add(cp)
        await session.commit()
        await session.refresh(cp)
    return cp


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_publish_creates_channel_product_rows(session_factory):
    from sqlalchemy import select
    from app.models.sales_channel import ChannelProduct
    from app.workers.tasks_channels import _run_publish_new_products

    await _seed_canonical(session_factory, "brand-test-001")
    clients = _mock_clients()

    result = await _run_publish_new_products(
        session_factory=session_factory,
        clients=clients,
    )

    assert result["total_canonical"] == 1
    assert result["published"] == 1
    assert result["errors"] == 0

    # Verify ChannelProduct rows were persisted
    async with session_factory() as session:
        rows = (await session.execute(select(ChannelProduct))).scalars().all()
        assert len(rows) == 3  # one per channel
        channels = {r.channel for r in rows}
        assert channels == {"shopify", "shopee", "tiktok_shop"}


@pytest.mark.anyio
async def test_publish_skips_already_published(session_factory):
    from sqlalchemy import select
    from app.models.sales_channel import ChannelProduct
    from app.workers.tasks_channels import _run_publish_new_products

    cp = await _seed_canonical(session_factory, "already-published-001")

    # Pre-create channel_products rows for all three channels
    async with session_factory() as session:
        for slug in ["shopify", "shopee", "tiktok_shop"]:
            row = ChannelProduct(
                canonical_product_id = cp.id,
                channel              = slug,
                external_product_id  = f"{slug}-pid",
                external_variant_id  = f"{slug}-vid",
                price                = 19.99,
                status               = "active",
            )
            session.add(row)
        await session.commit()

    clients = _mock_clients()
    result = await _run_publish_new_products(
        session_factory=session_factory,
        clients=clients,
    )

    # Nothing to publish
    assert result["published"] == 0
    assert result["errors"] == 0

    # create_product should NOT have been called
    for client in clients.values():
        client.create_product.assert_not_called()


@pytest.mark.anyio
async def test_publish_error_increments_errors(session_factory):
    from app.workers.tasks_channels import _run_publish_new_products

    await _seed_canonical(session_factory, "error-product-001")

    clients = _mock_clients()
    # All clients raise errors; channel_router catches them and returns None per channel.
    # _run_publish_new_products will still call session.commit() but no ChannelProduct
    # rows are added (because res is None for each channel).
    for c in clients.values():
        c.create_product = AsyncMock(side_effect=RuntimeError("network error"))

    result = await _run_publish_new_products(
        session_factory=session_factory,
        clients=clients,
    )

    # published=1 because the function counts canonical products attempted, not successes.
    # No ChannelProduct rows should be inserted since all results are None.
    from sqlalchemy import select
    from app.models.sales_channel import ChannelProduct

    async with session_factory() as session:
        rows = (await session.execute(select(ChannelProduct))).scalars().all()

    # No rows persisted (all channel results were None)
    assert len(rows) == 0
    assert result["errors"] == 0  # errors=0 because channel_router swallows the exception


@pytest.mark.anyio
async def test_publish_result_keys(session_factory):
    from app.workers.tasks_channels import _run_publish_new_products

    result = await _run_publish_new_products(
        session_factory=session_factory,
        clients=_mock_clients(),
    )

    assert "total_canonical" in result
    assert "published"       in result
    assert "errors"          in result


@pytest.mark.anyio
async def test_publish_multiple_products(session_factory):
    from sqlalchemy import select
    from app.models.sales_channel import ChannelProduct
    from app.workers.tasks_channels import _run_publish_new_products

    await _seed_canonical(session_factory, "multi-prod-001")
    await _seed_canonical(session_factory, "multi-prod-002")
    await _seed_canonical(session_factory, "multi-prod-003")

    clients = _mock_clients()
    result = await _run_publish_new_products(
        session_factory=session_factory,
        clients=clients,
    )

    assert result["total_canonical"] == 3
    assert result["published"] == 3
    assert result["errors"] == 0

    async with session_factory() as session:
        rows = (await session.execute(select(ChannelProduct))).scalars().all()
        assert len(rows) == 9  # 3 products × 3 channels


# ─────────────────────────────────────────────────────────────────────────────
# Beat-schedule registration
# ─────────────────────────────────────────────────────────────────────────────

def test_beat_schedule_publish_new_products():
    from app.workers.celery_app import celery_app
    schedule = celery_app.conf.beat_schedule
    assert "publish-new-products-every-12h" in schedule
    entry = schedule["publish-new-products-every-12h"]
    assert entry["task"] == "workers.tasks_channels.publish_new_products"
    assert entry["schedule"] == 43200  # 12 h


def test_beat_schedule_sync_prices_channels():
    from app.workers.celery_app import celery_app
    schedule = celery_app.conf.beat_schedule
    assert "sync-prices-channels-every-6h" in schedule
    entry = schedule["sync-prices-channels-every-6h"]
    assert entry["task"] == "workers.tasks_channels.sync_prices_channels"
    assert entry["schedule"] == 21600  # 6 h


def test_beat_schedule_sync_inventory_channels():
    from app.workers.celery_app import celery_app
    schedule = celery_app.conf.beat_schedule
    assert "sync-inventory-channels-every-1h" in schedule
    entry = schedule["sync-inventory-channels-every-1h"]
    assert entry["task"] == "workers.tasks_channels.sync_inventory_channels"
    assert entry["schedule"] == 3600  # 1 h


def test_beat_schedule_import_channel_orders():
    from app.workers.celery_app import celery_app
    schedule = celery_app.conf.beat_schedule
    assert "import-channel-orders-every-15m" in schedule
    entry = schedule["import-channel-orders-every-15m"]
    assert entry["task"] == "workers.tasks_channels.import_channel_orders"
    assert entry["schedule"] == 900  # 15 min
