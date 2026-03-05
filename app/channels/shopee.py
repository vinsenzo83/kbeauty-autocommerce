from __future__ import annotations

"""
app/channels/shopee.py
───────────────────────
Sprint 9 – Shopee ChannelClient (mock / placeholder implementation).

Shopee Open Platform API endpoints referenced (NOT called in this sprint):
  POST /api/v2/product/add
  POST /api/v2/product/update_item
  GET  /api/v2/order/get_order_list
  POST /api/v2/product/update_price

Real Shopee API requires:
  • Partner ID + Partner Key (HMAC-SHA256 signature on every request)
  • Shop ID per connected store
  • Refresh-token-based OAuth2 access tokens

This implementation returns deterministic mock responses so the channel
router and worker tasks can be exercised without any network access.
All mock responses follow the shape that the real Shopee API would return,
making it straightforward to swap in a real implementation later.
"""

from typing import Any

import structlog

from app.channels.base import ChannelClient

logger = structlog.get_logger(__name__)

# Placeholder base URL (never called in this sprint)
_SHOPEE_API_BASE = "https://partner.shopeemobile.com"


class ShopeeChannelClient(ChannelClient):
    """
    Shopee sales-channel client.

    Parameters
    ----------
    partner_id  : Shopee Partner ID (env: SHOPEE_PARTNER_ID)
    partner_key : Shopee Partner Key (env: SHOPEE_PARTNER_KEY)
    shop_id     : Shopee Shop ID (env: SHOPEE_SHOP_ID)
    access_token: OAuth2 access token (env: SHOPEE_ACCESS_TOKEN)

    All parameters default to None; when any is missing the client
    operates in stub mode (returns mock data, no real API calls).
    """

    CHANNEL_NAME = "shopee"

    def __init__(
        self,
        partner_id: str | None = None,
        partner_key: str | None = None,
        shop_id: str | None = None,
        access_token: str | None = None,
    ) -> None:
        super().__init__(self.CHANNEL_NAME)
        self._partner_id   = partner_id
        self._partner_key  = partner_key
        self._shop_id      = shop_id
        self._access_token = access_token
        self._stub_mode    = not all([partner_id, partner_key, shop_id, access_token])

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _stub_product_id(self, canonical_sku: str) -> str:
        """Generate a deterministic mock Shopee item ID from canonical_sku."""
        return f"SHOPEE-ITEM-{abs(hash(canonical_sku)) % 10_000_000:07d}"

    def _stub_variant_id(self, canonical_sku: str) -> str:
        """Generate a deterministic mock Shopee model ID from canonical_sku."""
        return f"SHOPEE-MODEL-{abs(hash(canonical_sku + 'variant')) % 10_000_000:07d}"

    # ── ChannelClient interface ───────────────────────────────────────────────

    async def create_product(
        self,
        canonical_product: Any,
        *,
        price: float | None = None,
    ) -> dict[str, Any]:
        """
        Create a product listing on Shopee.

        Real endpoint: POST /api/v2/product/add
        Stub: returns mock item_id / model_id without network call.
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
            log.info("shopee_channel.create_product.stub", name=name)
            return result

        # ── Real implementation placeholder ──────────────────────────────────
        # Would call: POST {_SHOPEE_API_BASE}/api/v2/product/add
        # with HMAC-SHA256-signed headers and product payload.
        # Not implemented in Sprint 9.
        raise NotImplementedError("Real Shopee API not wired in Sprint 9")  # pragma: no cover

    async def update_price(
        self,
        external_variant_id: str,
        new_price: float,
        currency: str = "USD",
    ) -> bool:
        """
        Update price for a Shopee product model.

        Real endpoint: POST /api/v2/product/update_price
        Stub: logs and returns True.
        """
        log = logger.bind(
            channel=self.channel_name,
            external_variant_id=external_variant_id,
            new_price=new_price,
            stub=self._stub_mode,
        )
        if self._stub_mode:
            log.info("shopee_channel.update_price.stub")
            return True

        raise NotImplementedError("Real Shopee API not wired in Sprint 9")  # pragma: no cover

    async def update_inventory(
        self,
        external_variant_id: str,
        quantity: int,
    ) -> bool:
        """
        Update stock quantity for a Shopee product model.

        Real endpoint: POST /api/v2/product/update_stock
        Stub: logs and returns True.
        """
        log = logger.bind(
            channel=self.channel_name,
            external_variant_id=external_variant_id,
            quantity=quantity,
            stub=self._stub_mode,
        )
        if self._stub_mode:
            log.info("shopee_channel.update_inventory.stub")
            return True

        raise NotImplementedError("Real Shopee API not wired in Sprint 9")  # pragma: no cover

    async def fetch_orders(
        self,
        *,
        limit: int = 50,
        status: str = "pending",
    ) -> list[dict[str, Any]]:
        """
        Fetch orders from Shopee.

        Real endpoint: GET /api/v2/order/get_order_list
        Stub: returns an empty list.
        """
        log = logger.bind(channel=self.channel_name, limit=limit, status=status, stub=self._stub_mode)
        if self._stub_mode:
            log.info("shopee_channel.fetch_orders.stub")
            return []

        raise NotImplementedError("Real Shopee API not wired in Sprint 9")  # pragma: no cover
