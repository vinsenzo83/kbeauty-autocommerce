from __future__ import annotations

"""
Sprint 2 tests – supplier placement pipeline (mock only).

No real browser / network calls are made.
playwright is NOT required to run these tests.
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
    mark_placing,
    mark_placed,
    mark_validated,
    mark_failed,
)
from app.suppliers.base import SupplierClient, SupplierError

# ── In-memory SQLite ─────────────────────────────────────────────────────────
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

VALID_PAYLOAD = {
    "id": 777666555,
    "email": "sprint2@example.com",
    "total_price": "69000.00",
    "currency": "KRW",
    "financial_status": "paid",
    "shipping_address": {
        "first_name": "하나",
        "address1": "강남구 도산대로 99",
        "city": "서울",
        "country": "South Korea",
    },
    "line_items": [
        {
            "title": "Snail Cream 50ml",
            "quantity": 1,
            "price": "69000.00",
            "sku": "SK-001",
        }
    ],
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
async def validated_order(session: AsyncSession) -> Order:
    async with session.begin_nested():
        order = await create_order(session, VALID_PAYLOAD)
        await mark_validated(session, order)
    return order


# ── Helper: build a mock SupplierClient ──────────────────────────────────────


def _mock_supplier(supplier_order_id: str = "SK_TEST_123") -> SupplierClient:
    client = MagicMock(spec=SupplierClient)
    client.name = "stylekorean"
    client.create_order = AsyncMock(return_value=supplier_order_id)
    client.get_tracking = AsyncMock(return_value=(None, None))
    return client


def _failing_supplier(reason: str = "Playwright timeout") -> SupplierClient:
    client = MagicMock(spec=SupplierClient)
    client.name = "stylekorean"
    client.create_order = AsyncMock(side_effect=SupplierError(reason, retryable=False))
    client.get_tracking = AsyncMock(return_value=(None, None))
    return client


# ── Unit: OrderStatus ENUM ────────────────────────────────────────────────────


class TestOrderStatusEnum:
    def test_all_statuses_present(self) -> None:
        statuses = {s.value for s in OrderStatus}
        # Sprint 3 added SHIPPED – assert all six are present
        assert {"RECEIVED", "VALIDATED", "PLACING", "PLACED", "FAILED"}.issubset(statuses)

    def test_status_is_string(self) -> None:
        assert isinstance(OrderStatus.PLACING, str)
        assert OrderStatus.PLACING == "PLACING"


# ── Unit: new DB columns present ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_order_has_supplier_columns(session: AsyncSession) -> None:
    async with session.begin_nested():
        order = await create_order(session, VALID_PAYLOAD)
    assert hasattr(order, "supplier")
    assert hasattr(order, "supplier_order_id")
    assert hasattr(order, "placed_at")
    assert order.supplier is None
    assert order.supplier_order_id is None
    assert order.placed_at is None


# ── Unit: mark_placing transition ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transition_validated_to_placing(
    session: AsyncSession, validated_order: Order
) -> None:
    async with session.begin_nested():
        placing = await mark_placing(session, validated_order)
    assert placing.status == OrderStatus.PLACING
    fetched = await get_order_by_id(session, validated_order.id)
    assert fetched.status == OrderStatus.PLACING


# ── Unit: mark_placed transition ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transition_placing_to_placed(
    session: AsyncSession, validated_order: Order
) -> None:
    async with session.begin_nested():
        await mark_placing(session, validated_order)
    async with session.begin_nested():
        placed = await mark_placed(
            session,
            validated_order,
            supplier="stylekorean",
            supplier_order_id="SK_TEST_123",
        )
    assert placed.status           == OrderStatus.PLACED
    assert placed.supplier         == "stylekorean"
    assert placed.supplier_order_id == "SK_TEST_123"
    assert placed.placed_at is not None

    fetched = await get_order_by_id(session, validated_order.id)
    assert fetched.status           == OrderStatus.PLACED
    assert fetched.supplier_order_id == "SK_TEST_123"


# ── Integration: VALIDATED → PLACING → PLACED via mock supplier ──────────────


@pytest.mark.asyncio
async def test_full_placement_happy_path(
    session: AsyncSession, validated_order: Order
) -> None:
    """
    Simulate the worker pipeline with a mocked SupplierClient.
    Asserts: VALIDATED → PLACING → PLACED, supplier_order_id stored.
    """
    mock_client = _mock_supplier("SK_TEST_123")

    with patch("app.services.supplier_router.choose_supplier", return_value=mock_client):
        from app.services.supplier_router import choose_supplier

        async with session.begin_nested():
            await mark_placing(session, validated_order)

        client = choose_supplier(validated_order)
        supplier_order_id = await client.create_order(validated_order)

        async with session.begin_nested():
            await mark_placed(
                session,
                validated_order,
                supplier=client.name,
                supplier_order_id=supplier_order_id,
            )

    fetched = await get_order_by_id(session, validated_order.id)
    assert fetched.status            == OrderStatus.PLACED
    assert fetched.supplier          == "stylekorean"
    assert fetched.supplier_order_id == "SK_TEST_123"
    mock_client.create_order.assert_awaited_once_with(validated_order)


# ── Integration: supplier failure → FAILED + event_log ───────────────────────


@pytest.mark.asyncio
async def test_supplier_failure_marks_failed_and_logs(
    session: AsyncSession, validated_order: Order
) -> None:
    """
    When SupplierClient.create_order raises SupplierError,
    order must transition to FAILED and event_log must contain the reason.
    """
    fail_reason = "Playwright timeout on confirmation page"
    mock_client = _failing_supplier(fail_reason)

    with patch("app.services.supplier_router.choose_supplier", return_value=mock_client):
        from app.services.supplier_router import choose_supplier

        async with session.begin_nested():
            await mark_placing(session, validated_order)

        client = choose_supplier(validated_order)

        try:
            await client.create_order(validated_order)
            pytest.fail("Expected SupplierError was not raised")
        except SupplierError as exc:
            async with session.begin_nested():
                await mark_failed(session, validated_order, reason=exc.message)
                session.add(EventLog(
                    event_hash=f"supplier_fail:{validated_order.id}:test",
                    source="worker",
                    event_type="order/supplier_failed",
                    payload_ref=validated_order.shopify_order_id,
                    note=exc.message,
                ))

    fetched = await get_order_by_id(session, validated_order.id)
    assert fetched.status     == OrderStatus.FAILED
    assert fail_reason in (fetched.fail_reason or "")

    result = await session.execute(
        select(EventLog).where(EventLog.event_type == "order/supplier_failed")
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert fail_reason in (logs[0].note or "")


# ── SupplierClient abstract interface ────────────────────────────────────────


class TestSupplierClientInterface:
    def test_cannot_instantiate_abstract_base(self) -> None:
        with pytest.raises(TypeError):
            SupplierClient()  # type: ignore[abstract]

    def test_supplier_error_stores_message_and_retryable(self) -> None:
        err = SupplierError("boom", retryable=False)
        assert err.message   == "boom"
        assert err.retryable is False

    def test_supplier_error_default_retryable_true(self) -> None:
        err = SupplierError("transient")
        assert err.retryable is True


# ── StyleKorean client: mode validation ──────────────────────────────────────


class TestStyleKoreanClientInit:
    def test_invalid_mode_raises(self) -> None:
        from app.suppliers.stylekorean import StyleKoreanClient
        with pytest.raises(ValueError, match="Unknown mode"):
            StyleKoreanClient(mode="ftp")

    def test_api_mode_create_order_raises_not_implemented(self) -> None:
        from app.suppliers.stylekorean import StyleKoreanClient
        import asyncio
        client = StyleKoreanClient(mode="api")
        with pytest.raises(NotImplementedError):
            asyncio.get_event_loop().run_until_complete(
                client.create_order(MagicMock())
            )

    def test_playwright_mode_raises_supplier_error_without_playwright(self) -> None:
        """
        When playwright package is not importable, create_order must raise
        SupplierError (not ImportError) with retryable=False.
        """
        import asyncio
        from app.suppliers.stylekorean import StyleKoreanClient
        client = StyleKoreanClient(mode="playwright")
        order  = MagicMock()
        order.id            = uuid4()
        order.line_items_json = []

        with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
            with pytest.raises(SupplierError) as exc_info:
                asyncio.get_event_loop().run_until_complete(client.create_order(order))
        assert exc_info.value.retryable is False
