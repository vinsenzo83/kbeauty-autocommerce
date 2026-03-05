"""
tests/test_sprint11_webhook_verify.py
───────────────────────────────────────
Sprint 11 – Webhook signature verification tests.

Coverage (pure-unit + ASGI integration, mock-only, no external DB):

Unit – verify_shopify_webhook()
  1. test_verify_shopify_valid_signature      – correct HMAC returns True
  2. test_verify_shopify_wrong_secret         – wrong secret returns False
  3. test_verify_shopify_tampered_body        – modified body returns False
  4. test_verify_shopify_empty_header         – empty header returns False
  5. test_verify_shopify_none_inputs          – None/empty values return False

Integration – POST /webhook/shopify (ASGI client, SQLite in-memory)
  6. test_ingress_valid_sig_verify_on         – valid sig + WEBHOOK_VERIFY=1 → 200 ok
  7. test_ingress_invalid_sig_verify_on       – invalid sig + WEBHOOK_VERIFY=1 → 401
  8. test_ingress_missing_header_verify_on    – no header + WEBHOOK_VERIFY=1 → 401
  9. test_ingress_verify_disabled             – bad sig + WEBHOOK_VERIFY=0 → 200 ok
 10. test_ingress_reason_in_401_body          – 401 body contains SHOPIFY_WEBHOOK_SIGNATURE_INVALID
 11. test_ingress_shopee_no_verify            – Shopee endpoint never checks Shopify HMAC
 12. test_ingress_tiktok_no_verify            – TikTok endpoint never checks Shopify HMAC
 13. test_existing_sprint10_tests_unaffected  – previous sprint10 tests still pass with verify off
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── SQLite compat patch (mirrors test_sprint10_webhook_ingress.py) ────────────
from app.main import create_app
from app.models.webhook_event import Base as WebhookBase, WebhookEvent
from app.models.channel_order import Base as ChannelOrderBase, ChannelOrderV2
from app.models.canonical_product import Base as ProductBase, CanonicalProduct
from app.webhooks.verify import verify_shopify_webhook


def _patch_jsonb_for_sqlite() -> None:
    """Replace JSONB columns with JSON for SQLite in-memory test engine."""
    for model, col_name in [
        (WebhookEvent, "payload_json"),
        (ChannelOrderV2, "raw_payload"),
    ]:
        col = getattr(model, col_name).property.columns[0]
        col.type = JSON()


_patch_jsonb_for_sqlite()

# ── In-memory SQLite engine ───────────────────────────────────────────────────
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

_SECRET = "shopify-test-secret-sprint11"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_hmac(secret: str, body: bytes) -> str:
    """Compute the correct Shopify HMAC for a body."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


# ── DB fixtures ───────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(autouse=True)
async def setup_db() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(WebhookBase.metadata.create_all)
        await conn.run_sync(ChannelOrderBase.metadata.create_all)
        await conn.run_sync(ProductBase.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(WebhookBase.metadata.drop_all)
        await conn.run_sync(ChannelOrderBase.metadata.drop_all)
        await conn.run_sync(ProductBase.metadata.drop_all)


def _make_client(webhook_verify: bool, secret: str = _SECRET) -> AsyncClient:
    """Build a test ASGI client with overridden settings."""
    from app.config import get_settings, Settings
    from app.db.session import get_db

    app = create_app(use_lifespan=False)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        async with TestSession() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    def override_settings() -> Settings:
        s = Settings()
        s.__dict__["WEBHOOK_VERIFY"] = webhook_verify
        s.__dict__["SHOPIFY_WEBHOOK_SECRET"] = secret
        return s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = override_settings

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


_SAMPLE_ORDER = {
    "id": 500001,
    "total_price": "50.00",
    "currency": "USD",
    "financial_status": "paid",
    "line_items": [{"title": "Sprint11 Test Product", "quantity": 1}],
    "customer": {"first_name": "Sprint", "last_name": "Eleven"},
}


# ══════════════════════════════════════════════════════════════════════════════
# UNIT TESTS — verify_shopify_webhook()
# ══════════════════════════════════════════════════════════════════════════════

def test_verify_shopify_valid_signature() -> None:
    """Correct HMAC digest must return True."""
    body = b'{"id":1,"total_price":"10.00"}'
    header_hmac = _make_hmac(_SECRET, body)
    assert verify_shopify_webhook(_SECRET, body, header_hmac) is True


def test_verify_shopify_wrong_secret() -> None:
    """HMAC signed with a different secret must return False."""
    body = b'{"id":1,"total_price":"10.00"}'
    header_hmac = _make_hmac("wrong-secret", body)
    assert verify_shopify_webhook(_SECRET, body, header_hmac) is False


def test_verify_shopify_tampered_body() -> None:
    """HMAC valid for original body must fail if body is modified."""
    original = b'{"id":1,"total_price":"10.00"}'
    tampered = b'{"id":1,"total_price":"99999.00"}'
    header_hmac = _make_hmac(_SECRET, original)
    assert verify_shopify_webhook(_SECRET, tampered, header_hmac) is False


def test_verify_shopify_empty_header() -> None:
    """Missing / empty HMAC header must return False."""
    body = b'{"id":1}'
    assert verify_shopify_webhook(_SECRET, body, "") is False
    assert verify_shopify_webhook(_SECRET, body, "   ") is False


def test_verify_shopify_none_inputs() -> None:
    """None or empty secret / body must return False without raising."""
    body = b'{"id":1}'
    assert verify_shopify_webhook("", body, "anything") is False
    assert verify_shopify_webhook(_SECRET, b"", "anything") is False


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — POST /webhook/shopify via ASGI client
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_ingress_valid_sig_verify_on() -> None:
    """Valid signature with WEBHOOK_VERIFY=1 → 200 ok."""
    body = json.dumps(_SAMPLE_ORDER).encode()
    sig  = _make_hmac(_SECRET, body)

    async with _make_client(webhook_verify=True) as ac:
        resp = await ac.post(
            "/webhook/shopify",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Topic": "orders/create",
                "X-Shopify-Hmac-Sha256": sig,
            },
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_ingress_invalid_sig_verify_on() -> None:
    """Invalid signature with WEBHOOK_VERIFY=1 → 401 Unauthorized."""
    body = json.dumps(_SAMPLE_ORDER).encode()

    async with _make_client(webhook_verify=True) as ac:
        resp = await ac.post(
            "/webhook/shopify",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Topic": "orders/create",
                "X-Shopify-Hmac-Sha256": "aW52YWxpZHNpZ25hdHVyZQ==",  # invalid
            },
        )
    assert resp.status_code == 401, resp.text
    data = resp.json()
    assert data["status"] == "unauthorized"
    assert data["reason"] == "SHOPIFY_WEBHOOK_SIGNATURE_INVALID"


