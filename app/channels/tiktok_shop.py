from __future__ import annotations

"""
app/channels/tiktok_shop.py
────────────────────────────
Sprint 9 – TikTok Shop ChannelClient (mock / placeholder implementation).

TikTok Shop Open Platform API endpoints referenced (NOT called in this sprint):
  POST /product/202309/products                (create product)
  PUT  /product/202309/products/{product_id}   (update product)
  GET  /order/202309/orders/search             (fetch orders)
  PUT  /product/202309/products/prices         (update prices)
  PUT  /product/202309/products/inventory      (update inventory)

Real TikTok Shop API requires:
  • App Key + App Secret (HMAC-SHA256 signature)
  • Shop ID per connected store
  • OAuth2 access token

This implementation returns deterministic mock responses so the channel
router and worker tasks can be exercised without any network access.
"""

from typing import Any

import structlog

from app.channels.base import ChannelClient

logger = structlog.get_logger(__name__)

# Placeholder base URL (never called in this sprint)
_TIKTOK_API_BASE = "https://open-api.tiktokglobalshop.com"


class TikTokShopClient(ChannelClient):
    """
    TikTok Shop sales-channel client.

    Parameters
    ----------
    app_key      : TikTok App Key (env: TIKTOK_APP_KEY)
    app_secret   : TikTok App Secret (env: TIKTOK_APP_SECRET)
    shop_id      : TikTok Shop ID (env: TIKTOK_SHOP_ID)
    access_token : OAuth2 access token (env: TIKTOK_ACCESS_TOKEN)

    All parameters default to None; when any is missing the client
    operates in stub mode (returns mock data, no real API calls).
    """

    CHANNEL_NAME = "tiktok_shop"

    def __init__(
        self,
        app_key: str | None = None,
        app_secret: str | None = None,
        shop_id: str | None = None,
        access_token: str | None = None,
    ) -> None:
        super().__init__(self.CHANNEL_NAME)
        self._app_key      = app_key
        self._app_secret   = app_secret
        self._shop_id      = shop_id
        self._access_token = access_token
        self._stub_mode    = not all([app_key, app_secret, shop_id, access_token])

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _stub_product_id(self, canonical_sku: str) -> str:
        """Generate a deterministic mock TikTok product ID."""
        return f"TIKTOK-PROD-{abs(hash(canonical_sku)) % 10_000_000:07d}"

    def _stub_variant_id(self, canonical_sku: str) -> str:
        """Generate a deterministic mock TikTok SKU/variant ID."""
        return f"TIKTOK-SKU-{abs(hash(canonical_sku + 'variant')) % 10_000_000:07d}"

    # ── ChannelClient interface ───────────────────────────────────────────────

    async def create_product(
        self,
        canonical_product: Any,
        *,
        price: float | None = None,
    ) -> dict[str, Any]:
        """
        Create a product on TikTok Shop.

        Real endpoint: POST /product/202309/products
        Stub: returns mock product_id / sku_id without network call.
        """
        if isinstance(canonical_product, dict):
            sku  = canonical_product.get("canonical_sku", "unknown")
            name = canonical_product.get("name", "")
        else:
            sku  = canonical_product.canonical_sku
            name = canonical_product.name

        sell_price = price or 0.0

        log = logger.bind(channel=self.channel_name, canonical_sku=sku, stub=self._stub_mode)

        if self._stub_mode:
            result = {
                "external_product_id": self._stub_product_id(sku),
                "external_variant_id": self._stub_variant_id(sku),
                "price":               sell_price,
                "currency":            "USD",
                "_stub":               True,
            }
            log.info("tiktok_shop_channel.create_product.stub", name=name)
            return result

        # ── Real implementation placeholder ──────────────────────────────────
        # Would call: POST {_TIKTOK_API_BASE}/product/202309/products
        # Not implemented in Sprint 9.
        raise NotImplementedError("Real TikTok Shop API not wired in Sprint 9")  # pragma: no cover

    async def update_price(
        self,
        external_variant_id: str,
        new_price: float,
        currency: str = "USD",
    ) -> bool:
        """
        Update price for a TikTok Shop SKU.

        Real endpoint: PUT /product/202309/products/prices
        Stub: logs and returns True.
        """
        log = logger.bind(
            channel=self.channel_name,
            external_variant_id=external_variant_id,
            new_price=new_price,
            stub=self._stub_mode,
        )
        if self._stub_mode:
            log.info("tiktok_shop_channel.update_price.stub")
            return True

        raise NotImplementedError("Real TikTok Shop API not wired in Sprint 9")  # pragma: no cover

    async def update_inventory(
        self,
        external_variant_id: str,
        quantity: int,
    ) -> bool:
        """
        Update inventory for a TikTok Shop SKU.

        Real endpoint: PUT /product/202309/products/inventory
        Stub: logs and returns True.
        """
        log = logger.bind(
            channel=self.channel_name,
            external_variant_id=external_variant_id,
            quantity=quantity,
            stub=self._stub_mode,
        )
        if self._stub_mode:
            log.info("tiktok_shop_channel.update_inventory.stub")
            return True

        raise NotImplementedError("Real TikTok Shop API not wired in Sprint 9")  # pragma: no cover

    async def fetch_orders(
        self,
        *,
        limit: int = 50,
        status: str = "pending",
    ) -> list[dict[str, Any]]:
        """
        Fetch orders from TikTok Shop.

        Real endpoint: GET /order/202309/orders/search
        Stub: returns an empty list.
        """
        log = logger.bind(channel=self.channel_name, limit=limit, status=status, stub=self._stub_mode)
        if self._stub_mode:
            log.info("tiktok_shop_channel.fetch_orders.stub")
            return []

        raise NotImplementedError("Real TikTok Shop API not wired in Sprint 9")  # pragma: no cover
