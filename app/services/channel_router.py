from __future__ import annotations

"""
app/services/channel_router.py
───────────────────────────────
Sprint 9 – Multi-Channel routing service.

Responsibilities
----------------
* get_enabled_channels()
    Returns the list of channel slugs that are currently enabled
    (reads from in-process config; no DB query needed for the fast path).

* publish_product_to_channels(canonical_product, *, price, clients)
    Calls create_product on every enabled channel client and returns
    a summary dict  {channel_name: result_dict | None}.

* update_price_all_channels(canonical_product, new_price, *, clients)
    Calls update_price on every enabled channel client that has an
    external_variant_id registered in channel_products.

* update_inventory_all_channels(canonical_product, quantity, *, clients)
    Calls update_inventory on every enabled channel client.

Design notes
------------
* All public functions accept an optional ``clients`` parameter so that
  tests can inject mock clients without touching the real constructors.
* The default client list is built lazily; missing credentials cause a
  graceful fallback to stub mode (no exceptions propagate).
* No DB session is required by default – channel_products persistence is
  done by the callers (worker tasks / admin API).
"""

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ── Enabled-channel registry ─────────────────────────────────────────────────
#
# In a production system this would be driven by the sales_channels DB table.
# For Sprint 9 we use a static list that matches the seeded rows in 0010.
#
_ENABLED_CHANNELS: list[str] = ["shopify", "shopee", "tiktok_shop"]


def get_enabled_channels() -> list[str]:
    """
    Return the list of enabled channel slugs.

    Returns
    -------
    list[str]
        e.g. ['shopify', 'shopee', 'tiktok_shop']
    """
    return list(_ENABLED_CHANNELS)


# ── Default client factory ────────────────────────────────────────────────────

def _build_default_clients() -> dict[str, Any]:
    """
    Lazily build default channel clients using environment credentials.

    Each client gracefully degrades to stub mode when credentials are absent,
    so this never raises in test / CI environments.
    """
    from app.channels.shopify import ShopifyChannelClient
    from app.channels.shopee import ShopeeChannelClient
    from app.channels.tiktok_shop import TikTokShopClient

    import os

    return {
        "shopify": ShopifyChannelClient(),
        "shopee": ShopeeChannelClient(
            partner_id   = os.getenv("SHOPEE_PARTNER_ID"),
            partner_key  = os.getenv("SHOPEE_PARTNER_KEY"),
            shop_id      = os.getenv("SHOPEE_SHOP_ID"),
            access_token = os.getenv("SHOPEE_ACCESS_TOKEN"),
        ),
        "tiktok_shop": TikTokShopClient(
            app_key      = os.getenv("TIKTOK_APP_KEY"),
            app_secret   = os.getenv("TIKTOK_APP_SECRET"),
            shop_id      = os.getenv("TIKTOK_SHOP_ID"),
            access_token = os.getenv("TIKTOK_ACCESS_TOKEN"),
        ),
    }


# ── Core routing functions ────────────────────────────────────────────────────

