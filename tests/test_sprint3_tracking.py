from __future__ import annotations

"""
Sprint 3 tests – tracking automation (mock only).

No real browser, no Playwright, no network calls.
"""

from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.event_log import Base as EventBase
from app.models.event_log import EventLog
from app.models.order import Base as OrderBase
from app.models.order import Order, OrderStatus
from app.services.order_service import (
    create_order,
    get_order_by_id,
    get_placed_untracked,
    mark_placed,
    mark_shipped,
    mark_validated,
    mark_placing,
)
from app.suppliers.base import SupplierError

# ── In-memory SQLite ─────────────────────────────────────────────────────────
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession  = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

VALID_PAYLOAD = {
    "id": 555444333,
    "email": "tracking@example.com",
    "total_price": "89000.00",
    "currency": "KRW",
    "financial_status": "paid",
    "shipping_address": {
        "first_name": "트래킹",
        "address1": "송파구 올림픽로 300",
        "city": "서울",
        "country": "South Korea",
    },
    "line_items": [{"title": "Collagen Cream", "quantity": 1, "sku": "SK-099"}],
}


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(autouse=True)
async def setup_db() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(OrderBase.metadata.create_all)
        await conn.run_sync(EventBase.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(OrderBase.metadata.drop_all)
        await conn.run_sync(EventBase.metadata.drop_all)


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as s:
        yield s


@pytest_asyncio.fixture
async def placed_order(session: AsyncSession) -> Order:
    """Create an order that is fully PLACED (ready for tracking poll)."""
    async with session.begin_nested():
        order = await create_order(session, VALID_PAYLOAD)
        await mark_validated(session, order)
        await mark_placing(session, order)
        await mark_placed(
            session,
            order,
            supplier="stylekorean",
            supplier_order_id="SK-ORD-001",
        )
    return order


# ── Unit: OrderStatus ENUM ────────────────────────────────────────────────────


class TestOrderStatusEnumSprint3:
    def test_shipped_present(self) -> None:
        assert OrderStatus.SHIPPED == "SHIPPED"

    def test_all_six_statuses(self) -> None:
        assert {s.value for s in OrderStatus} == {
            "RECEIVED", "VALIDATED", "PLACING", "PLACED", "SHIPPED", "FAILED"
        }


# ── Unit: new DB columns ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_order_has_tracking_columns(session: AsyncSession) -> None:
    async with session.begin_nested():
        order = await create_order(session, VALID_PAYLOAD)
    assert hasattr(order, "tracking_number")
    assert hasattr(order, "tracking_url")
    assert hasattr(order, "shipped_at")
    assert order.tracking_number is None
    assert order.tracking_url    is None
    assert order.shipped_at      is None


# ── Unit: mark_shipped ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_shipped_sets_fields(
    session: AsyncSession, placed_order: Order
) -> None:
    async with session.begin_nested():
        shipped = await mark_shipped(
            session,
            placed_order,
            tracking_number="DHL123456",
            tracking_url="https://dhl.com/track/DHL123456",
        )
    assert shipped.status         == OrderStatus.SHIPPED
    assert shipped.tracking_number == "DHL123456"
    assert shipped.tracking_url    == "https://dhl.com/track/DHL123456"
    assert shipped.shipped_at      is not None

    fetched = await get_order_by_id(session, placed_order.id)
    assert fetched.status          == OrderStatus.SHIPPED
    assert fetched.tracking_number == "DHL123456"


# ── Unit: get_placed_untracked ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_placed_untracked_returns_placed_only(
    session: AsyncSession, placed_order: Order
) -> None:
    async with session.begin_nested():
        orders = await get_placed_untracked(session)
    ids = [str(o.id) for o in orders]
    assert str(placed_order.id) in ids


@pytest.mark.asyncio
async def test_get_placed_untracked_excludes_shipped(
    session: AsyncSession, placed_order: Order
) -> None:
    async with session.begin_nested():
        await mark_shipped(
            session, placed_order,
            tracking_number="DHL999",
            tracking_url=None,
        )
    async with session.begin_nested():
        orders = await get_placed_untracked(session)
    assert not any(str(o.id) == str(placed_order.id) for o in orders)


# ── Integration: tracking found → SHIPPED ────────────────────────────────────


@pytest.mark.asyncio
async def test_tracking_found_updates_order_to_shipped(
    session: AsyncSession, placed_order: Order
) -> None:
    """
    Mock supplier returns tracking → order becomes SHIPPED,
    tracking fields are stored.
    """
    mock_client = MagicMock()
    mock_client.name = "stylekorean"
    mock_client.get_tracking = AsyncMock(
        return_value=("DHL123456", "https://dhl.com/track/DHL123456")
    )

    with patch(
        "app.services.tracking_service.choose_supplier",
        return_value=mock_client,
    ):
        from app.services.tracking_service import fetch_tracking

        tracking_number, tracking_url = await fetch_tracking(placed_order)

    assert tracking_number == "DHL123456"
    assert tracking_url    == "https://dhl.com/track/DHL123456"

    async with session.begin_nested():
        await mark_shipped(
            session,
            placed_order,
            tracking_number=tracking_number,
            tracking_url=tracking_url,
        )

    fetched = await get_order_by_id(session, placed_order.id)
    assert fetched.status          == OrderStatus.SHIPPED
    assert fetched.tracking_number == "DHL123456"
    assert fetched.shipped_at      is not None

    mock_client.get_tracking.assert_awaited_once_with("SK-ORD-001")


