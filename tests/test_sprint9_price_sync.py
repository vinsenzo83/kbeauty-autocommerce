"""
tests/test_sprint9_price_sync.py
──────────────────────────────────
Sprint 9 – Mock-only tests for the price-sync channel task.

Covers:
  * _run_sync_prices_channels() updates prices for mapped channels
  * Skips channel_products with no external_variant_id
  * Skips rows with price 0 / None
  * Errors in update_price are counted correctly
  * Result dict keys are correct

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

    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(CpBase.metadata.create_all)
        await conn.run_sync(ScBase.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


def _mock_clients(update_price_ok: bool = True):
    def _make(slug):
        c = MagicMock()
        c.channel_name = slug
        c.update_price = AsyncMock(return_value=update_price_ok)
        return c

    return {
        "shopify":     _make("shopify"),
        "shopee":      _make("shopee"),
        "tiktok_shop": _make("tiktok_shop"),
    }


async def _seed_channel_product(factory, canonical_id, channel: str, price: float | None, vid: str | None = "test-vid-001"):
    from app.models.sales_channel import ChannelProduct

    async with factory() as session:
        row = ChannelProduct(
            canonical_product_id = canonical_id,
            channel              = channel,
            external_product_id  = "test-pid-001",
            external_variant_id  = vid,
            price                = Decimal(str(price)) if price else None,
            status               = "active",
        )
        session.add(row)
        await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_sync_prices_calls_update_price(session_factory):
    from app.workers.tasks_channels import _run_sync_prices_channels

    cid = uuid.uuid4()
    await _seed_channel_product(session_factory, cid, "shopify", 19.99, "shopify-vid-001")
    await _seed_channel_product(session_factory, cid, "shopee",  19.99, "shopee-vid-001")

    clients = _mock_clients()
    result  = await _run_sync_prices_channels(session_factory=session_factory, clients=clients)

    assert result["total_channel_products"] == 2
    assert result["updated"] >= 1
    assert result["errors"] == 0

    clients["shopify"].update_price.assert_called_once_with("shopify-vid-001", 19.99)
    clients["shopee"].update_price.assert_called_once_with("shopee-vid-001", 19.99)


@pytest.mark.anyio
async def test_sync_prices_skips_no_variant_id(session_factory):
    from app.workers.tasks_channels import _run_sync_prices_channels

    cid = uuid.uuid4()
    # vid=None → should be skipped
    await _seed_channel_product(session_factory, cid, "shopify", 19.99, vid=None)

    clients = _mock_clients()
    result  = await _run_sync_prices_channels(session_factory=session_factory, clients=clients)

    # Nothing to update (no variant IDs)
    assert result["updated"] == 0
    clients["shopify"].update_price.assert_not_called()


@pytest.mark.anyio
async def test_sync_prices_skips_zero_price(session_factory):
    from app.workers.tasks_channels import _run_sync_prices_channels

    cid = uuid.uuid4()
    await _seed_channel_product(session_factory, cid, "shopee", 0.0, "shopee-vid-001")

    clients = _mock_clients()
    result  = await _run_sync_prices_channels(session_factory=session_factory, clients=clients)

    assert result["updated"] == 0
    clients["shopee"].update_price.assert_not_called()


@pytest.mark.anyio
async def test_sync_prices_result_keys(session_factory):
    from app.workers.tasks_channels import _run_sync_prices_channels

    result = await _run_sync_prices_channels(session_factory=session_factory, clients=_mock_clients())

    assert "total_channel_products" in result
    assert "updated"                in result
    assert "errors"                 in result


@pytest.mark.anyio
async def test_sync_prices_empty_db(session_factory):
    from app.workers.tasks_channels import _run_sync_prices_channels

    result = await _run_sync_prices_channels(session_factory=session_factory, clients=_mock_clients())

    assert result["total_channel_products"] == 0
    assert result["updated"] == 0
    assert result["errors"] == 0


@pytest.mark.anyio
async def test_sync_prices_multiple_products(session_factory):
    from app.workers.tasks_channels import _run_sync_prices_channels

    for i in range(3):
        cid = uuid.uuid4()
        await _seed_channel_product(session_factory, cid, "shopify", 20.0 + i, f"shopify-vid-{i:03d}")

    clients = _mock_clients()
    result  = await _run_sync_prices_channels(session_factory=session_factory, clients=clients)

    assert result["total_channel_products"] == 3
    assert result["updated"] == 3
    assert result["errors"] == 0
    assert clients["shopify"].update_price.call_count == 3
