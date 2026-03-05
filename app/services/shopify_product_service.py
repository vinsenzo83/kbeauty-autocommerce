from __future__ import annotations

"""
app/services/shopify_product_service.py
────────────────────────────────────────
Shopify product sync service for Sprint 4.

Responsibilities
----------------
* Create or update a Shopify product via the Admin REST API.
* Set the ``supplier.product_url`` metafield so the storefront
  can link back to the original supplier listing.
* Return the Shopify product ID for storage in our DB.

All calls are stubbed when credentials are missing, so tests never
touch the real Shopify API.
"""

from typing import Any

import structlog

from app.services.shopify_service import ShopifyClient, get_shopify_client

logger = structlog.get_logger(__name__)

# Shopify Admin API version (same as ShopifyClient._API_VERSION)
_API_VERSION = "2024-01"


class ShopifyProductService:
    """
    Thin wrapper around ``ShopifyClient`` for product operations.

    Parameters
    ----------
    client : ShopifyClient | None
        Injected for testing. Falls back to ``get_shopify_client()``.
    """

    def __init__(self, client: ShopifyClient | None = None) -> None:
        self._client = client or get_shopify_client()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_product_body(self, product: Any) -> dict[str, Any]:
        """
        Construct the JSON body for Shopify product create/update.

        Accepts ORM Product instances or plain dicts.
        """
        if isinstance(product, dict):
            name                 = product.get("name", "")
            brand                = product.get("brand") or ""
            price                = str(product.get("sale_price") or product.get("price") or "0.00")
            image_urls           = product.get("image_urls_json") or product.get("image_urls") or []
            stock_status         = product.get("stock_status", "unknown")
        else:
            name                 = product.name
            brand                = product.brand or ""
            price                = str(product.sale_price or product.price or "0.00")
            image_urls           = product.image_urls_json or []
            stock_status         = product.stock_status

        inventory_policy = "deny" if stock_status == "out_of_stock" else "continue"

        body: dict[str, Any] = {
            "product": {
                "title":        name,
                "vendor":       brand,
                "product_type": "K-Beauty",
                "status":       "active",
                "variants": [
                    {
                        "price":            price,
                        "inventory_policy": inventory_policy,
                    }
                ],
            }
        }

        # Attach images (up to 10 to stay within Shopify limits)
        if image_urls:
            body["product"]["images"] = [
                {"src": url} for url in image_urls[:10]
            ]

        return body

    async def _set_metafield(
        self,
        shopify_product_id: str,
        supplier_product_url: str,
    ) -> None:
        """
        Set the ``supplier.product_url`` metafield on a Shopify product.

        POST /admin/api/{version}/products/{id}/metafields.json
        """
        path = f"/products/{shopify_product_id}/metafields.json"
        body = {
            "metafield": {
                "namespace": "supplier",
                "key":       "product_url",
                "value":     supplier_product_url,
                "type":      "url",
            }
        }
        result = await self._client._post(path, body)  # type: ignore[protected-access]
        logger.debug(
            "shopify_product_service.metafield_set",
            shopify_product_id=shopify_product_id,
            supplier_product_url=supplier_product_url,
            result_keys=list(result.keys()) if result else [],
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def create_or_update_product(self, product: Any) -> str | None:
        """
        Create or update a Shopify product.

        If the product already has a ``shopify_product_id``, update it;
        otherwise create a new one.

        Parameters
        ----------
        product : ORM Product instance or dict
            Must have ``supplier_product_url``.

        Returns
        -------
        Shopify product ID (str) on success, or None in stub mode.
        """
        if isinstance(product, dict):
            shopify_id           = product.get("shopify_product_id")
            supplier_product_url = product.get("supplier_product_url", "")
            name                 = product.get("name", "")
        else:
            shopify_id           = product.shopify_product_id
            supplier_product_url = product.supplier_product_url
            name                 = product.name

        body = self._build_product_body(product)
        log  = logger.bind(name=name, supplier_product_url=supplier_product_url)

        if shopify_id:
            # Update existing product
            log.info(
                "shopify_product_service.updating",
                shopify_product_id=shopify_id,
            )
            result = await self._client._post(  # type: ignore[protected-access]
                f"/products/{shopify_id}.json",
                {"product": {**body["product"], "id": shopify_id}},
            )
        else:
            # Create new product
            log.info("shopify_product_service.creating")
            result = await self._client._post("/products.json", body)  # type: ignore[protected-access]

        # Extract Shopify product ID from response
        new_shopify_id: str | None = None
        if result and "product" in result:
            raw_id         = result["product"].get("id")
            new_shopify_id = str(raw_id) if raw_id else None

        if not new_shopify_id:
            # Stub mode: no credentials → return None
            log.debug("shopify_product_service.stub_noop")
            return None

        # Set metafield
        if supplier_product_url:
            await self._set_metafield(new_shopify_id, supplier_product_url)

        log.info(
            "shopify_product_service.done",
            shopify_product_id=new_shopify_id,
        )
        return new_shopify_id

    # ── Sprint 6: Inventory sync methods ─────────────────────────────────────

    async def _get_variant_id(self, product: Any) -> str | None:
        """
        Return the first variant ID for a Shopify product.

        Checks ``product.shopify_variant_id`` first (cached); otherwise
        fetches from Shopify and returns the first variant ID.
        """
        if hasattr(product, "shopify_variant_id") and product.shopify_variant_id:
            return str(product.shopify_variant_id)

        shopify_id = (
            product.get("shopify_product_id")
            if isinstance(product, dict)
            else getattr(product, "shopify_product_id", None)
        )
        if not shopify_id:
            return None

        result = await self._client._get(f"/products/{shopify_id}.json")  # type: ignore[protected-access]
        variants = result.get("product", {}).get("variants", [])
        if not variants:
            return None
        return str(variants[0]["id"])

    async def set_inventory_zero(self, product: Any) -> bool:
        """
        Set Shopify inventory to 0 for an out-of-stock product.

        Updates the variant's ``inventory_policy`` to ``"deny"`` via
        PUT /variants/{variant_id}.json — prevents new orders even when
        location-based inventory API is unavailable.

        Returns
        -------
        True if the call succeeded (or was a no-op stub), False on error.
        """
        shopify_id = (
            product.get("shopify_product_id")
            if isinstance(product, dict)
            else getattr(product, "shopify_product_id", None)
        )
        if not shopify_id:
            logger.debug("shopify_product_service.set_inventory_zero.no_shopify_id")
            return False

        name = (
            product.get("name", "")
            if isinstance(product, dict)
            else getattr(product, "name", "")
        )
        log = logger.bind(shopify_product_id=shopify_id, name=name)
        log.info("shopify_product_service.set_inventory_zero")

        variant_id = await self._get_variant_id(product)
        if not variant_id:
            log.warning("shopify_product_service.set_inventory_zero.no_variant")
            return False

        result = await self._client._put(  # type: ignore[protected-access]
            f"/variants/{variant_id}.json",
            {
                "variant": {
                    "id":               variant_id,
                    "inventory_policy": "deny",
                }
            },
        )

        ok = bool(result) and "variant" in result
        log.info(
            "shopify_product_service.set_inventory_zero.done",
            ok=ok,
            variant_id=variant_id,
        )
        return ok

    async def update_variant_price(self, product: Any, new_price: float) -> bool:
        """
        Update the price of the first variant of a Shopify product.

        Parameters
        ----------
        product   : ORM Product or dict with ``shopify_product_id``.
        new_price : New price as float.

        Returns
        -------
        True if the update succeeded or was a stub no-op, False on error.
        """
        shopify_id = (
            product.get("shopify_product_id")
            if isinstance(product, dict)
            else getattr(product, "shopify_product_id", None)
        )
        if not shopify_id:
            logger.debug("shopify_product_service.update_variant_price.no_shopify_id")
            return False

        name = (
            product.get("name", "")
            if isinstance(product, dict)
            else getattr(product, "name", "")
        )
        log = logger.bind(
            shopify_product_id=shopify_id,
            name=name,
            new_price=new_price,
        )
        log.info("shopify_product_service.update_variant_price")

        variant_id = await self._get_variant_id(product)
        if not variant_id:
            log.warning("shopify_product_service.update_variant_price.no_variant")
            return False

        result = await self._client._put(  # type: ignore[protected-access]
            f"/variants/{variant_id}.json",
            {
                "variant": {
                    "id":    variant_id,
                    "price": f"{new_price:.2f}",
                }
            },
        )

        ok = bool(result) and "variant" in result
        log.info(
            "shopify_product_service.update_variant_price.done",
            ok=ok,
            variant_id=variant_id,
        )
        return ok


def get_shopify_product_service(
    client: ShopifyClient | None = None,
) -> ShopifyProductService:
    """Factory function for use in Celery tasks."""
    return ShopifyProductService(client=client)