async def publish_product_to_channels(
    canonical_product: Any,
    *,
    price: float | None = None,
    clients: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Publish (create / register) a canonical product on all enabled channels.

    Parameters
    ----------
    canonical_product :
        CanonicalProduct ORM instance or dict.
    price :
        Override sell price.  Falls back to canonical_product.last_price.
    clients :
        Map of {channel_slug: ChannelClient}.  Injected for testing.
        Defaults to _build_default_clients().

    Returns
    -------
    dict mapping channel_slug → result dict (or None on error):
        {
          "shopify":     {"external_product_id": ..., "external_variant_id": ..., ...},
          "shopee":      {"external_product_id": ..., ...},
          "tiktok_shop": {"external_product_id": ..., ...},
        }
    """
    if clients is None:
        clients = _build_default_clients()  # pragma: no cover

    canonical_sku = (
        canonical_product.get("canonical_sku")
        if isinstance(canonical_product, dict)
        else getattr(canonical_product, "canonical_sku", None)
    )
    log = logger.bind(canonical_sku=canonical_sku, price=price)
    log.info("channel_router.publish_product_to_channels.start")

    results: dict[str, Any] = {}
    for channel_slug in get_enabled_channels():
        client = clients.get(channel_slug)
        if client is None:
            log.warning("channel_router.no_client", channel=channel_slug)
            results[channel_slug] = None
            continue

        try:
            result = await client.create_product(canonical_product, price=price)
            results[channel_slug] = result
            log.info(
                "channel_router.publish_product_to_channels.channel_done",
                channel=channel_slug,
                external_product_id=result.get("external_product_id"),
            )
        except Exception as exc:
            log.error(
                "channel_router.publish_product_to_channels.error",
                channel=channel_slug,
                exc=str(exc),
            )
            results[channel_slug] = None

    log.info("channel_router.publish_product_to_channels.done", results=results)
    return results


async def update_price_all_channels(
    canonical_product: Any,
    new_price: float,
    *,
    channel_variant_map: dict[str, str] | None = None,
    clients: dict[str, Any] | None = None,
) -> dict[str, bool]:
    """
    Update the sell price on all enabled channels.

    Parameters
    ----------
    canonical_product :
        CanonicalProduct ORM instance or dict (used only for logging).
    new_price :
        New sell price (already rounded / computed by pricing engine).
    channel_variant_map :
        Map of {channel_slug: external_variant_id} for channels that
        already have a listing.  Channels absent from this map are skipped.
    clients :
        Injected for testing.

    Returns
    -------
    dict mapping channel_slug → bool (True = success, False = error/skipped).
    """
    if clients is None:
        clients = _build_default_clients()  # pragma: no cover

    if channel_variant_map is None:
        channel_variant_map = {}

    canonical_sku = (
        canonical_product.get("canonical_sku")
        if isinstance(canonical_product, dict)
        else getattr(canonical_product, "canonical_sku", None)
    )
    log = logger.bind(canonical_sku=canonical_sku, new_price=new_price)
    log.info("channel_router.update_price_all_channels.start")

    results: dict[str, bool] = {}
    for channel_slug in get_enabled_channels():
        variant_id = channel_variant_map.get(channel_slug)
        if not variant_id:
            # No listing registered yet – skip silently
            results[channel_slug] = False
            continue

        client = clients.get(channel_slug)
        if client is None:
            results[channel_slug] = False
            continue

        try:
            ok = await client.update_price(variant_id, new_price)
            results[channel_slug] = ok
            log.info(
                "channel_router.update_price_all_channels.channel_done",
                channel=channel_slug,
                ok=ok,
            )
        except Exception as exc:
            log.error(
                "channel_router.update_price_all_channels.error",
                channel=channel_slug,
                exc=str(exc),
            )
            results[channel_slug] = False

    return results


async def update_inventory_all_channels(
    canonical_product: Any,
    quantity: int,
    *,
    channel_variant_map: dict[str, str] | None = None,
    clients: dict[str, Any] | None = None,
) -> dict[str, bool]:
    """
    Update inventory quantity on all enabled channels.

    Parameters
    ----------
    canonical_product :
        CanonicalProduct ORM instance or dict (used only for logging).
    quantity :
        New available quantity (0 = out of stock).
    channel_variant_map :
        Map of {channel_slug: external_variant_id}.
    clients :
        Injected for testing.

    Returns
    -------
    dict mapping channel_slug → bool.
    """
    if clients is None:
        clients = _build_default_clients()  # pragma: no cover

    if channel_variant_map is None:
        channel_variant_map = {}

    canonical_sku = (
        canonical_product.get("canonical_sku")
        if isinstance(canonical_product, dict)
        else getattr(canonical_product, "canonical_sku", None)
    )
    log = logger.bind(canonical_sku=canonical_sku, quantity=quantity)
    log.info("channel_router.update_inventory_all_channels.start")

    results: dict[str, bool] = {}
    for channel_slug in get_enabled_channels():
        variant_id = channel_variant_map.get(channel_slug)
        if not variant_id:
            results[channel_slug] = False
            continue

        client = clients.get(channel_slug)
        if client is None:
            results[channel_slug] = False
            continue

        try:
            ok = await client.update_inventory(variant_id, quantity)
            results[channel_slug] = ok
            log.info(
                "channel_router.update_inventory_all_channels.channel_done",
                channel=channel_slug,
                ok=ok,
            )
        except Exception as exc:
            log.error(
                "channel_router.update_inventory_all_channels.error",
                channel=channel_slug,
                exc=str(exc),
            )
            results[channel_slug] = False

    return results
