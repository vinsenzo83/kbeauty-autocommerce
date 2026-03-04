from __future__ import annotations

from typing import Any, Optional
import structlog

logger = structlog.get_logger(__name__)


class ShopifyClient:
    """
    Stub Shopify API client.

    In production this would call the real Shopify Admin REST / GraphQL API.
    For MVP / tests it is a no-op stub that can be monkey-patched.
    """

    def __init__(self, store_domain: Optional[str] = None, api_secret: Optional[str] = None):
        self.store_domain = store_domain
        self.api_secret = api_secret

    async def get_order(self, shopify_order_id: str) -> dict[str, Any]:
        """Fetch a single order from Shopify. Stub returns empty dict."""
        logger.debug("shopify_client.get_order (stub)", shopify_order_id=shopify_order_id)
        return {}

    async def update_order_tags(self, shopify_order_id: str, tags: list[str]) -> bool:
        """Update order tags on Shopify. Stub is a no-op."""
        logger.debug(
            "shopify_client.update_order_tags (stub)",
            shopify_order_id=shopify_order_id,
            tags=tags,
        )
        return True

    async def cancel_order(self, shopify_order_id: str, reason: str = "") -> bool:
        """Cancel an order on Shopify. Stub is a no-op."""
        logger.debug(
            "shopify_client.cancel_order (stub)",
            shopify_order_id=shopify_order_id,
            reason=reason,
        )
        return True
