from __future__ import annotations

from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.event_log import Base as EventBase
from app.models.order import Base as OrderBase
from app.models.order import Order, OrderStatus
from app.services.order_service import (
    create_order,
    get_order_by_id,
    mark_failed,
    mark_validated,
)
from app.services.policy_service import PolicyViolation, validate_order_policy

# ── In-memory SQLite ─────────────────────────────────────────────────────────
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

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


# ── Helper ────────────────────────────────────────────────────────────────────

VALID_PAYLOAD = {
    "id": 999888777,
    "email": "jane@example.com",
    "total_price": "79000.00",
    "currency": "KRW",
    "financial_status": "paid",
    "shipping_address": {
        "first_name": "민지",
        "address1": "마포구 월드컵로 100",
        "city": "서울",
    },
    "line_items": [{"title": "Hyaluronic Toner", "quantity": 1}],
}


async def _create(session: AsyncSession, overrides: dict | None = None) -> Order:
    payload = {**VALID_PAYLOAD, **(overrides or {})}
    async with session.begin_nested():
        order = await create_order(session, payload)
    return order


# ── Policy unit tests ─────────────────────────────────────────────────────────


class TestValidateOrderPolicy:
    def test_valid_order_passes(self) -> None:
        validate_order_policy(VALID_PAYLOAD)  # no exception

    def test_unpaid_raises(self) -> None:
        data = {**VALID_PAYLOAD, "financial_status": "pending"}
        with pytest.raises(PolicyViolation) as exc_info:
            validate_order_policy(data)
        assert "paid" in exc_info.value.reason

    def test_missing_shipping_raises(self) -> None:
        data = {**VALID_PAYLOAD, "shipping_address": None}
        with pytest.raises(PolicyViolation) as exc_info:
            validate_order_policy(data)
        assert "shipping_address" in exc_info.value.reason

    def test_empty_shipping_raises(self) -> None:
        data = {**VALID_PAYLOAD, "shipping_address": {}}
        # empty dict is falsy → should raise
        with pytest.raises(PolicyViolation):
            validate_order_policy(data)

    def test_refunded_status_raises(self) -> None:
        data = {**VALID_PAYLOAD, "financial_status": "refunded"}
        with pytest.raises(PolicyViolation):
            validate_order_policy(data)


# ── State machine tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initial_status_is_received(session: AsyncSession) -> None:
    order = await _create(session)
    fetched = await get_order_by_id(session, order.id)
    assert fetched is not None
    assert fetched.status == OrderStatus.RECEIVED


@pytest.mark.asyncio
async def test_transition_received_to_validated(session: AsyncSession) -> None:
    order = await _create(session)
    assert order.status == OrderStatus.RECEIVED

    async with session.begin_nested():
        validated = await mark_validated(session, order)

    assert validated.status == OrderStatus.VALIDATED
    fetched = await get_order_by_id(session, order.id)
    assert fetched.status == OrderStatus.VALIDATED


@pytest.mark.asyncio
async def test_transition_received_to_failed(session: AsyncSession) -> None:
    order = await _create(session)
    reason = "financial_status is 'pending', expected 'paid'"

    async with session.begin_nested():
        failed = await mark_failed(session, order, reason=reason)

    assert failed.status == OrderStatus.FAILED
    assert failed.fail_reason == reason

    fetched = await get_order_by_id(session, order.id)
    assert fetched.status == OrderStatus.FAILED
    assert fetched.fail_reason == reason


@pytest.mark.asyncio
async def test_full_flow_validated(session: AsyncSession) -> None:
    """Simulate the full happy-path worker logic."""
    order = await _create(session)
    assert order.status == OrderStatus.RECEIVED

    order_data = {
        "id": order.shopify_order_id,
        "financial_status": order.financial_status,
        "shipping_address": order.shipping_address_json,
    }

    try:
        validate_order_policy(order_data)
        async with session.begin_nested():
            await mark_validated(session, order)
    except PolicyViolation as e:
        async with session.begin_nested():
            await mark_failed(session, order, reason=e.reason)

    fetched = await get_order_by_id(session, order.id)
    assert fetched.status == OrderStatus.VALIDATED


@pytest.mark.asyncio
async def test_full_flow_failed_unpaid(session: AsyncSession) -> None:
    """Simulate worker logic when financial_status is not 'paid'."""
    order = await _create(session, overrides={"financial_status": "pending"})

    order_data = {
        "id": order.shopify_order_id,
        "financial_status": order.financial_status,
        "shipping_address": order.shipping_address_json,
    }

    try:
        validate_order_policy(order_data)
        async with session.begin_nested():
            await mark_validated(session, order)
    except PolicyViolation as e:
        async with session.begin_nested():
            await mark_failed(session, order, reason=e.reason)

    fetched = await get_order_by_id(session, order.id)
    assert fetched.status == OrderStatus.FAILED
    assert "paid" in fetched.fail_reason


@pytest.mark.asyncio
async def test_full_flow_failed_no_shipping(session: AsyncSession) -> None:
    """Simulate worker logic when shipping_address is missing."""
    order = await _create(session, overrides={"shipping_address": None})

    order_data = {
        "id": order.shopify_order_id,
        "financial_status": order.financial_status,
        "shipping_address": order.shipping_address_json,
    }

    try:
        validate_order_policy(order_data)
        async with session.begin_nested():
            await mark_validated(session, order)
    except PolicyViolation as e:
        async with session.begin_nested():
            await mark_failed(session, order, reason=e.reason)

    fetched = await get_order_by_id(session, order.id)
    assert fetched.status == OrderStatus.FAILED
    assert "shipping" in fetched.fail_reason
