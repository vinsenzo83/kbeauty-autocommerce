"""
tests/test_sprint10_webhook_ingress.py
───────────────────────────────────────
Sprint 10 – Tests for the unified multi-channel webhook ingress.

Coverage:
  1. test_shopify_order_created         – POST /webhook/shopify creates a ChannelOrderV2
  2. test_shopee_order_created          – POST /webhook/shopee creates a ChannelOrderV2
  3. test_tiktok_order_created          – POST /webhook/tiktok creates a ChannelOrderV2
  4. test_shopify_product_updated       – POST /webhook/shopify creates a CanonicalProduct
  5. test_tiktok_product_updated        – POST /webhook/tiktok creates / updates a CanonicalProduct
  6. test_ingress_idempotency           – duplicate event_id returns status=duplicate (200)
  7. test_cross_channel_idempotency     – same payload sent to two different channels
                                          creates two distinct rows (different event_ids)
  8. test_invalid_json_returns_400      – malformed body returns 400

All tests use an in-process SQLite (aiosqlite) so no external DB is required.
"""
from __future__ import annotations

import json
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

# ── SQLite compatibility: override JSONB → JSON at dialect level ──────────────
# Needed because tests use aiosqlite (no native JSONB).
from sqlalchemy.dialects import registry as _dialect_registry  # noqa: F401
import sqlalchemy.dialects.sqlite.json as _sqlite_json  # noqa: F401

_JSONB_orig_init = JSONB.__init__


from app.main import create_app
from app.models.webhook_event import Base as WebhookBase, WebhookEvent
from app.models.channel_order import Base as ChannelOrderBase, ChannelOrderV2
from app.models.canonical_product import Base as ProductBase, CanonicalProduct

# Patch JSONB columns to use JSON for SQLite test engine
from sqlalchemy import event as _sa_event
from sqlalchemy.engine import Engine as _Engine
import sqlite3 as _sqlite3


@_sa_event.listens_for(_Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, _sqlite3.Connection):
        pass  # no-op; pragma handled elsewhere


def _patch_jsonb_for_sqlite():
    """Replace JSONB type in Sprint-10 model columns with JSON for SQLite compat."""
    from sqlalchemy import JSON as _JSON
    for model, col_name in [
        (WebhookEvent, "payload_json"),
        (ChannelOrderV2, "raw_payload"),
    ]:
        col = getattr(model, col_name).property.columns[0]
        col.type = _JSON()


_patch_jsonb_for_sqlite()

