from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import AsyncGenerator
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import create_app
from app.models.event_log import Base as EventBase
from app.models.event_log import EventLog
from app.models.order import Base as OrderBase
from app.models.order import Order

# ── In-memory SQLite engine (no external dependencies) ────────────────────────
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

SECRET = "test-secret"

SAMPLE_PAYLOAD: dict = {
    "id": 111222333,
    "email": "buyer@example.com",
    "total_price": "59000.00",
    "currency": "KRW",
    "financial_status": "paid",
    "shipping_address": {
        "first_name": "길동",
        "last_name": "홍",
        "address1": "강남구 테헤란로 1",
        "city": "서울",
        "country": "South Korea",
    },
    "line_items": [{"title": "Snail Cream 50ml", "quantity": 2, "price": "29500.00"}],
}


def _sign(body: bytes) -> str:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


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
async def client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app(use_lifespan=False)

    # Override DB dependency
    from app.db.session import get_db

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with TestSession() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    # Override settings secret
    from app.config import get_settings, Settings

    def override_settings() -> Settings:
        s = Settings()
        s.__dict__["SHOPIFY_WEBHOOK_SECRET"] = SECRET
        return s

    app.dependency_overrides[get_settings] = override_settings

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_webhook_creates_order(client: AsyncClient) -> None:
    """First delivery of a webhook creates exactly one order."""
    raw = json.dumps(SAMPLE_PAYLOAD).encode()
    sig = _sign(raw)

    with patch("app.webhooks.shopify.celery_app") as mock_celery:
        mock_celery.send_task = MagicMock()
        resp = await client.post(
            "/webhooks/shopify/order-created",
            content=raw,
            headers={
                "X-Shopify-Hmac-Sha256": sig,
                "X-Shopify-Topic": "orders/create",
                "Content-Type": "application/json",
            },
        )

    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "accepted"
    assert data["shopify_order_id"] == str(SAMPLE_PAYLOAD["id"])

    async with TestSession() as session:
        result = await session.execute(select(Order))
        orders = result.scalars().all()
    assert len(orders) == 1
    assert orders[0].shopify_order_id == str(SAMPLE_PAYLOAD["id"])


@pytest.mark.asyncio
async def test_duplicate_webhook_is_idempotent(client: AsyncClient) -> None:
    """Sending the same payload twice must not create a second order."""
    raw = json.dumps(SAMPLE_PAYLOAD).encode()
    sig = _sign(raw)
    headers = {
        "X-Shopify-Hmac-Sha256": sig,
        "X-Shopify-Topic": "orders/create",
        "Content-Type": "application/json",
    }

    with patch("app.webhooks.shopify.celery_app") as mock_celery:
        mock_celery.send_task = MagicMock()
        r1 = await client.post("/webhooks/shopify/order-created", content=raw, headers=headers)
        r2 = await client.post("/webhooks/shopify/order-created", content=raw, headers=headers)

    assert r1.status_code == 202
    assert r1.json()["status"] == "accepted"

    assert r2.status_code == 202
    assert r2.json()["status"] == "duplicate"

    async with TestSession() as session:
        result = await session.execute(select(Order))
        orders = result.scalars().all()
    assert len(orders) == 1, "Duplicate webhook must not create a second order"


@pytest.mark.asyncio
async def test_invalid_hmac_returns_401(client: AsyncClient) -> None:
    raw = json.dumps(SAMPLE_PAYLOAD).encode()
    resp = await client.post(
        "/webhooks/shopify/order-created",
        content=raw,
        headers={
            "X-Shopify-Hmac-Sha256": "invalidsignature==",
            "X-Shopify-Topic": "orders/create",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401
