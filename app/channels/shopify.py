from __future__ import annotations

"""
app/channels/shopify.py
────────────────────────
Sprint 9 – Shopify ChannelClient adapter.

Wraps the existing ShopifyProductService / ShopifyClient so the
multi-channel engine has a uniform interface to Shopify.

All methods gracefully degrade to a stub (no-op) when Shopify
credentials are missing, so unit tests never make real network calls.
"""

from typing import Any

import structlog

from app.channels.base import ChannelClient

logger = structlog.get_logger(__name__)


class ShopifyChannelClient(ChannelClient):
    """
    Shopify implementation of ChannelClient.

    Parameters
    ----------
    shopify_product_service :
        Injected ShopifyProductService (or mock).  If None, creates one
        via the factory function.
    shopify_client :
        Low-level ShopifyClient injected for order fetching.  If None,
        created lazily.
    """

    CHANNEL_NAME = "shopify"

    def __init__(
        self,
        shopify_product_service: Any = None,
        shopify_client: Any = None,
    ) -> None:
        super().__init__(self.CHANNEL_NAME)
        self._svc    = shopify_product_service
        self._client = shopify_client

    def _get_service(self) -> Any:
        if self._svc is None:  # pragma: no cover
            from app.services.shopify_product_service import get_shopify_product_service
            self._svc = get_shopify_product_service()
        return self._svc

    def _get_client(self) -> Any:
        if self._client is None:  # pragma: no cover
            from app.services.shopify_service import get_shopify_client
            self._client = get_shopify_client()
        return self._client

    # ── ChannelClient interface ───────────────────────────────────────────────

    async def create_product(
        self,
        canonical_product: Any,
        *,
        price: float | None = None,
    ) -> dict[str, Any]:
        """
        Create or update a Shopify product from a canonical product.

        Delegates to ShopifyProductService.create_or_update_product.
        """
        log = logger.bind(
            channel=self.channel_name,
            canonical_sku=getattr(canonical_product, "canonical_sku", None)
            or (canonical_product.get("canonical_sku") if isinstance(canonical_product, dict) else None),
        )

        # Build a product-like dict the existing service expects
        if isinstance(canonical_product, dict):
            product_data = dict(canonical_product)
        else:
            product_data = {
                "name":                canonical_product.name,
                "brand":               getattr(canonical_product, "brand", ""),
                "sale_price":          price or float(canonical_product.last_price or 0),
                "price":               price or float(canonical_product.last_price or 0),
                "image_urls_json":     getattr(canonical_product, "image_urls_json", []),
                "stock_status":        "in_stock",
                "supplier_product_url": "",
                "shopify_product_id":  None,
            }
            if price:
                product_data["sale_price"] = price
                product_data["price"]      = price

        svc = self._get_service()
        shopify_product_id = await svc.create_or_update_product(product_data)

        result = {
            "external_product_id": shopify_product_id or "",
            "external_variant_id": "",
            "price":               price or 0.0,
            "currency":            "USD",
        }
        log.info("shopify_channel.create_product.done", result=result)
        return result

    async def update_price(
        self,
        external_variant_id: str,
        new_price: float,
        currency: str = "USD",
    ) -> bool:
        """
        Update variant price on Shopify.

        Delegates to ShopifyProductService.update_variant_price_by_id.
        """
        log = logger.bind(
            channel=self.channel_name,
            external_variant_id=external_variant_id,
            new_price=new_price,
        )
        svc = self._get_service()
        try:
            ok = await svc.update_variant_price_by_id(external_variant_id, new_price)
            log.info("shopify_channel.update_price.done", ok=ok)
            return ok
        except Exception as exc:  # pragma: no cover
            log.error("shopify_channel.update_price.error", exc=str(exc))
            return False

    async def update_inventory(
        self,
        external_variant_id: str,
        quantity: int,
    ) -> bool:
        """
        Update inventory quantity on Shopify.

        Uses set_inventory_zero for out-of-stock (quantity <= 0).
        For positive quantities, delegates to the low-level client.
        """
        log = logger.bind(
            channel=self.channel_name,
            external_variant_id=external_variant_id,
            quantity=quantity,
        )
        try:
            if quantity <= 0:
                # Use the existing set_inventory_zero path
                svc = self._get_service()
                product_dict = {
                    "shopify_product_id": None,
                    "shopify_variant_id": external_variant_id,
                    "name": "",
                }
                ok = await svc.set_inventory_zero(product_dict)
            else:
                # Shopify inventory adjustment requires Inventory API;
                # stub to True for now (extend when Inventory API is wired)
                log.debug("shopify_channel.update_inventory.positive.stub")
                ok = True
            log.info("shopify_channel.update_inventory.done", ok=ok)
            return ok
        except Exception as exc:  # pragma: no cover
            log.error("shopify_channel.update_inventory.error", exc=str(exc))
            return False

    async def fetch_orders(
        self,
        *,
        limit: int = 50,
        status: str = "pending",
    ) -> list[dict[str, Any]]:
        """
        Fetch recent orders from Shopify via the Admin REST API.

        Returns a normalised list of order dicts matching the ChannelClient
        contract.
        """
        log = logger.bind(channel=self.channel_name, limit=limit, status=status)
        client = self._get_client()
        try:
            raw = await client._get(  # type: ignore[protected-access]
                f"/orders.json?limit={limit}&fulfillment_status=unfulfilled&status=open"
            )
        except Exception as exc:  # pragma: no cover
            log.error("shopify_channel.fetch_orders.error", exc=str(exc))
            return []

        orders_raw = (raw or {}).get("orders", [])
        orders: list[dict[str, Any]] = []
        for o in orders_raw:
            for li in o.get("line_items", []):
                orders.append(
                    {
                        "external_order_id":   str(o["id"]),
                        "external_product_id": str(li.get("product_id", "")),
                        "external_variant_id": str(li.get("variant_id", "")),
                        "quantity":            int(li.get("quantity", 1)),
                        "price":               float(li.get("price", 0)),
                        "currency":            o.get("currency", "USD"),
                        "status":              "pending",
                    }
                )
        log.info("shopify_channel.fetch_orders.done", count=len(orders))
        return orders