@pytest.mark.asyncio
async def test_ingress_missing_header_verify_on() -> None:
    """No HMAC header with WEBHOOK_VERIFY=1 → 401 Unauthorized."""
    body = json.dumps(_SAMPLE_ORDER).encode()

    async with _make_client(webhook_verify=True) as ac:
        resp = await ac.post(
            "/webhook/shopify",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Topic": "orders/create",
                # X-Shopify-Hmac-Sha256 intentionally omitted
            },
        )
    assert resp.status_code == 401, resp.text
    assert resp.json()["reason"] == "SHOPIFY_WEBHOOK_SIGNATURE_INVALID"


@pytest.mark.asyncio
async def test_ingress_verify_disabled() -> None:
    """Invalid / missing signature with WEBHOOK_VERIFY=0 → 200 ok (dev mode)."""
    body = json.dumps(_SAMPLE_ORDER).encode()

    async with _make_client(webhook_verify=False) as ac:
        resp = await ac.post(
            "/webhook/shopify",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Topic": "orders/create",
                "X-Shopify-Hmac-Sha256": "totallywrong==",
            },
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_ingress_reason_in_401_body() -> None:
    """401 response body must contain the canonical reason string."""
    body = b'{"id":99,"line_items":[]}'

    async with _make_client(webhook_verify=True) as ac:
        resp = await ac.post(
            "/webhook/shopify",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Hmac-Sha256": "badsig==",
            },
        )
    assert resp.status_code == 401
    payload = resp.json()
    assert "SHOPIFY_WEBHOOK_SIGNATURE_INVALID" in payload.get("reason", "")
    assert "detail" in payload          # human-readable detail must be present


@pytest.mark.asyncio
async def test_ingress_shopee_no_verify() -> None:
    """Shopee endpoint must NOT apply Shopify HMAC check (even with WEBHOOK_VERIFY=1)."""
    shopee_payload = {
        "code": 3,
        "data": {
            "order_sn": "SHOPEE_S11_001",
            "total_amount": 20.0,
            "currency": "MYR",
            "buyer_username": "tester",
        },
    }
    body = json.dumps(shopee_payload).encode()

    async with _make_client(webhook_verify=True) as ac:
        resp = await ac.post(
            "/webhook/shopee",
            content=body,
            headers={"Content-Type": "application/json"},
            # No X-Shopify-Hmac-Sha256 header → must NOT trigger 401
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] in ("ok", "duplicate")


@pytest.mark.asyncio
async def test_ingress_tiktok_no_verify() -> None:
    """TikTok endpoint must NOT apply Shopify HMAC check (even with WEBHOOK_VERIFY=1)."""
    tiktok_payload = {
        "type": "ORDER_STATUS_CHANGE",
        "shop_id": "999",
        "data": {
            "order_id": "TT_S11_001",
            "currency": "USD",
            "payment_info": {"total_amount": "29.00"},
        },
    }
    body = json.dumps(tiktok_payload).encode()

    async with _make_client(webhook_verify=True) as ac:
        resp = await ac.post(
            "/webhook/tiktok",
            content=body,
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] in ("ok", "duplicate")


@pytest.mark.asyncio
async def test_existing_sprint10_tests_unaffected() -> None:
    """Regression guard: Sprint 10 flow works unchanged when verify=False (default)."""
    payload = {
        "id": 600001,
        "total_price": "30.00",
        "currency": "KRW",
        "financial_status": "paid",
        "line_items": [{"title": "Regression Guard Item"}],
    }
    body = json.dumps(payload).encode()

    async with _make_client(webhook_verify=False) as ac:
        r1 = await ac.post(
            "/webhook/shopify",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Topic": "orders/create",
            },
        )
        # Second send – idempotency
        r2 = await ac.post(
            "/webhook/shopify",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Topic": "orders/create",
            },
        )

    assert r1.status_code == 200
    assert r1.json()["status"] == "ok"
    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate"