# ── SQLite in-memory engine ───────────────────────────────────────────────────
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(autouse=True)
async def setup_db() -> AsyncGenerator[None, None]:
    """Create all Sprint-10 tables before each test, drop after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(WebhookBase.metadata.create_all)
        await conn.run_sync(ChannelOrderBase.metadata.create_all)
        await conn.run_sync(ProductBase.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(WebhookBase.metadata.drop_all)
        await conn.run_sync(ChannelOrderBase.metadata.drop_all)
        await conn.run_sync(ProductBase.metadata.drop_all)


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app(use_lifespan=False)

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

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ── Helper payloads ───────────────────────────────────────────────────────────

def shopify_order_payload(order_id: int = 820982911946154000) -> dict:
    return {
        "id": order_id,
        "email": "test@example.com",
        "total_price": "99.00",
        "currency": "USD",
        "financial_status": "paid",
        "created_at": "2024-01-01T10:00:00+00:00",
        "line_items": [{"title": "Snail Cream", "quantity": 1, "price": "99.00"}],
        "customer": {"first_name": "Test", "last_name": "User", "email": "test@example.com"},
    }


def shopify_product_payload(product_id: int = 632910392) -> dict:
    return {
        "id": product_id,
        "title": "COSRX Snail Essence 100ml",
        "vendor": "COSRX",
        "updated_at": "2024-01-15T12:00:00+00:00",
        "variants": [{"id": 808950810, "price": "25.00", "sku": "COSRX-SNL-100"}],
        "images": [{"src": "https://cdn.shopify.com/s/files/1/cosrx.jpg"}],
    }


def shopee_order_payload(order_sn: str = "SHP240101ABCDEF") -> dict:
    return {
        "code": 3,
        "timestamp": 1704067200,
        "shop_id": 123456789,
        "data": {
            "order_sn": order_sn,
            "total_amount": 31.50,
            "currency": "MYR",
            "buyer_username": "kbeauty_buyer",
        },
    }

def tiktok_order_payload(order_id: str = "576462233419950401") -> dict:
    return {
        "type": "ORDER_STATUS_CHANGE",
        "shop_id": "7123456789",
        "timestamp": 1704067200,
        "data": {
            "order_id": order_id,
            "currency": "USD",
            "payment_info": {"total_amount": "39.90"},
            "recipient_address": {"name": "Park Ji Sung"},
        },
    }


def tiktok_product_payload(product_id: str = "1729579164882894277") -> dict:
    return {
        "type": "PRODUCT_STATUS_CHANGE",
        "shop_id": "7123456789",
        "timestamp": 1704067200,
        "data": {
            "product_id": product_id,
            "title": "Laneige Lip Sleeping Mask Berry 20g",
            "update_time": 1704067200,
            "main_images": [{"url": "https://p16-oecms.tiktokcdn.com/img/laneige.jpg"}],
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_shopify_order_created(client: AsyncClient) -> None:
    """Shopify order webhook → ChannelOrderV2 created, WebhookEvent status=processed."""
    payload = shopify_order_payload()
    resp = await client.post(
        "/webhook/shopify",
        json=payload,
        headers={"X-Shopify-Topic": "orders/create"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ok"
    assert data["topic"] == "order.created"
    assert data["channel"] == "shopify"
    assert data["result_ref"] is not None

    async with TestSession() as s:
        orders = (await s.execute(select(ChannelOrderV2))).scalars().all()
        events = (await s.execute(select(WebhookEvent))).scalars().all()

    assert len(orders) == 1
    assert orders[0].channel == "shopify"
    assert orders[0].external_order_id == str(payload["id"])
    assert float(orders[0].total_price) == 99.0

    assert len(events) == 1
    assert events[0].status == "processed"
    assert events[0].topic == "order.created"


@pytest.mark.asyncio
async def test_shopee_order_created(client: AsyncClient) -> None:
    """Shopee order webhook → ChannelOrderV2 created."""
    payload = shopee_order_payload()
    resp = await client.post("/webhook/shopee", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ok"
    assert data["topic"] == "order.created"
    assert data["channel"] == "shopee"

    async with TestSession() as s:
        orders = (await s.execute(
            select(ChannelOrderV2).where(ChannelOrderV2.channel == "shopee")
        )).scalars().all()
    assert len(orders) == 1
    assert orders[0].external_order_id == "SHP240101ABCDEF"


@pytest.mark.asyncio
async def test_tiktok_order_created(client: AsyncClient) -> None:
    """TikTok order webhook → ChannelOrderV2 created."""
    payload = tiktok_order_payload()
    resp = await client.post("/webhook/tiktok", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ok"
    assert data["topic"] == "order.created"

    async with TestSession() as s:
        orders = (await s.execute(
            select(ChannelOrderV2).where(ChannelOrderV2.channel == "tiktok")
        )).scalars().all()
    assert len(orders) == 1
    assert orders[0].external_order_id == "576462233419950401"


@pytest.mark.asyncio
async def test_shopify_product_updated(client: AsyncClient) -> None:
    """Shopify product webhook → CanonicalProduct created with channel-prefixed SKU."""
    payload = shopify_product_payload()
    resp = await client.post(
        "/webhook/shopify",
        json=payload,
        headers={"X-Shopify-Topic": "products/update"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ok"
    assert data["topic"] == "product.updated"

    async with TestSession() as s:
        products = (await s.execute(select(CanonicalProduct))).scalars().all()
    assert len(products) == 1
    assert products[0].canonical_sku == f"shopify-{payload['id']}"
    assert products[0].name == payload["title"]
    assert products[0].brand == payload["vendor"]


@pytest.mark.asyncio
async def test_tiktok_product_updated(client: AsyncClient) -> None:
    """TikTok product webhook → CanonicalProduct created; second send updates it."""
    payload = tiktok_product_payload()

    # First send – create
    r1 = await client.post("/webhook/tiktok", json=payload)
    assert r1.status_code == 200
    assert r1.json()["status"] == "ok"

    # Second send with new title (different payload → different event_id → new event)
    payload2 = dict(payload)
    payload2["data"] = dict(payload["data"], title="Laneige Lip Mask (NEW)")
    r2 = await client.post("/webhook/tiktok", json=payload2)
    assert r2.status_code == 200
    assert r2.json()["status"] == "ok"

    async with TestSession() as s:
        products = (await s.execute(
            select(CanonicalProduct).where(
                CanonicalProduct.canonical_sku == f"tiktok-{payload['data']['product_id']}"
            )
        )).scalars().all()
        events = (await s.execute(select(WebhookEvent))).scalars().all()

    # Only one product row (upsert), but name updated
    assert len(products) == 1
    assert products[0].name == "Laneige Lip Mask (NEW)"
    # Two distinct webhook events (different payloads → different sha256 event_ids)
    assert len(events) == 2


@pytest.mark.asyncio
async def test_ingress_idempotency(client: AsyncClient) -> None:
    """Sending the exact same payload twice → 2nd response is status=duplicate, only 1 DB row."""
    payload = shopify_order_payload()
    body = json.dumps(payload).encode()

    # Send with a stable event-id header to guarantee same event_id both times
    headers = {
        "X-Shopify-Topic": "orders/create",
        "X-Shopify-Webhook-Id": "stable-test-event-id-abc123",
    }

    r1 = await client.post("/webhook/shopify", content=body, headers={
        **headers, "Content-Type": "application/json",
    })
    r2 = await client.post("/webhook/shopify", content=body, headers={
        **headers, "Content-Type": "application/json",
    })

    assert r1.status_code == 200
    assert r1.json()["status"] == "ok"

    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate", \
        f"Expected 'duplicate', got: {r2.json()}"

    async with TestSession() as s:
        events = (await s.execute(select(WebhookEvent))).scalars().all()
        orders = (await s.execute(select(ChannelOrderV2))).scalars().all()

    assert len(events) == 1, "Duplicate event should not create a second webhook_event row"
    assert len(orders) == 1, "Duplicate event should not create a second order row"


@pytest.mark.asyncio
async def test_cross_channel_idempotency(client: AsyncClient) -> None:
    """Same payload body sent to /shopify and /shopee creates two separate rows
    because channel is included in the sha256 event_id."""
    # Use a small payload that looks like an order to both detectors
    payload = {"id": 99999, "order_sn": "CROSS999", "financial_status": "paid",
               "total_price": "10.00", "currency": "USD",
               "line_items": [{"title": "Test"}]}
    body = json.dumps(payload).encode()
    ct = {"Content-Type": "application/json"}

    r1 = await client.post("/webhook/shopify", content=body,
                            headers={**ct, "X-Shopify-Topic": "orders/create"})
    r2 = await client.post("/webhook/shopee",  content=body, headers=ct)

    assert r1.status_code == 200
    assert r2.status_code == 200

    async with TestSession() as s:
        events = (await s.execute(select(WebhookEvent))).scalars().all()
        orders = (await s.execute(select(ChannelOrderV2))).scalars().all()

    assert len(events) == 2, "Same payload to different channels must create 2 distinct events"
    channels = {e.channel for e in events}
    assert channels == {"shopify", "shopee"}
    assert len(orders) == 2


@pytest.mark.asyncio
async def test_invalid_json_returns_400(client: AsyncClient) -> None:
    """Malformed JSON body → 400 Bad Request."""
    resp = await client.post(
        "/webhook/shopify",
        content=b"{invalid json!!!}",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["status"] == "error"
