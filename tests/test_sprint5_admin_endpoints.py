from __future__ import annotations

"""
tests/test_sprint5_admin_endpoints.py
───────────────────────────────────────
Sprint 5 tests — Admin API endpoints (auth, orders, ops, tickets).

Uses httpx ASGI transport with a mocked DB (MagicMock/AsyncMock).
JWT tokens are created directly without touching jose.
"""

from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _auth_headers(role: str = "ADMIN") -> dict[str, str]:
    return {"Authorization": f"Bearer fake-token-{role}"}


def _make_current_user(role: str = "ADMIN"):
    from app.services.auth_service import CurrentUser
    return CurrentUser(sub="admin@kbeauty.local", role=role)


def _make_order(
    *,
    status: str = "PLACED",
    shopify_id: str = "SPRINT5-001",
    supplier_order_id: str = "SK-999",
) -> MagicMock:
    order = MagicMock()
    order.id                    = uuid4()
    order.shopify_order_id      = shopify_id
    order.email                 = "ops@example.com"
    order.total_price           = "89000.00"
    order.currency              = "KRW"
    order.financial_status      = "paid"
    order.status                = status
    order.supplier              = "stylekorean"
    order.supplier_order_id     = supplier_order_id
    order.shipping_address_json = {"country": "South Korea"}
    order.line_items_json       = [{"title": "Cream", "quantity": 1}]
    order.placed_at             = datetime.now(timezone.utc)
    order.shipped_at            = None
    order.tracking_number       = None
    order.tracking_url          = None
    order.fail_reason           = None
    order.created_at            = datetime.now(timezone.utc)
    order.updated_at            = datetime.now(timezone.utc)
    return order


def _make_ticket(*, status: str = "OPEN") -> MagicMock:
    t = MagicMock()
    t.id         = uuid4()
    t.order_id   = None
    t.type       = "OTHER"
    t.status     = status
    t.subject    = "Test ticket"
    t.payload    = None
    t.note       = None
    t.created_by = "admin@kbeauty.local"
    t.closed_at  = None
    t.created_at = datetime.now(timezone.utc)
    t.updated_at = datetime.now(timezone.utc)
    return t


# ─────────────────────────────────────────────────────────────────────────────
# App + client fixture (no lifespan, DB fully mocked)
# ─────────────────────────────────────────────────────────────────────────────

def _fake_decode(token: str) -> dict:
    role = "ADMIN"
    if "VIEWER"   in token: role = "VIEWER"
    elif "OPERATOR" in token: role = "OPERATOR"
    return {"sub": "admin@kbeauty.local", "role": role}


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    from app.main import create_app
    from app.db.session import get_db

    async def override_db():
        """Yield a plain AsyncMock DB session."""
        db = AsyncMock()
        db.add    = MagicMock()
        db.flush  = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.execute = AsyncMock()
        yield db

    app = create_app(use_lifespan=False)
    app.dependency_overrides[get_db] = override_db

    with patch("app.services.auth_service.decode_token", side_effect=_fake_decode):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac


# ═══════════════════════════════════════════════════════════════════════════════
# A) Auth: login
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_login_with_env_credentials(client: AsyncClient) -> None:
    with patch("app.services.auth_service.create_access_token", return_value="test-token-xyz"):
        resp = await client.post("/admin/auth/login", json={
            "email":    "admin@kbeauty.local",
            "password": "admin1234",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["role"]   == "ADMIN"


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(client: AsyncClient) -> None:
    resp = await client.post("/admin/auth/login", json={
        "email":    "admin@kbeauty.local",
        "password": "wrongpassword",
    })
    assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# B) Auth: protected endpoints
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_kpi_requires_auth(client: AsyncClient) -> None:
    with patch("app.services.auth_service.decode_token", side_effect=Exception("no token")):
        resp = await client.get("/admin/dashboard/kpi")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_me_returns_current_user(client: AsyncClient) -> None:
    resp = await client.get("/admin/auth/me", headers=_auth_headers("ADMIN"))
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"]  == "ADMIN"
    assert data["email"] == "admin@kbeauty.local"


# ═══════════════════════════════════════════════════════════════════════════════
# C) Dashboard KPI endpoint
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dashboard_kpi_endpoint_returns_structure(client: AsyncClient) -> None:
    from decimal import Decimal

    def _scalar(v):
        m = MagicMock(); m.scalar_one = MagicMock(return_value=v); return m

    kpi_results = [_scalar(0), _scalar(None), _scalar(0), _scalar(0), _scalar(0)]

    with patch("app.routers.admin.compute_kpi", new_callable=AsyncMock) as mock_kpi:
        mock_kpi.return_value = {
            "orders_today": 0, "revenue_today": 0.0,
            "avg_margin_pct": 0.0, "failed_today": 0,
            "tracking_stale_count": 0, "open_tickets_count": 0,
        }
        resp = await client.get("/admin/dashboard/kpi", headers=_auth_headers())

    assert resp.status_code == 200
    data = resp.json()
    for key in ("orders_today", "revenue_today", "avg_margin_pct",
                "failed_today", "tracking_stale_count", "open_tickets_count"):
        assert key in data


# ═══════════════════════════════════════════════════════════════════════════════
# D) Orders list
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_orders_list_returns_items(client: AsyncClient) -> None:
    order = _make_order()
    with patch("app.routers.admin.list_orders", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = ([order], 1)
        resp = await client.get("/admin/orders", headers=_auth_headers())

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1


@pytest.mark.asyncio
async def test_orders_list_filter_by_status(client: AsyncClient) -> None:
    order = _make_order(status="PLACED")
    with patch("app.routers.admin.list_orders", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = ([order], 1)
        resp = await client.get("/admin/orders?status=PLACED", headers=_auth_headers())

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(i["status"] == "PLACED" for i in items)


@pytest.mark.asyncio
async def test_orders_list_filter_by_q(client: AsyncClient) -> None:
    order = _make_order(shopify_id="SPRINT5-001")
    with patch("app.routers.admin.list_orders", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = ([order], 1)
        resp = await client.get("/admin/orders?q=SPRINT5", headers=_auth_headers())

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any("SPRINT5" in i["shopify_order_id"] for i in items)


@pytest.mark.asyncio
async def test_orders_list_pagination(client: AsyncClient) -> None:
    orders = [_make_order(shopify_id=f"PAGE-{i:03d}") for i in range(3)]
    with patch("app.routers.admin.list_orders", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = (orders, 5)
        resp = await client.get("/admin/orders?page=1&page_size=3", headers=_auth_headers())

    assert resp.status_code == 200
    data = resp.json()
    assert data["page_size"] == 3
    assert len(data["items"]) <= 3


# ═══════════════════════════════════════════════════════════════════════════════
# E) Order detail
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_order_detail_includes_events_and_artifacts(client: AsyncClient) -> None:
    order = _make_order()

    scalars_mock = MagicMock()
    scalars_mock.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))

    with patch("app.routers.admin.get_order_by_id", new_callable=AsyncMock) as mock_get, \
         patch("app.routers.admin._list_artifacts", return_value=[]):
        mock_get.return_value = order
        # The route also calls db.execute for event_log
        with patch("app.routers.admin.AsyncSession") as _:
            resp = await client.get(
                f"/admin/orders/{order.id}", headers=_auth_headers()
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["shopify_order_id"] == "SPRINT5-001"
    assert "events"    in data
    assert "artifacts" in data


@pytest.mark.asyncio
async def test_order_detail_404(client: AsyncClient) -> None:
    with patch("app.routers.admin.get_order_by_id", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        resp = await client.get(f"/admin/orders/{uuid4()}", headers=_auth_headers())
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# F) Ops: retry-place logs event
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_retry_place_enqueues_task(client: AsyncClient) -> None:
    order = _make_order(status="FAILED")

    with patch("app.routers.admin.get_order_by_id", new_callable=AsyncMock) as mock_get, \
         patch("app.routers.admin.celery_app") as mock_celery, \
         patch("app.routers.admin._log_event", new_callable=AsyncMock):
        mock_get.return_value    = order
        mock_celery.send_task    = MagicMock()
        resp = await client.post(
            f"/admin/orders/{order.id}/retry-place",
            headers=_auth_headers("OPERATOR"),
        )

    assert resp.status_code == 202
    mock_celery.send_task.assert_called_once()


@pytest.mark.asyncio
async def test_retry_place_requires_operator_role(client: AsyncClient) -> None:
    order = _make_order(status="FAILED")
    with patch("app.routers.admin.get_order_by_id", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = order
        resp = await client.post(
            f"/admin/orders/{order.id}/retry-place",
            headers=_auth_headers("VIEWER"),
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_retry_place_non_failed_returns_409(client: AsyncClient) -> None:
    order = _make_order(status="PLACED")  # not FAILED
    with patch("app.routers.admin.get_order_by_id", new_callable=AsyncMock) as mock_get, \
         patch("app.routers.admin.celery_app"):
        mock_get.return_value = order
        resp = await client.post(
            f"/admin/orders/{order.id}/retry-place",
            headers=_auth_headers("OPERATOR"),
        )
    assert resp.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# G) Ops: cancel-refund
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cancel_refund_calls_shopify(client: AsyncClient) -> None:
    order = _make_order(status="PLACED")

    mock_shopify = MagicMock()
    mock_shopify.cancel_order = AsyncMock(return_value=True)

    with patch("app.routers.admin.get_order_by_id",   new_callable=AsyncMock) as mock_get, \
         patch("app.routers.admin.get_shopify_client", return_value=mock_shopify), \
         patch("app.routers.admin.mark_canceled",      new_callable=AsyncMock) as mock_cancel, \
         patch("app.routers.admin._log_event",         new_callable=AsyncMock):
        mock_get.return_value    = order
        mock_cancel.return_value = order
        resp = await client.post(
            f"/admin/orders/{order.id}/cancel-refund",
            headers=_auth_headers("OPERATOR"),
        )

    assert resp.status_code == 202
    mock_shopify.cancel_order.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════════
# H) Ops: create-ticket
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_create_ticket_for_order(client: AsyncClient) -> None:
    order  = _make_order()
    ticket = _make_ticket()

    with patch("app.routers.admin.get_order_by_id", new_callable=AsyncMock) as mock_get, \
         patch("app.routers.admin.create_ticket",   new_callable=AsyncMock) as mock_ticket:
        mock_get.return_value    = order
        mock_ticket.return_value = ticket
        resp = await client.post(
            f"/admin/orders/{order.id}/create-ticket",
            json={"type": "TRACKING_ISSUE", "subject": "Tracking not updated"},
            headers=_auth_headers("OPERATOR"),
        )

    assert resp.status_code == 201
    assert "ticket_id" in resp.json()


# ═══════════════════════════════════════════════════════════════════════════════
# I) Tickets CRUD
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_list_tickets_returns_items(client: AsyncClient) -> None:
    ticket = _make_ticket()
    with patch("app.routers.admin.list_tickets", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = ([ticket], 1)
        resp = await client.get("/admin/tickets", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_close_ticket_changes_status(client: AsyncClient) -> None:
    ticket         = _make_ticket()
    ticket.status  = "CLOSED"
    ticket.closed_at = datetime.now(timezone.utc)

    with patch("app.routers.admin.close_ticket", new_callable=AsyncMock) as mock_close:
        mock_close.return_value = ticket
        resp = await client.post(
            f"/admin/tickets/{ticket.id}/close",
            headers=_auth_headers("OPERATOR"),
        )
    assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# J) Artifacts — path traversal guard
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_artifacts_path_traversal_blocked(client: AsyncClient) -> None:
    resp = await client.get(
        "/admin/artifacts?path=../../etc/passwd",
        headers=_auth_headers(),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_artifacts_missing_file_returns_404(client: AsyncClient) -> None:
    resp = await client.get(
        "/admin/artifacts?path=bot_failures/nonexistent/screenshot.png",
        headers=_auth_headers(),
    )
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# K) Role enforcement & misc
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_switch_supplier_returns_409(client: AsyncClient) -> None:
    resp = await client.post(
        f"/admin/orders/{uuid4()}/switch-supplier",
        headers=_auth_headers("ADMIN"),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_admin_health_endpoint(client: AsyncClient) -> None:
    with patch("app.routers.admin.compute_health", new_callable=AsyncMock) as mock_health:
        mock_health.return_value = {
            "db_ok": True, "redis_ok": False,
            "queue_depth": None, "recent_failures_24h": [],
        }
        resp = await client.get("/admin/health", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert "db_ok"    in data
    assert "redis_ok" in data