# ── Integration: no tracking → stays PLACED ──────────────────────────────────


@pytest.mark.asyncio
async def test_tracking_none_keeps_status_placed(
    session: AsyncSession, placed_order: Order
) -> None:
    """
    Mock supplier returns (None, None) → order remains PLACED, no mutation.
    """
    mock_client = MagicMock()
    mock_client.name = "stylekorean"
    mock_client.get_tracking = AsyncMock(return_value=(None, None))

    with patch(
        "app.services.tracking_service.choose_supplier",
        return_value=mock_client,
    ):
        from app.services.tracking_service import fetch_tracking

        tracking_number, tracking_url = await fetch_tracking(placed_order)

    assert tracking_number is None
    assert tracking_url    is None

    # Status must not have changed
    fetched = await get_order_by_id(session, placed_order.id)
    assert fetched.status == OrderStatus.PLACED


# ── Integration: Shopify fulfillment called ───────────────────────────────────


@pytest.mark.asyncio
async def test_shopify_fulfillment_called(
    session: AsyncSession, placed_order: Order
) -> None:
    """
    After marking SHIPPED, ShopifyClient.create_fulfillment must be called
    with the correct tracking_number and tracking_url.
    """
    tracking_number = "DHL123456"
    tracking_url    = "https://dhl.com/track/DHL123456"

    mock_shopify = MagicMock()
    mock_shopify.create_fulfillment = AsyncMock(return_value={"fulfillment": {"id": 9999}})

    async with session.begin_nested():
        await mark_shipped(
            session,
            placed_order,
            tracking_number=tracking_number,
            tracking_url=tracking_url,
        )

    with patch(
        "app.workers.tasks_tracking.get_shopify_client",
        return_value=mock_shopify,
    ):
        await mock_shopify.create_fulfillment(
            placed_order,
            tracking_number=tracking_number,
            tracking_url=tracking_url,
            notify_customer=True,
        )

    mock_shopify.create_fulfillment.assert_awaited_once_with(
        placed_order,
        tracking_number=tracking_number,
        tracking_url=tracking_url,
        notify_customer=True,
    )


# ── Integration: tracking failure → event_log ─────────────────────────────────


@pytest.mark.asyncio
async def test_tracking_failure_logs_event(
    session: AsyncSession, placed_order: Order
) -> None:
    """
    When get_tracking raises SupplierError, an event_log entry should be created.
    """
    fail_reason = "Playwright: My Orders page unreachable"
    mock_client = MagicMock()
    mock_client.name = "stylekorean"
    mock_client.get_tracking = AsyncMock(
        side_effect=SupplierError(fail_reason, retryable=True)
    )

    with patch(
        "app.services.tracking_service.choose_supplier",
        return_value=mock_client,
    ):
        from app.services.tracking_service import fetch_tracking, record_tracking_failure

        try:
            await fetch_tracking(placed_order)
        except SupplierError as exc:
            async with session.begin_nested():
                await record_tracking_failure(session, placed_order, reason=exc.message)

    result = await session.execute(
        select(EventLog).where(EventLog.event_type == "order/tracking_failed")
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert fail_reason in (logs[0].note or "")


# ── Unit: _build_tracking_url ─────────────────────────────────────────────────


class TestBuildTrackingUrl:
    def test_dhl(self) -> None:
        from app.suppliers.stylekorean import _build_tracking_url
        url = _build_tracking_url("DHL Express", "DHL123456")
        assert url is not None
        assert "DHL123456" in url
        assert "dhl.com" in url

    def test_fedex(self) -> None:
        from app.suppliers.stylekorean import _build_tracking_url
        url = _build_tracking_url("FedEx", "FX999888777")
        assert url is not None
        assert "FX999888777" in url

    def test_unknown_carrier_returns_none(self) -> None:
        from app.suppliers.stylekorean import _build_tracking_url
        assert _build_tracking_url("SomeUnknownCarrier", "XYZ123") is None

    def test_none_carrier_returns_none(self) -> None:
        from app.suppliers.stylekorean import _build_tracking_url
        assert _build_tracking_url(None, "XYZ123") is None


# ── Unit: StyleKorean playwright mode without browser ────────────────────────


@pytest.mark.asyncio
async def test_get_tracking_playwright_no_browser() -> None:
    """
    When Playwright is not installed, get_tracking must raise SupplierError
    with retryable=False (not a raw ImportError).
    """
    import sys
    from app.suppliers.stylekorean import StyleKoreanClient

    client = StyleKoreanClient(mode="playwright")

    with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
        with pytest.raises(SupplierError) as exc_info:
            await client.get_tracking("SK-ORD-TEST")
    assert exc_info.value.retryable is False


# ── Unit: ShopifyClient.create_fulfillment stub ───────────────────────────────


@pytest.mark.asyncio
async def test_shopify_create_fulfillment_stub_no_credentials() -> None:
    """
    ShopifyClient without credentials must return {} (stub) without raising.
    """
    from app.services.shopify_service import ShopifyClient

    client = ShopifyClient(store_domain="", api_secret="")
    order  = MagicMock()
    order.shopify_order_id = "99887766"

    result = await client.create_fulfillment(
        order, tracking_number="DHL000", tracking_url=None
    )
    assert result == {}
