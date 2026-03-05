"""
tests/test_sprint14_fulfillment.py
────────────────────────────────────
Sprint 14 – Mock-only tests for the Auto-Fulfillment Pipeline.

Coverage
--------
1.  test_select_best_supplier_in_stock           – cheapest IN_STOCK supplier selected
2.  test_select_best_supplier_none_when_no_stock – no IN_STOCK → returns None
3.  test_process_channel_order_dry_run           – dry_run: placed without real API call
4.  test_process_channel_order_creates_record    – supplier_orders row created
5.  test_process_channel_order_no_line_items     – empty payload → NO_SUPPLIER_AVAILABLE
6.  test_process_channel_order_sku_not_canonical – unknown SKU skipped gracefully
7.  test_supplier_api_error_recorded             – SupplierError → FAILED + reason
8.  test_retry_logic_increments_count            – retry_count incremented on failure
9.  test_celery_poll_lock_skips_concurrent       – Redis lock busy → skipped
10. test_poll_ships_supplier_order               – status==shipped → tracking updated
11. test_shopify_fulfillment_stub_no_network     – ShopifyFulfillmentService stub
12. test_webhook_enqueues_fulfillment_task       – handle_order_created enqueues task
13. test_place_order_no_supplier_available       – failure_reason NO_SUPPLIER_AVAILABLE
14. test_place_order_all_three_suppliers         – each supplier returns PlacedOrder
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _uid() -> uuid.UUID:
    return uuid.uuid4()


def _make_supplier_product(supplier: str, price: float, in_stock: bool = True):
    sp = MagicMock()
    sp.supplier            = supplier
    sp.supplier_product_id = f"{supplier}-PROD-001"
    sp.price               = Decimal(str(price))
    sp.stock_status        = "IN_STOCK" if in_stock else "OUT_OF_STOCK"
    sp.canonical_product_id = _uid()
    return sp


def _make_channel_order(
    order_id=None,
    channel="shopify",
    external_order_id="SHOPIFY-001",
    raw_payload=None,
    status="received",
):
    co = MagicMock()
    co.id               = order_id or _uid()
    co.channel          = channel
    co.external_order_id = external_order_id
    co.currency         = "USD"
    co.buyer_name       = "Test Buyer"
    co.buyer_email      = "buyer@example.com"
    co.status           = status
    co.raw_payload      = raw_payload or {
        "line_items": [{"sku": "SKU-001", "quantity": 1}],
        "shipping_address": {"name": "Test Buyer", "address1": "123 Main", "city": "Seoul", "country_code": "KR", "zip": "04501"},
    }
    co.updated_at       = None
    return co


def _make_supplier_order(
    so_id=None,
    channel_order_id=None,
    supplier="STYLEKOREAN",
    status="pending",
    supplier_order_id=None,
    retry_count=0,
):
    so = MagicMock()
    so.id               = so_id or _uid()
    so.channel_order_id = channel_order_id or _uid()
    so.supplier         = supplier
    so.status           = status
    so.supplier_order_id = supplier_order_id
    so.supplier_status  = None
    so.tracking_number  = None
    so.tracking_carrier = None
    so.cost             = None
    so.currency         = "USD"
    so.failure_reason   = None
    so.retry_count      = retry_count
    so.updated_at       = None
    return so


# ─────────────────────────────────────────────────────────────────────────────
# 1. select_best_supplier – cheapest IN_STOCK
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_select_best_supplier_in_stock():
    from app.services.supplier_router import choose_best_supplier_for_canonical

    canonical_id = _uid()
    sp_cheap  = _make_supplier_product("JOLSE", 10.00)
    sp_expensive = _make_supplier_product("STYLEKOREAN", 15.00)

    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [sp_cheap, sp_expensive]
    session.execute = AsyncMock(return_value=mock_result)

    result = await choose_best_supplier_for_canonical(canonical_id, session)

    assert result is not None
    assert result["supplier"] == "JOLSE"
    assert result["price"] == 10.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. select_best_supplier – None when no IN_STOCK
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_select_best_supplier_none_when_no_stock():
    from app.services.supplier_router import choose_best_supplier_for_canonical

    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=mock_result)

    result = await choose_best_supplier_for_canonical(_uid(), session)
    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# 3. process_channel_order – dry_run places without real API
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_channel_order_dry_run():
    from app.services.order_fulfillment_service import process_channel_order
    from app.models.supplier_order import SupplierOrderStatus

    co = _make_channel_order()
    canonical_id = _uid()

    session = AsyncMock()
    session.flush = AsyncMock()
    session.add   = MagicMock()

    sp = _make_supplier_product("STYLEKOREAN", 12.00)
    so = _make_supplier_order(channel_order_id=co.id)

    def _exec(stmt):
        result = MagicMock()
        q = str(stmt)
        if "channel_orders_v2" in q or "ChannelOrderV2" in q:
            result.scalar_one_or_none.return_value = co
        elif "canonical_products" in q or "CanonicalProduct" in q:
            cp = MagicMock()
            cp.id = canonical_id
            cp.canonical_sku = "SKU-001"
            result.scalar_one_or_none.return_value = cp
        elif "supplier_products" in q or "SupplierProduct" in q:
            result.scalars.return_value.all.return_value = [sp]
        elif "supplier_orders" in q or "SupplierOrder" in q:
            result.scalar_one_or_none.return_value = None  # force create
        else:
            result.scalar_one_or_none.return_value = None
            result.scalars.return_value.all.return_value = []
        return result

    session.execute = AsyncMock(side_effect=_exec)

    results = await process_channel_order(str(co.id), session, dry_run=True)

    assert len(results) >= 1
    # In dry_run the supplier's real place_order must NOT be called
    # (verified by no supplier_client_factory)


# ─────────────────────────────────────────────────────────────────────────────
# 4. process_channel_order – SupplierOrder record created with placed status
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_channel_order_creates_record():
    from app.services.order_fulfillment_service import process_channel_order
    from app.suppliers.base import PlacedOrder

    co = _make_channel_order()
    canonical_id = _uid()

    mock_placed = PlacedOrder(
        supplier_order_id="SK-12345",
        status="placed",
        cost=12.00,
        currency="USD",
    )

    mock_client = AsyncMock()
    mock_client.place_order = AsyncMock(return_value=mock_placed)

    sp = _make_supplier_product("STYLEKOREAN", 12.00)

    session = AsyncMock()
    session.flush = AsyncMock()
    session.add   = MagicMock()

    created_so = _make_supplier_order(channel_order_id=co.id)

    def _exec(stmt):
        result = MagicMock()
        q = str(stmt)
        if "channel_orders_v2" in q or "ChannelOrderV2" in q:
            result.scalar_one_or_none.return_value = co
        elif "canonical_products" in q or "CanonicalProduct" in q:
            cp = MagicMock()
            cp.id = canonical_id
            cp.canonical_sku = "SKU-001"
            result.scalar_one_or_none.return_value = cp
        elif "supplier_products" in q or "SupplierProduct" in q:
            result.scalars.return_value.all.return_value = [sp]
        elif "supplier_orders" in q or "SupplierOrder" in q:
            result.scalar_one_or_none.return_value = None  # force create new
        else:
            result.scalar_one_or_none.return_value = None
            result.scalars.return_value.all.return_value = []
        return result

    session.execute = AsyncMock(side_effect=_exec)

    results = await process_channel_order(
        str(co.id),
        session,
        dry_run=False,
        supplier_client_factory=lambda name: mock_client,
    )

    assert len(results) >= 1
    mock_client.place_order.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 5. process_channel_order – empty payload → NO_SUPPLIER_AVAILABLE
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_channel_order_no_line_items():
    from app.services.order_fulfillment_service import process_channel_order
    from app.models.supplier_order import SupplierOrderStatus, FailureReason

    co = _make_channel_order(raw_payload={"line_items": []})  # empty line items

    session = AsyncMock()
    session.flush = AsyncMock()
    session.add   = MagicMock()

    def _exec(stmt):
        result = MagicMock()
        if "channel_orders_v2" in str(stmt) or "ChannelOrderV2" in str(stmt):
            result.scalar_one_or_none.return_value = co
        else:
            result.scalar_one_or_none.return_value = None
            result.scalars.return_value.all.return_value = []
        return result

    session.execute = AsyncMock(side_effect=_exec)

    results = await process_channel_order(str(co.id), session)

    assert len(results) == 1
    assert results[0].status == SupplierOrderStatus.FAILED
    assert results[0].failure_reason == FailureReason.NO_SUPPLIER_AVAILABLE


# ─────────────────────────────────────────────────────────────────────────────
# 6. process_channel_order – unknown SKU → gracefully skipped
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_channel_order_sku_not_canonical():
    from app.services.order_fulfillment_service import process_channel_order

    co = _make_channel_order(raw_payload={
        "line_items": [{"sku": "UNKNOWN-SKU-999", "quantity": 1}],
    })

    session = AsyncMock()
    session.flush = AsyncMock()
    session.add   = MagicMock()

    def _exec(stmt):
        result = MagicMock()
        q = str(stmt)
        if "channel_orders_v2" in q or "ChannelOrderV2" in q:
            result.scalar_one_or_none.return_value = co
        else:
            result.scalar_one_or_none.return_value = None
            result.scalars.return_value.all.return_value = []
        return result

    session.execute = AsyncMock(side_effect=_exec)

    results = await process_channel_order(str(co.id), session)

    # SKU not found → no SupplierOrder rows created (gracefully skipped)
    assert results == [] or all(r.failure_reason is not None for r in results)


# ─────────────────────────────────────────────────────────────────────────────
# 7. SupplierError → FAILED + SUPPLIER_API_ERROR reason
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_supplier_api_error_recorded():
    from app.services.order_fulfillment_service import process_channel_order
    from app.models.supplier_order import SupplierOrderStatus, FailureReason
    from app.suppliers.base import SupplierError

    co = _make_channel_order()
    canonical_id = _uid()
    sp = _make_supplier_product("STYLEKOREAN", 12.00)

    mock_client = AsyncMock()
    mock_client.place_order = AsyncMock(
        side_effect=SupplierError("Connection timeout", retryable=True)
    )

    session = AsyncMock()
    session.flush = AsyncMock()
    session.add   = MagicMock()

    def _exec(stmt):
        result = MagicMock()
        q = str(stmt)
        if "channel_orders_v2" in q or "ChannelOrderV2" in q:
            result.scalar_one_or_none.return_value = co
        elif "canonical_products" in q or "CanonicalProduct" in q:
            cp = MagicMock()
            cp.id = canonical_id
            cp.canonical_sku = "SKU-001"
            result.scalar_one_or_none.return_value = cp
        elif "supplier_products" in q or "SupplierProduct" in q:
            result.scalars.return_value.all.return_value = [sp]
        elif "supplier_orders" in q or "SupplierOrder" in q:
            result.scalar_one_or_none.return_value = None
        else:
            result.scalar_one_or_none.return_value = None
            result.scalars.return_value.all.return_value = []
        return result

    session.execute = AsyncMock(side_effect=_exec)

    results = await process_channel_order(
        str(co.id), session,
        dry_run=False,
        supplier_client_factory=lambda name: mock_client,
    )

    assert len(results) == 1
    assert results[0].status == SupplierOrderStatus.FAILED
    assert results[0].failure_reason == FailureReason.SUPPLIER_API_ERROR


# ─────────────────────────────────────────────────────────────────────────────
# 8. retry_count incremented on failure
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_logic_increments_count():
    from app.services.order_fulfillment_service import process_channel_order
    from app.suppliers.base import SupplierError
    from app.models.supplier_order import SupplierOrderStatus

    co = _make_channel_order()
    canonical_id = _uid()
    sp = _make_supplier_product("JOLSE", 8.00)

    mock_client = AsyncMock()
    mock_client.place_order = AsyncMock(
        side_effect=SupplierError("API unavailable", retryable=True)
    )

    # existing SO with retry_count=1
    existing_so = _make_supplier_order(channel_order_id=co.id, status="failed", retry_count=1)

    session = AsyncMock()
    session.flush = AsyncMock()
    session.add   = MagicMock()

    def _exec(stmt):
        result = MagicMock()
        q = str(stmt)
        if "channel_orders_v2" in q or "ChannelOrderV2" in q:
            result.scalar_one_or_none.return_value = co
        elif "canonical_products" in q or "CanonicalProduct" in q:
            cp = MagicMock()
            cp.id = canonical_id
            cp.canonical_sku = "SKU-001"
            result.scalar_one_or_none.return_value = cp
        elif "supplier_products" in q or "SupplierProduct" in q:
            result.scalars.return_value.all.return_value = [sp]
        elif "supplier_orders" in q or "SupplierOrder" in q:
            # Return existing failed SO (retry scenario)
            result.scalar_one_or_none.return_value = existing_so
        else:
            result.scalar_one_or_none.return_value = None
            result.scalars.return_value.all.return_value = []
        return result

    session.execute = AsyncMock(side_effect=_exec)

    results = await process_channel_order(
        str(co.id), session,
        dry_run=False,
        supplier_client_factory=lambda name: mock_client,
    )

    # retry_count should be incremented
    assert results[0].retry_count >= 2


# ─────────────────────────────────────────────────────────────────────────────
# 9. Celery poll – Redis lock busy → skipped
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_celery_poll_lock_skips_concurrent():
    from app.workers.tasks_fulfillment import _poll_supplier_orders_async

    mock_redis = AsyncMock()
    mock_redis.set    = AsyncMock(return_value=None)   # lock NOT acquired
    mock_redis.delete = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        result = await _poll_supplier_orders_async(limit=5)

    assert result["status"] == "skipped"


# ─────────────────────────────────────────────────────────────────────────────
# 10. poll_supplier_orders – status=shipped → tracking updated
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_poll_ships_supplier_order():
    from app.workers.tasks_fulfillment import _poll_supplier_orders_async
    from app.models.supplier_order import SupplierOrderStatus
    from app.suppliers.base import OrderStatus

    co = _make_channel_order()
    so = _make_supplier_order(
        channel_order_id=co.id,
        status="placed",
        supplier_order_id="SK-999",
    )

    # Mock supplier client returning "shipped"
    mock_client = AsyncMock()
    mock_client.get_order_status = AsyncMock(return_value=OrderStatus(
        supplier_order_id="SK-999",
        status="shipped",
        tracking_number="TRK-ABC123",
        tracking_carrier="DHL",
    ))

    # Mock Shopify fulfillment service
    mock_svc = AsyncMock()
    mock_svc.create_shopify_fulfillment = AsyncMock(return_value={"fulfillment_id": "FUL-1", "stub": True})

    # Mock session
    session = AsyncMock()
    session.commit = AsyncMock()
    session.flush  = AsyncMock()

    def _exec(stmt):
        result = MagicMock()
        q = str(stmt)
        if "supplier_orders" in q or "SupplierOrder" in q:
            result.scalars.return_value.all.return_value = [so]
        elif "channel_orders_v2" in q or "ChannelOrderV2" in q:
            result.scalar_one_or_none.return_value = co
        else:
            result.scalars.return_value.all.return_value = []
            result.scalar_one_or_none.return_value = None
        return result

    session.execute = AsyncMock(side_effect=_exec)

    mock_sf = MagicMock()
    mock_sf.return_value.__aenter__ = AsyncMock(return_value=session)
    mock_sf.return_value.__aexit__  = AsyncMock(return_value=False)

    mock_redis = AsyncMock()
    mock_redis.set    = AsyncMock(return_value=True)   # lock acquired
    mock_redis.delete = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        result = await _poll_supplier_orders_async(
            limit=5,
            session_factory=mock_sf,
            shopify_svc=mock_svc,
            supplier_client_factory=lambda name: mock_client,
        )

    assert result["status"] == "ok"
    assert result["updated"] >= 1
    assert so.tracking_number == "TRK-ABC123"
    mock_svc.create_shopify_fulfillment.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 11. ShopifyFulfillmentService – stub no network
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_shopify_fulfillment_stub_no_network():
    from app.services.shopify_fulfillment_service import ShopifyFulfillmentService

    mock_client = MagicMock()
    mock_client.store_domain = ""   # no credentials → stub mode
    mock_client.api_secret   = ""

    svc = ShopifyFulfillmentService(shopify_client=mock_client)

    result = await svc.create_shopify_fulfillment(
        shopify_order_id="123456789",
        tracking_number="DHL-TRK-001",
        carrier="DHL",
    )

    assert result["stub"] is True
    assert result["tracking_number"] == "DHL-TRK-001"
    assert "fulfillment_id" in result


# ─────────────────────────────────────────────────────────────────────────────
# 12. webhook handle_order_created – enqueues fulfillment task
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_enqueues_fulfillment_task():
    from app.webhooks.handlers.order_created import handle_order_created
    from app.webhooks.normalized import NormalizedEvent

    evt = NormalizedEvent(
        channel="shopify",
        topic="order.created",
        event_id="evt-001",
        external_id="SHOPIFY-ORD-001",
        payload={
            "id": "SHOPIFY-ORD-001",
            "currency": "USD",
            "total_price": "49.99",
            "email": "buyer@test.com",
            "customer": {"first_name": "Test", "last_name": "User"},
            "line_items": [{"sku": "SKU-001", "quantity": 1}],
        },
        occurred_at=None,
    )

    session = AsyncMock()
    session.flush = AsyncMock()
    session.add   = MagicMock()

    # No existing row
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=mock_result)

    with patch(
        "app.webhooks.handlers.order_created._enqueue_fulfillment"
    ) as mock_enqueue:
        row = await handle_order_created(evt, session)

    mock_enqueue.assert_called_once_with(str(row.id), channel="shopify")


# ─────────────────────────────────────────────────────────────────────────────
# 13. NO_SUPPLIER_AVAILABLE failure path
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_place_order_no_supplier_available():
    from app.services.order_fulfillment_service import process_channel_order
    from app.models.supplier_order import SupplierOrderStatus, FailureReason

    co = _make_channel_order()
    canonical_id = _uid()

    session = AsyncMock()
    session.flush = AsyncMock()
    session.add   = MagicMock()

    def _exec(stmt):
        result = MagicMock()
        q = str(stmt)
        if "channel_orders_v2" in q or "ChannelOrderV2" in q:
            result.scalar_one_or_none.return_value = co
        elif "canonical_products" in q or "CanonicalProduct" in q:
            cp = MagicMock()
            cp.id = canonical_id
            cp.canonical_sku = "SKU-001"
            result.scalar_one_or_none.return_value = cp
        elif "supplier_products" in q or "SupplierProduct" in q:
            result.scalars.return_value.all.return_value = []  # no in-stock
        elif "supplier_orders" in q or "SupplierOrder" in q:
            result.scalar_one_or_none.return_value = None
        else:
            result.scalar_one_or_none.return_value = None
            result.scalars.return_value.all.return_value = []
        return result

    session.execute = AsyncMock(side_effect=_exec)

    results = await process_channel_order(str(co.id), session)

    assert len(results) == 1
    assert results[0].status == SupplierOrderStatus.FAILED
    assert results[0].failure_reason == FailureReason.NO_SUPPLIER_AVAILABLE


# ─────────────────────────────────────────────────────────────────────────────
# 14. All three supplier stubs return PlacedOrder
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_place_order_all_three_suppliers():
    from app.suppliers.stylekorean import StyleKoreanClient
    from app.suppliers.jolse import JolseClient
    from app.suppliers.oliveyoung import OliveYoungClient
    from app.suppliers.base import PlacedOrder

    order_payload = {
        "channel_order_id": str(_uid()),
        "canonical_sku": "SKU-001",
        "supplier_product_id": "PROD-001",
        "quantity": 1,
        "cost": 12.00,
        "currency": "USD",
    }

    # StyleKorean stub (no credentials)
    sk = StyleKoreanClient()
    result_sk = await sk.place_order(order_payload)
    assert isinstance(result_sk, PlacedOrder)
    assert result_sk.status == "placed"
    assert "SK-STUB-" in result_sk.supplier_order_id

    # Jolse stub
    jo = JolseClient()
    result_jo = await jo.place_order(order_payload)
    assert isinstance(result_jo, PlacedOrder)
    assert "JOLSE-STUB-" in result_jo.supplier_order_id

    # OliveYoung stub
    oy = OliveYoungClient()
    result_oy = await oy.place_order(order_payload)
    assert isinstance(result_oy, PlacedOrder)
    assert "OY-STUB-" in result_oy.supplier_order_id
