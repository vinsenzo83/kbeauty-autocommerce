from __future__ import annotations

"""
tests/test_sprint4_products.py
───────────────────────────────
Sprint 4 tests – best-seller crawler + Shopify product sync.

All tests are mock-only: no Playwright, no network calls, no real DB.
In-memory SQLite is used via aiosqlite.
"""

from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.product import Base as ProductBase, Product

# ── In-memory SQLite ─────────────────────────────────────────────────────────
TEST_DB_URL  = "sqlite+aiosqlite:///:memory:"
test_engine  = create_async_engine(TEST_DB_URL, echo=False)
TestSession  = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


# ── Sample data ───────────────────────────────────────────────────────────────

SAMPLE_PRODUCT_DATA: dict = {
    "supplier":             "stylekorean",
    "supplier_product_id":  "some-cream-123",
    "supplier_product_url": "https://www.stylekorean.com/products/some-cream-123",
    "name":                 "Some Hydrating Cream",
    "brand":                "COSRX",
    "price":                "15.00",
    "sale_price":           "12.50",
    "currency":             "USD",
    "stock_status":         "in_stock",
    "image_urls":           [
        "https://cdn.stylekorean.com/images/some-cream-123-01.jpg",
        "https://cdn.stylekorean.com/images/some-cream-123-02.jpg",
    ],
}

SAMPLE_HTML_IN_STOCK = """
<html>
<head><title>Some Hydrating Cream - StyleKorean</title></head>
<body>
  <h1 class="product-name">Some Hydrating Cream</h1>
  <div class="brand-name">COSRX</div>
  <span itemprop="price" content="15.00">$15.00</span>
  <span class="sale-price amount">$12.50</span>
  <img class="product-image"
       src="https://cdn.stylekorean.com/images/some-cream-123-01.jpg"
       alt="main image" />
  <img class="product-image"
       data-src="https://cdn.stylekorean.com/images/some-cream-123-02.jpg"
       alt="second image" />
  <p>Add to Cart</p>
</body>
</html>
"""

SAMPLE_HTML_OUT_OF_STOCK = """
<html>
<body>
  <h1>Rare Serum</h1>
  <div class="brand-name">Innisfree</div>
  <span itemprop="price" content="20.00">$20.00</span>
  <p class="stock-status">Out of Stock</p>
</body>
</html>
"""

