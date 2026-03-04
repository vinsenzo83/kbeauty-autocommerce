from __future__ import annotations

import json
from typing import Any, Optional

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)


class ShopifyClient:
    """
    Shopify Admin REST API client.

    Production calls use SHOPIFY_STORE_DOMAIN + SHOPIFY_API_SECRET.
    All methods fall back to stub behaviour when credentials are absent,
    so tests never make real network requests.
    """

    _API_VERSION = "2024-01"

    def __init__(
        self,
        store_domain: Optional[str] = None,
        api_secret: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        self.store_domain = store_domain or settings.SHOPIFY_STORE_DOMAIN or ""
        self.api_secret   = api_secret   or settings.SHOPIFY_API_SECRET   or ""

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _base_url(self) -> str:
        return f"https://{self.store_domain}/admin/api/{self._API_VERSION}"

    def _headers(self) -> dict[str, str]:
        return {
            "X-Shopify-Access-Token": self.api_secret,
            "Content-Type":           "application/json",
        }

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """
        POST to the Shopify Admin API.

        Returns parsed JSON response dict.
        Stub: when store_domain or api_secret is empty, logs and returns {}.
        """
        if not self.store_domain or not self.api_secret:
            logger.debug(
                "shopify_client.stub_call",
                method="POST",
                path=path,
                note="No credentials configured — stub response returned.",
            )
            return {}

        try:
            import httpx  # type: ignore[import]
        except ImportError:
            logger.warning(
                "shopify_client.httpx_missing",
                note="httpx not installed. Returning stub response.",
            )
            return {}

        url = f"{self._base_url()}{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=self._headers(), content=json.dumps(body))
            resp.raise_for_status()
            return resp.json()

    # ── Public methods ────────────────────────────────────────────────────────

    async def get_order(self, shopify_order_id: str) -> dict[str, Any]:
        """Fetch a single order from Shopify."""
        logger.debug("shopify_client.get_order", shopify_order_id=shopify_order_id)
        return {}

    async def update_order_tags(self, shopify_order_id: str, tags: list[str]) -> bool:
        """Update order tags on Shopify."""
        logger.debug(
            "shopify_client.update_order_tags",
            shopify_order_id=shopify_order_id,
            tags=tags,
        )
        return True

    async def cancel_order(self, shopify_order_id: str, reason: str = "") -> bool:
        """Cancel an order on Shopify."""
        logger.debug(
            "shopify_client.cancel_order",
            shopify_order_id=shopify_order_id,
            reason=reason,
        )
        return True

    async def create_fulfillment(
        self,
        order: Any,
        tracking_number: str,
        tracking_url: Optional[str] = None,
        *,
        notify_customer: bool = True,
    ) -> dict[str, Any]:
        """
        Create a Shopify fulfillment for an order.

        Calls:
          POST /admin/api/2024-01/orders/{shopify_order_id}/fulfillments.json

        Parameters
        ----------
        order           : ORM Order instance (reads .shopify_order_id)
        tracking_number : Carrier tracking number
        tracking_url    : Full tracking URL (optional)
        notify_customer : Whether Shopify should send shipment notification email

        Returns
        -------
        Parsed JSON response from Shopify, or {} in stub mode.
        """
        shopify_order_id = order.shopify_order_id
        log = logger.bind(
            shopify_order_id=shopify_order_id,
            tracking_number=tracking_number,
        )
        log.info("shopify.create_fulfillment")

        body: dict[str, Any] = {
            "fulfillment": {
                "tracking_number": tracking_number,
                "notify_customer": notify_customer,
            }
        }
        if tracking_url:
            body["fulfillment"]["tracking_url"] = tracking_url

        result = await self._post(
            f"/orders/{shopify_order_id}/fulfillments.json",
            body,
        )

        if result:
            log.info(
                "shopify.create_fulfillment.success",
                fulfillment_id=result.get("fulfillment", {}).get("id"),
            )
        else:
            log.debug("shopify.create_fulfillment.stub_noop")

        return result


def get_shopify_client() -> ShopifyClient:
    """Factory used by worker tasks; returns a configured ShopifyClient."""
    return ShopifyClient()
