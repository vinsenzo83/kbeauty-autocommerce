from __future__ import annotations

"""
tests/test_inventory_sync.py
──────────────────────────────
Sprint 6 — Inventory Sync System tests.

All tests are mock-only:
  - No Playwright (supplier crawl is stubbed via ``fetch_fn``).
  - No Shopify network calls (ShopifyProductService is replaced with an AsyncMock).
  - In-memory SQLite via aiosqlite (same pattern as Sprint 4 tests).

Test coverage
-------------
1.  _normalise_price           — price string parsing helper
2.  _detect_out_of_stock       — OOS detection with mock page
3.  _extract_price             — price extraction with mock page
4.  fetch_inventory            — end-to-end crawler with injected mock page
5.  check_supplier_inventory   — service wrapper
6.  update_product_inventory / price_change  — DB + Shopify price update
7.  update_product_inventory / out_of_stock  — DB + Shopify zero-inventory
8.  update_product_inventory / already_oos   — no duplicate Shopify call
9.  update_product_inventory / no_shopify_id — skips Shopify calls gracefully
10. sync_inventory Celery task              — end-to-end task with mocks
11. /admin/inventory/stale endpoint         — stale product list
"""

from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.product import Base as ProductBase, Product

# ── In-memory SQLite DB ───────────────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine  = create_async_engine(TEST_DB_URL, echo=False)
TestSession  = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture(autouse=True)
async def create_tables():
    async with test_engine.begin() as conn:
        await conn.run_sync(ProductBase.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(ProductBase.metadata.drop_all)


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as s:
        yield s


# ── Sample ORM product helpers ────────────────────────────────────────────────

def _make_product(
    *,
    stock_status: str = "IN_STOCK",
    price: str = "15.00",
    last_seen_price: str | None = None,
    shopify_product_id: str | None = "gid://shopify/Product/111",
    shopify_variant_id: str | None = "gid://shopify/ProductVariant/222",
    supplier_product_url: str | None = None,
) -> Product:
    uid = uuid4().hex[:6]
    return Product(
        id                   = uuid4(),
        supplier             = "stylekorean",
        supplier_product_id  = f"test-product-{uid}",
        supplier_product_url = supplier_product_url or f"https://www.stylekorean.com/products/test-{uid}",
        name                 = "Test Cream",
        brand                = "COSRX",
        price                = price,
        currency             = "USD",
        stock_status         = stock_status,
        last_seen_price      = last_seen_price,
        shopify_product_id   = shopify_product_id,
        shopify_variant_id   = shopify_variant_id,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 1. _normalise_price
# ═════════════════════════════════════════════════════════════════════════════

def test_normalise_price_simple():
    from app.crawlers.stylekorean_inventory import _normalise_price
    assert _normalise_price("$12.50") == pytest.approx(12.50)


def test_normalise_price_with_currency():
    from app.crawlers.stylekorean_inventory import _normalise_price
    assert _normalise_price("USD 25.00") == pytest.approx(25.00)


def test_normalise_price_thousands():
    from app.crawlers.stylekorean_inventory import _normalise_price
    assert _normalise_price("12,500") == pytest.approx(12500.0)


def test_normalise_price_none():
    from app.crawlers.stylekorean_inventory import _normalise_price
    assert _normalise_price("") is None
    assert _normalise_price("N/A") is None


def test_normalise_price_decimal():
    from app.crawlers.stylekorean_inventory import _normalise_price
    assert _normalise_price("9.99") == pytest.approx(9.99)


# ═════════════════════════════════════════════════════════════════════════════
# 2. _detect_out_of_stock (mock page)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_detect_oos_presence_selector():
    """A presence-only OOS selector returns True."""
    from app.crawlers.stylekorean_inventory import _detect_out_of_stock

    mock_page = AsyncMock()
    # simulate .sold-out element being present
    async def _query_selector(sel):
        if sel == ".sold-out":
            return MagicMock()  # truthy element
        return None

    mock_page.query_selector = _query_selector
    assert await _detect_out_of_stock(mock_page) is True


@pytest.mark.anyio
async def test_detect_oos_text_keyword():
    """A stock-status element containing 'out of stock' text returns True."""
    from app.crawlers.stylekorean_inventory import _detect_out_of_stock

    mock_page = AsyncMock()

    async def _query_selector(sel):
        if sel == ".stock-status":
            el = AsyncMock()
            el.inner_text = AsyncMock(return_value="Out of Stock")
            return el
        return None

    mock_page.query_selector = _query_selector
    assert await _detect_out_of_stock(mock_page) is True


@pytest.mark.anyio
async def test_detect_in_stock():
    """Page with no OOS signals returns False."""
    from app.crawlers.stylekorean_inventory import _detect_out_of_stock

    mock_page = AsyncMock()
    mock_page.query_selector = AsyncMock(return_value=None)
    mock_page.inner_text = AsyncMock(return_value="Add to Cart — Available!")

    assert await _detect_out_of_stock(mock_page) is False


# ═════════════════════════════════════════════════════════════════════════════
# 3. _extract_price (mock page)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_extract_price_content_attr():
    """itemprop='price' content attribute is used first."""
    from app.crawlers.stylekorean_inventory import _extract_price

    mock_el = AsyncMock()
    mock_el.get_attribute = AsyncMock(return_value="14.99")
    mock_el.inner_text    = AsyncMock(return_value="$14.99")

    mock_page = AsyncMock()
    async def _query_selector(sel):
        if "[itemprop='price']" in sel:
            return mock_el
        return None

    mock_page.query_selector = _query_selector
    result = await _extract_price(mock_page)
    assert result == pytest.approx(14.99)


@pytest.mark.anyio
async def test_extract_price_inner_text_fallback():
    """Falls back to inner text when no content attribute present."""
    from app.crawlers.stylekorean_inventory import _extract_price

    mock_el = AsyncMock()
    mock_el.get_attribute = AsyncMock(return_value=None)
    mock_el.inner_text    = AsyncMock(return_value="$18.00")

    mock_page = AsyncMock()
    async def _query_selector(sel):
        if "[itemprop='price']" in sel:
            return mock_el
        return None

    mock_page.query_selector = _query_selector
    result = await _extract_price(mock_page)
    assert result == pytest.approx(18.00)


@pytest.mark.anyio
async def test_extract_price_none_when_no_selector():
    """Returns None when no price selector matches."""
    from app.crawlers.stylekorean_inventory import _extract_price

    mock_page = AsyncMock()
    mock_page.query_selector = AsyncMock(return_value=None)
    assert await _extract_price(mock_page) is None


# ═════════════════════════════════════════════════════════════════════════════
# 4. fetch_inventory — end-to-end with injected page
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_fetch_inventory_in_stock():
    from app.crawlers.stylekorean_inventory import fetch_inventory

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    # No OOS selectors
    mock_page.query_selector = AsyncMock(return_value=None)
    mock_page.inner_text = AsyncMock(return_value="Add to Cart")

    # Price via itemprop
    price_el = AsyncMock()
    price_el.get_attribute = AsyncMock(return_value="12.50")
    price_el.inner_text    = AsyncMock(return_value="$12.50")

    async def _qs(sel):
        if "[itemprop='price']" in sel:
            return price_el
        return None

    mock_page.query_selector = _qs

    result = await fetch_inventory("https://example.com/product", page=mock_page)
    assert result["in_stock"] is True
    assert result["price"] == pytest.approx(12.50)


@pytest.mark.anyio
async def test_fetch_inventory_out_of_stock():
    from app.crawlers.stylekorean_inventory import fetch_inventory

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()

    async def _qs(sel):
        if sel == ".sold-out":
            return MagicMock()  # OOS presence
        return None

    mock_page.query_selector = _qs

    result = await fetch_inventory("https://example.com/product", page=mock_page)
    assert result["in_stock"] is False


# ═════════════════════════════════════════════════════════════════════════════
# 5. check_supplier_inventory
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_check_supplier_inventory_calls_fetch_fn():
    from app.services.inventory_service import check_supplier_inventory

    product = _make_product()
    fake_result = {"in_stock": True, "price": 9.99}

    async def _fake_fetch(url):
        assert url == product.supplier_product_url
        return fake_result

    result = await check_supplier_inventory(product, fetch_fn=_fake_fetch)
    assert result == fake_result


# ═════════════════════════════════════════════════════════════════════════════
# 6. update_product_inventory — price change
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_price_change_updates_db_and_shopify(session: AsyncSession):
    from app.services.inventory_service import update_product_inventory

    product = _make_product(price="15.00", last_seen_price="15.00")
    session.add(product)
    await session.flush()

    mock_shopify = AsyncMock()
    mock_shopify.update_variant_price = AsyncMock(return_value=True)
    mock_shopify.set_inventory_zero   = AsyncMock(return_value=True)

    inventory_data = {"in_stock": True, "price": 13.00}  # price dropped

    result = await update_product_inventory(
        product, inventory_data, session, shopify_svc=mock_shopify
    )

    assert result["price_changed"] is True
    assert result["shopify_repriced"] is True
    assert result["stock_changed"] is False
    assert result["shopify_zeroed"] is False

    # DB updated
    assert float(product.last_seen_price) == pytest.approx(13.00)
    assert product.last_checked_at is not None

    # Shopify called
    mock_shopify.update_variant_price.assert_awaited_once()
    mock_shopify.set_inventory_zero.assert_not_awaited()


@pytest.mark.anyio
async def test_price_unchanged_no_shopify_call(session: AsyncSession):
    from app.services.inventory_service import update_product_inventory

    product = _make_product(price="15.00", last_seen_price="15.00")
    session.add(product)
    await session.flush()

    mock_shopify = AsyncMock()

    inventory_data = {"in_stock": True, "price": 15.001}  # within 0.5% threshold

    result = await update_product_inventory(
        product, inventory_data, session, shopify_svc=mock_shopify
    )

    assert result["price_changed"] is False
    assert result["shopify_repriced"] is False
    mock_shopify.update_variant_price.assert_not_awaited()


# ═════════════════════════════════════════════════════════════════════════════
# 7. update_product_inventory — out-of-stock
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_out_of_stock_zeroes_shopify(session: AsyncSession):
    from app.services.inventory_service import update_product_inventory

    product = _make_product(stock_status="IN_STOCK")
    session.add(product)
    await session.flush()

    mock_shopify = AsyncMock()
    mock_shopify.set_inventory_zero   = AsyncMock(return_value=True)
    mock_shopify.update_variant_price = AsyncMock(return_value=True)

    inventory_data = {"in_stock": False, "price": 15.00}

    result = await update_product_inventory(
        product, inventory_data, session, shopify_svc=mock_shopify
    )

    assert result["stock_changed"] is True
    assert result["shopify_zeroed"] is True
    assert product.stock_status == "OUT_OF_STOCK"

    mock_shopify.set_inventory_zero.assert_awaited_once()


@pytest.mark.anyio
async def test_already_oos_no_duplicate_shopify_call(session: AsyncSession):
    """If already OUT_OF_STOCK, stock_changed=False → no Shopify zero call."""
    from app.services.inventory_service import update_product_inventory

    product = _make_product(stock_status="OUT_OF_STOCK")
    session.add(product)
    await session.flush()

    mock_shopify = AsyncMock()
    mock_shopify.set_inventory_zero   = AsyncMock(return_value=True)
    mock_shopify.update_variant_price = AsyncMock(return_value=True)

    inventory_data = {"in_stock": False, "price": None}

    result = await update_product_inventory(
        product, inventory_data, session, shopify_svc=mock_shopify
    )

    assert result["stock_changed"] is False
    assert result["shopify_zeroed"] is False
    mock_shopify.set_inventory_zero.assert_not_awaited()


# ═════════════════════════════════════════════════════════════════════════════
# 8. update_product_inventory — no shopify_product_id
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_no_shopify_id_skips_shopify(session: AsyncSession):
    from app.services.inventory_service import update_product_inventory

    product = _make_product(stock_status="IN_STOCK", shopify_product_id=None)
    session.add(product)
    await session.flush()

    mock_shopify = AsyncMock()
    mock_shopify.set_inventory_zero   = AsyncMock(return_value=True)
    mock_shopify.update_variant_price = AsyncMock(return_value=True)

    inventory_data = {"in_stock": False, "price": 10.00}

    result = await update_product_inventory(
        product, inventory_data, session, shopify_svc=mock_shopify
    )

    # DB still updated
    assert product.stock_status == "OUT_OF_STOCK"
    # Shopify not called because shopify_product_id is None
    mock_shopify.set_inventory_zero.assert_not_awaited()
    mock_shopify.update_variant_price.assert_not_awaited()


# ═════════════════════════════════════════════════════════════════════════════
# 9. ShopifyProductService.set_inventory_zero + update_variant_price
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_shopify_set_inventory_zero_stub():
    """set_inventory_zero returns False in stub mode (no credentials)."""
    from app.services.shopify_product_service import ShopifyProductService
    from app.services.shopify_service import ShopifyClient

    stub_client = ShopifyClient(store_domain="", api_secret="")
    svc = ShopifyProductService(client=stub_client)

    product = _make_product(shopify_product_id="111")
    # stub _get returns {} → no variants → returns False
    result = await svc.set_inventory_zero(product)
    assert result is False  # no variant_id found in stub response


@pytest.mark.anyio
async def test_shopify_update_variant_price_stub():
    """update_variant_price returns False in stub mode (no credentials)."""
    from app.services.shopify_product_service import ShopifyProductService
    from app.services.shopify_service import ShopifyClient

    stub_client = ShopifyClient(store_domain="", api_secret="")
    svc = ShopifyProductService(client=stub_client)

    product = _make_product(shopify_product_id="111")
    result = await svc.update_variant_price(product, 9.99)
    assert result is False


@pytest.mark.anyio
async def test_shopify_set_inventory_zero_with_variant():
    """set_inventory_zero calls _put with correct body when variant_id cached."""
    from app.services.shopify_product_service import ShopifyProductService
    from app.services.shopify_service import ShopifyClient

    mock_client = MagicMock(spec=ShopifyClient)
    mock_client._get = AsyncMock(return_value={})  # not used (variant_id cached)
    mock_client._put = AsyncMock(return_value={"variant": {"id": "222"}})

    svc = ShopifyProductService(client=mock_client)

    product = _make_product(shopify_product_id="111", shopify_variant_id="222")
    result = await svc.set_inventory_zero(product)

    assert result is True
    call_args = mock_client._put.call_args
    assert call_args[0][0] == "/variants/222.json"
    payload = call_args[0][1]["variant"]
    assert payload["inventory_policy"] == "deny"


@pytest.mark.anyio
async def test_shopify_update_variant_price_with_variant():
    """update_variant_price calls _put with correct price string."""
    from app.services.shopify_product_service import ShopifyProductService
    from app.services.shopify_service import ShopifyClient

    mock_client = MagicMock(spec=ShopifyClient)
    mock_client._get = AsyncMock(return_value={})
    mock_client._put = AsyncMock(return_value={"variant": {"id": "222"}})

    svc = ShopifyProductService(client=mock_client)

    product = _make_product(shopify_product_id="111", shopify_variant_id="222")
    result = await svc.update_variant_price(product, 11.99)

    assert result is True
    call_args = mock_client._put.call_args
    assert call_args[0][0] == "/variants/222.json"
    payload = call_args[0][1]["variant"]
    assert payload["price"] == "11.99"


# ═════════════════════════════════════════════════════════════════════════════
# 10. sync_inventory Celery task
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_sync_inventory_task_end_to_end():
    """
    _run_sync processes all products, updates DB, calls Shopify correctly.

    Uses a patched AsyncSessionLocal so it works with SQLite.
    """
    from app.workers.tasks_inventory import _run_sync

    # Each product gets a unique URL so _fake_fetch can distinguish them
    url_in  = "https://www.stylekorean.com/products/item-in-stock"
    url_oos = "https://www.stylekorean.com/products/item-out-of-stock"
    url_pc  = "https://www.stylekorean.com/products/item-price-change"

    product_in  = _make_product(stock_status="IN_STOCK",  last_seen_price="15.00", supplier_product_url=url_in)
    product_oos = _make_product(stock_status="IN_STOCK",  last_seen_price="20.00", supplier_product_url=url_oos)
    product_pc  = _make_product(stock_status="IN_STOCK",  last_seen_price="10.00", supplier_product_url=url_pc)

    async with TestSession() as s:
        s.add_all([product_in, product_oos, product_pc])
        await s.commit()

    # Supplier responses per URL
    _responses: dict[str, dict] = {
        url_in:  {"in_stock": True,  "price": 15.00},   # no change
        url_oos: {"in_stock": False, "price": 20.00},   # OOS transition
        url_pc:  {"in_stock": True,  "price": 13.00},   # price drop
    }

    async def _fake_fetch(url: str):
        return _responses.get(url, {"in_stock": True, "price": None})

    mock_shopify = AsyncMock()
    mock_shopify.set_inventory_zero   = AsyncMock(return_value=True)
    mock_shopify.update_variant_price = AsyncMock(return_value=True)

    # Patch AsyncSessionLocal to use our test session factory
    with patch(
        "app.workers.tasks_inventory.AsyncSessionLocal",
        TestSession,
    ):
        result = await _run_sync(
            fetch_fn=_fake_fetch,
            shopify_svc=mock_shopify,
        )

    assert result["total"] == 3
    assert result["errors"] == 0
    assert result["updated"] >= 2  # OOS + price-change products triggered updates

    # Verify Shopify calls
    mock_shopify.set_inventory_zero.assert_awaited_once()      # product_oos
    mock_shopify.update_variant_price.assert_awaited_once()    # product_pc


# ═════════════════════════════════════════════════════════════════════════════
# 11. Celery beat schedule registration
# ═════════════════════════════════════════════════════════════════════════════

def test_celery_beat_schedule_contains_inventory_task():
    from app.workers.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    task_names = [entry["task"] for entry in schedule.values()]
    assert "workers.tasks_inventory.sync_inventory" in task_names


def test_inventory_sync_interval_is_30_minutes():
    from app.workers.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    entry = schedule.get("sync-inventory-every-30m", {})
    assert entry.get("schedule") == 1800  # 30 min


# ═════════════════════════════════════════════════════════════════════════════
# 12. /admin/inventory/stale endpoint
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_stale_inventory_endpoint():
    """GET /admin/inventory/stale returns products not checked in 24 h."""
    import os
    os.environ.setdefault("DATABASE_URL_TEST", TEST_DB_URL)

    from app.main import app
    from app.db.session import get_db
    from app.services.auth_service import get_current_user, CurrentUser

    # Override DB dependency
    async def _override_db():
        async with TestSession() as s:
            yield s

    # Override auth: return a VIEWER user unconditionally
    def _override_auth():
        return CurrentUser(sub="test-user", role="VIEWER")

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_auth

    # Seed: one stale product, one recently-checked product
    stale = _make_product(shopify_product_id=None)
    stale.last_checked_at = None  # never checked → stale

    recent = _make_product(shopify_product_id=None)
    recent.last_checked_at = datetime.now(timezone.utc) - timedelta(hours=1)

    async with TestSession() as s:
        s.add_all([stale, recent])
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/inventory/stale")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "stale_count" in data
    assert "items" in data
    # The stale product (last_checked_at=None) must be in the list
    ids = {item["id"] for item in data["items"]}
    assert str(stale.id) in ids
    # The recently-checked product should NOT be in the list
    assert str(recent.id) not in ids

    app.dependency_overrides.clear()