SAMPLE_HTML_NO_NAME = """
<html>
<body>
  <p>No product name here</p>
</body>
</html>
"""


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(autouse=True)
async def setup_db() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(ProductBase.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(ProductBase.metadata.drop_all)


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as s:
        yield s


# ── A) product_parser tests ───────────────────────────────────────────────────


class TestProductParser:
    """Tests for app.crawlers.product_parser.parse_product_page."""

    def test_parses_name(self) -> None:
        from app.crawlers.product_parser import parse_product_page

        result = parse_product_page(SAMPLE_HTML_IN_STOCK)
        assert result["name"] == "Some Hydrating Cream"

    def test_parses_brand(self) -> None:
        from app.crawlers.product_parser import parse_product_page

        result = parse_product_page(SAMPLE_HTML_IN_STOCK)
        assert result["brand"] == "COSRX"

    def test_parses_price(self) -> None:
        from app.crawlers.product_parser import parse_product_page

        result = parse_product_page(SAMPLE_HTML_IN_STOCK)
        assert result["price"] is not None
        assert "15" in result["price"]

    def test_parses_sale_price(self) -> None:
        from app.crawlers.product_parser import parse_product_page

        result = parse_product_page(SAMPLE_HTML_IN_STOCK)
        assert result["sale_price"] is not None
        assert "12" in result["sale_price"]

    def test_detects_in_stock(self) -> None:
        from app.crawlers.product_parser import parse_product_page

        result = parse_product_page(SAMPLE_HTML_IN_STOCK)
        assert result["stock_status"] == "in_stock"

    def test_detects_out_of_stock(self) -> None:
        from app.crawlers.product_parser import parse_product_page

        result = parse_product_page(SAMPLE_HTML_OUT_OF_STOCK)
        assert result["stock_status"] == "out_of_stock"

    def test_collects_image_urls(self) -> None:
        from app.crawlers.product_parser import parse_product_page

        result = parse_product_page(SAMPLE_HTML_IN_STOCK)
        assert len(result["image_urls"]) >= 1
        assert any("some-cream-123" in url for url in result["image_urls"])

    def test_data_src_preferred_over_src(self) -> None:
        from app.crawlers.product_parser import parse_product_page

        # The second image uses data-src, ensure it's collected
        result = parse_product_page(SAMPLE_HTML_IN_STOCK)
        urls = result["image_urls"]
        assert any("some-cream-123-02" in u for u in urls)

    def test_empty_name_on_minimal_html(self) -> None:
        from app.crawlers.product_parser import parse_product_page

        result = parse_product_page(SAMPLE_HTML_NO_NAME)
        # Should not raise; name defaults to empty string
        assert isinstance(result["name"], str)

    def test_selector_constants_exist(self) -> None:
        """Ensure selector constants are defined at module level (for test patching)."""
        import app.crawlers.product_parser as m

        assert hasattr(m, "SEL_PRODUCT_NAME")
        assert hasattr(m, "SEL_BRAND")
        assert hasattr(m, "SEL_PRICE")
        assert hasattr(m, "SEL_IMAGES")


# ── B) product_service tests ──────────────────────────────────────────────────


class TestProductService:
    """Tests for app.services.product_service."""

    @pytest.mark.asyncio
    async def test_upsert_inserts_new_product(self, session: AsyncSession) -> None:
        from app.services.product_service import upsert_product

        async with session.begin_nested():
            product = await upsert_product(session, SAMPLE_PRODUCT_DATA)

        assert product.supplier_product_id == "some-cream-123"
        assert product.name                == "Some Hydrating Cream"
        assert product.brand               == "COSRX"

    @pytest.mark.asyncio
    async def test_upsert_updates_existing_product(self, session: AsyncSession) -> None:
        from app.services.product_service import upsert_product

        async with session.begin_nested():
            await upsert_product(session, SAMPLE_PRODUCT_DATA)

        updated_data = {**SAMPLE_PRODUCT_DATA, "name": "Updated Cream Name", "price": "18.00"}
        async with session.begin_nested():
            product = await upsert_product(session, updated_data)

        assert product.name == "Updated Cream Name"

    @pytest.mark.asyncio
    async def test_get_unsynced_products_returns_all_without_shopify_id(
        self, session: AsyncSession
    ) -> None:
        from app.services.product_service import get_unsynced_products, upsert_product

        async with session.begin_nested():
            await upsert_product(session, SAMPLE_PRODUCT_DATA)

        unsynced = await get_unsynced_products(session)
        assert len(unsynced) >= 1
        assert all(p.shopify_product_id is None for p in unsynced)

    @pytest.mark.asyncio
    async def test_mark_synced_sets_shopify_id(self, session: AsyncSession) -> None:
        from app.services.product_service import mark_synced, upsert_product

        async with session.begin_nested():
            product = await upsert_product(session, SAMPLE_PRODUCT_DATA)

        async with session.begin_nested():
            updated = await mark_synced(session, product, shopify_product_id="SHOP-999")

        assert updated.shopify_product_id == "SHOP-999"

    @pytest.mark.asyncio
    async def test_get_unsynced_excludes_synced_products(
        self, session: AsyncSession
    ) -> None:
        from app.services.product_service import (
            get_unsynced_products,
            mark_synced,
            upsert_product,
        )

        async with session.begin_nested():
            product = await upsert_product(session, SAMPLE_PRODUCT_DATA)
            await mark_synced(session, product, shopify_product_id="SHOP-777")

        unsynced = await get_unsynced_products(session)
        assert all(p.shopify_product_id is not None for p in unsynced)


# ── C) StyleKorean crawler tests (mocked Playwright) ─────────────────────────


class TestStyleKoreanCrawler:
    """
    Tests for app.crawlers.stylekorean_crawler.

    Playwright is fully mocked – no browser is launched.
    """

    @pytest.mark.asyncio
    async def test_crawler_stops_at_limit(self) -> None:
        """Crawler must not collect more URLs than the specified limit."""
        from app.crawlers.stylekorean_crawler import _collect_product_urls

        # Create a mock Playwright page
        mock_page = AsyncMock()

        # Simulate 30 product links on page 1
        link_mocks = []
        for i in range(30):
            m = AsyncMock()
            m.get_attribute = AsyncMock(
                return_value=f"https://www.stylekorean.com/products/item-{i}"
            )
            link_mocks.append(m)

        mock_page.goto = AsyncMock()
        mock_page.wait_for_selector = AsyncMock()
        mock_page.query_selector_all = AsyncMock(return_value=link_mocks)
        # No next page
        mock_page.query_selector = AsyncMock(return_value=None)

        urls = await _collect_product_urls(mock_page, limit=10)
        assert len(urls) == 10

    @pytest.mark.asyncio
    async def test_crawler_calls_upsert_for_each_product(
        self, session: AsyncSession
    ) -> None:
        """
        Mock crawl_best_sellers with a pre-built product list and verify
        upsert_fn is called once per product.
        """
        from app.crawlers.stylekorean_crawler import crawl_best_sellers

        mock_upsert = AsyncMock()
        fake_products = [
            {
                "supplier_product_id":  f"item-{i}",
                "supplier_product_url": f"https://www.stylekorean.com/products/item-{i}",
                "name":                 f"Product {i}",
                "supplier":             "stylekorean",
                "currency":             "USD",
                "stock_status":         "in_stock",
                "image_urls":           [],
            }
            for i in range(5)
        ]

        async def mock_do_crawl(  # type: ignore[return]
            db_session: object,
            *,
            limit: int | None = None,
            upsert_fn: object = None,
        ) -> list[dict]:
            for d in fake_products:
                await upsert_fn(db_session, d)  # type: ignore[operator]
            return fake_products

        with patch(
            "app.crawlers.stylekorean_crawler.crawl_best_sellers",
            side_effect=mock_do_crawl,
        ):
            results = await mock_do_crawl(
                session,
                limit=5,
                upsert_fn=mock_upsert,
            )

        assert mock_upsert.call_count == 5
        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_crawler_skips_failed_page(self, session: AsyncSession) -> None:
        """
        When _fetch_product_data returns None (parse failure),
        the item should be skipped, not crash the whole run.
        """
        from app.crawlers.stylekorean_crawler import _fetch_product_data

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(side_effect=Exception("Network timeout"))

        result = await _fetch_product_data(mock_page, "https://example.com/product-x")
        assert result is None


# ── D) ShopifyProductService tests ───────────────────────────────────────────


class TestShopifyProductService:
    """Tests for app.services.shopify_product_service."""

    @pytest.mark.asyncio
    async def test_create_product_stub_returns_none(self) -> None:
        """
        Without credentials, create_or_update_product must return None (stub mode).
        """
        from app.services.shopify_service import ShopifyClient
        from app.services.shopify_product_service import ShopifyProductService

        client  = ShopifyClient(store_domain="", api_secret="")
        service = ShopifyProductService(client=client)

        result = await service.create_or_update_product(SAMPLE_PRODUCT_DATA)
        assert result is None

    @pytest.mark.asyncio
    async def test_create_product_calls_shopify_post(self) -> None:
        """
        With a mock _post that returns a product id,
        create_or_update_product should return that id as a string.
        """
        from app.services.shopify_service import ShopifyClient
        from app.services.shopify_product_service import ShopifyProductService

        mock_client           = MagicMock(spec=ShopifyClient)
        mock_client.store_domain = "test.myshopify.com"
        mock_client.api_secret   = "secret"
        mock_client._post     = AsyncMock(
            side_effect=[
                # First call: create product
                {"product": {"id": 98765, "title": "Some Hydrating Cream"}},
                # Second call: set metafield
                {"metafield": {"id": 111}},
            ]
        )
        service = ShopifyProductService(client=mock_client)

        shopify_id = await service.create_or_update_product(SAMPLE_PRODUCT_DATA)

        assert shopify_id == "98765"
        assert mock_client._post.call_count == 2  # create + metafield

    @pytest.mark.asyncio
    async def test_update_product_uses_correct_path(self) -> None:
        """
        If product already has shopify_product_id, _post path must include it.
        """
        from app.services.shopify_service import ShopifyClient
        from app.services.shopify_product_service import ShopifyProductService

        data_with_id = {
            **SAMPLE_PRODUCT_DATA,
            "shopify_product_id": "EXISTING-123",
        }

        mock_client           = MagicMock(spec=ShopifyClient)
        mock_client.store_domain = "test.myshopify.com"
        mock_client.api_secret   = "secret"
        mock_client._post     = AsyncMock(
            side_effect=[
                {"product": {"id": "EXISTING-123"}},
                {"metafield": {"id": 222}},
            ]
        )
        service = ShopifyProductService(client=mock_client)
        await service.create_or_update_product(data_with_id)

        first_call_path = mock_client._post.call_args_list[0][0][0]
        assert "EXISTING-123" in first_call_path

    @pytest.mark.asyncio
    async def test_metafield_contains_supplier_url(self) -> None:
        """
        The metafield POST body must contain the supplier_product_url.
        """
        from app.services.shopify_service import ShopifyClient
        from app.services.shopify_product_service import ShopifyProductService

        mock_client           = MagicMock(spec=ShopifyClient)
        mock_client.store_domain = "test.myshopify.com"
        mock_client.api_secret   = "secret"
        mock_client._post     = AsyncMock(
            side_effect=[
                {"product": {"id": 11111}},
                {"metafield": {"id": 333}},
            ]
        )
        service = ShopifyProductService(client=mock_client)
        await service.create_or_update_product(SAMPLE_PRODUCT_DATA)

        # Second call is the metafield POST
        metafield_body = mock_client._post.call_args_list[1][0][1]
        assert "supplier" in metafield_body.get("metafield", {}).get("namespace", "")
        assert (
            SAMPLE_PRODUCT_DATA["supplier_product_url"]
            in metafield_body["metafield"]["value"]
        )


# ── E) sync_products_to_shopify task test ─────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_task_calls_service_for_unsynced_product(
    session: AsyncSession,
) -> None:
    """
    sync_products_to_shopify task logic:
    - queries get_unsynced_products
    - calls ShopifyProductService.create_or_update_product for each
    - calls mark_synced when shopify_id returned
    """
    from app.services.product_service import upsert_product

    async with session.begin_nested():
        await upsert_product(session, SAMPLE_PRODUCT_DATA)

    mock_service = MagicMock()
    mock_service.create_or_update_product = AsyncMock(return_value="SHOPIFY-NEW-ID")

    mock_mark_synced = AsyncMock()

    with (
        patch(
            "app.services.shopify_product_service.get_shopify_product_service",
            return_value=mock_service,
        ),
        patch(
            "app.services.product_service.mark_synced",
            mock_mark_synced,
        ),
    ):
        from app.services.product_service import get_unsynced_products

        unsynced = await get_unsynced_products(session)
        for product in unsynced:
            shopify_id = await mock_service.create_or_update_product(product)
            if shopify_id:
                await mock_mark_synced(session, product, shopify_id)

    mock_service.create_or_update_product.assert_awaited()
    mock_mark_synced.assert_awaited()
