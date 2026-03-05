from __future__ import annotations

"""
app/services/shopify_fulfillment_service.py
─────────────────────────────────────────────
Sprint 14 – Create Shopify fulfillment with tracking information.

Uses the existing ShopifyClient to:
1. Find the fulfillment order(s) for a Shopify order ID.
2. Create a fulfillment with tracking number and carrier.

Design notes
------------
- Stub mode (no Shopify credentials): returns a mock fulfillment_id.
- Real mode: calls Shopify Admin REST API (2024-01).
"""

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Carrier name normalization ────────────────────────────────────────────────

_CARRIER_MAP: dict[str, str] = {
    "DHL":    "dhl",
    "FEDEX":  "fedex",
    "UPS":    "ups",
    "USPS":   "usps",
    "EMS":    "ems",
    "KOREA POST": "korea_post",
}


def _normalize_carrier(carrier: str | None) -> str:
    if not carrier:
        return "other"
    return _CARRIER_MAP.get(carrier.upper(), carrier.lower().replace(" ", "_"))


# ─────────────────────────────────────────────────────────────────────────────

class ShopifyFulfillmentService:
    """
    Creates fulfillments on Shopify when a supplier ships an order.

    Parameters
    ----------
    shopify_client : ShopifyClient instance (injectable for testing).
    """

    def __init__(self, shopify_client: Any = None) -> None:
        if shopify_client is None:
            from app.services.shopify_service import ShopifyClient
            shopify_client = ShopifyClient()
        self._client = shopify_client

    async def create_shopify_fulfillment(
        self,
        shopify_order_id: str,
        tracking_number: str,
        carrier: str | None = None,
        *,
        notify_customer: bool = True,
    ) -> dict[str, Any]:
        """
        Create a fulfillment on Shopify for the given order.

        Parameters
        ----------
        shopify_order_id : Shopify order ID (numeric string, e.g. "6543210987654").
        tracking_number  : Carrier tracking code.
        carrier          : Carrier name (e.g. 'DHL', 'FedEx').
        notify_customer  : Send tracking email to customer (default True).

        Returns
        -------
        dict with keys: fulfillment_id, status, tracking_number, carrier, shopify_order_id.

        Raises
        ------
        RuntimeError on Shopify API error (non-stub mode only).
        """
        log = logger.bind(
            shopify_order_id=shopify_order_id,
            tracking_number=tracking_number,
            carrier=carrier,
        )

        # ── Stub mode ─────────────────────────────────────────────────────────
        if not self._client.store_domain or not self._client.api_secret:
            stub_id = f"STUB-FULFILL-{shopify_order_id[-8:]}"
            log.info(
                "shopify_fulfillment.stub",
                fulfillment_id=stub_id,
                note="No credentials configured — returning stub response",
            )
            return {
                "fulfillment_id":  stub_id,
                "status":          "success",
                "tracking_number": tracking_number,
                "carrier":         carrier,
                "shopify_order_id": shopify_order_id,
                "stub":            True,
            }

        # ── Real mode ─────────────────────────────────────────────────────────
        # Step 1: Get fulfillment orders for the Shopify order
        fo_resp = await self._client._get(
            f"/orders/{shopify_order_id}/fulfillment_orders.json"
        )
        fulfillment_orders = fo_resp.get("fulfillment_orders", [])

        if not fulfillment_orders:
            log.warning("shopify_fulfillment.no_fulfillment_orders")
            raise RuntimeError(
                f"No fulfillment orders found for Shopify order {shopify_order_id}"
            )

        # Use first open fulfillment order
        fo = next(
            (f for f in fulfillment_orders if f.get("status") == "open"),
            fulfillment_orders[0],
        )
        fo_id = fo["id"]

        # Step 2: Create fulfillment
        payload = {
            "fulfillment": {
                "line_items_by_fulfillment_order": [
                    {"fulfillment_order_id": fo_id}
                ],
                "tracking_info": {
                    "number":  tracking_number,
                    "company": _normalize_carrier(carrier),
                },
                "notify_customer": notify_customer,
            }
        }

        resp = await self._client._post("/fulfillments.json", payload)
        fulfillment = resp.get("fulfillment", {})
        fulfillment_id = str(fulfillment.get("id", ""))

        log.info(
            "shopify_fulfillment.created",
            fulfillment_id=fulfillment_id,
            fo_id=fo_id,
        )

        return {
            "fulfillment_id":   fulfillment_id,
            "status":           fulfillment.get("status", "success"),
            "tracking_number":  tracking_number,
            "carrier":          carrier,
            "shopify_order_id": shopify_order_id,
            "stub":             False,
        }


def get_shopify_fulfillment_service(
    shopify_client: Any = None,
) -> ShopifyFulfillmentService:
    """Factory — returns a ShopifyFulfillmentService instance."""
    return ShopifyFulfillmentService(shopify_client=shopify_client)
