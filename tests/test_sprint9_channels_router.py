"""
tests/test_sprint9_channels_router.py
──────────────────────────────────────
Sprint 9 – Mock-only tests for app/services/channel_router.py.

Covers:
  * get_enabled_channels() returns the three expected channels
  * publish_product_to_channels() calls create_product on each client
  * update_price_all_channels() calls update_price only for mapped channels
  * update_inventory_all_channels() calls update_inventory for mapped channels
  * Missing client → result is False/None (no crash)
  * Error in client method → result is False/None (no crash)

No real network calls.  All channel clients are MagicMock / AsyncMock.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_client(channel_name: str) -> MagicMock:
    """Return an AsyncMock-based mock channel client."""
    client = MagicMock()
    client.channel_name = channel_name
    client.create_product = AsyncMock(
        return_value={
            "external_product_id": f"{channel_name}-pid-001",
            "external_variant_id": f"{channel_name}-vid-001",
            "price":               19.99,
            "currency":            "USD",
        }
    )
    client.update_price    = AsyncMock(return_value=True)
    client.update_inventory = AsyncMock(return_value=True)
    client.fetch_orders    = AsyncMock(return_value=[])
    return client


def _make_clients() -> dict:
    return {
        "shopify":     _make_mock_client("shopify"),
        "shopee":      _make_mock_client("shopee"),
        "tiktok_shop": _make_mock_client("tiktok_shop"),
    }


def _canonical(sku: str = "brand-name-100ml") -> dict:
    return {
        "canonical_sku":  sku,
        "name":           "Test Product",
        "brand":          "TestBrand",
        "last_price":     19.99,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. get_enabled_channels
# ─────────────────────────────────────────────────────────────────────────────

def test_get_enabled_channels_returns_three():
    from app.services.channel_router import get_enabled_channels
    channels = get_enabled_channels()
    assert set(channels) == {"shopify", "shopee", "tiktok_shop"}
    assert len(channels) == 3


def test_get_enabled_channels_includes_shopify():
    from app.services.channel_router import get_enabled_channels
    assert "shopify" in get_enabled_channels()


def test_get_enabled_channels_includes_shopee():
    from app.services.channel_router import get_enabled_channels
    assert "shopee" in get_enabled_channels()


def test_get_enabled_channels_includes_tiktok_shop():
    from app.services.channel_router import get_enabled_channels
    assert "tiktok_shop" in get_enabled_channels()


# ─────────────────────────────────────────────────────────────────────────────
# 2. publish_product_to_channels
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_publish_product_calls_all_clients():
    from app.services.channel_router import publish_product_to_channels

    clients = _make_clients()
    result = await publish_product_to_channels(_canonical(), price=19.99, clients=clients)

    assert set(result.keys()) == {"shopify", "shopee", "tiktok_shop"}
    for slug, res in result.items():
        assert res is not None, f"Expected result for {slug}"
        assert res["external_product_id"].startswith(slug)


@pytest.mark.anyio
async def test_publish_product_passes_price():
    from app.services.channel_router import publish_product_to_channels

    clients = _make_clients()
    await publish_product_to_channels(_canonical(), price=25.00, clients=clients)

    for client in clients.values():
        client.create_product.assert_called_once()
        _, kwargs = client.create_product.call_args
        assert kwargs.get("price") == 25.00


@pytest.mark.anyio
async def test_publish_product_missing_client_returns_none():
    from app.services.channel_router import publish_product_to_channels

    # Only shopify client provided
    clients = {"shopify": _make_mock_client("shopify")}
    result  = await publish_product_to_channels(_canonical(), price=10.0, clients=clients)

    assert result["shopify"] is not None
    assert result["shopee"]      is None
    assert result["tiktok_shop"] is None


@pytest.mark.anyio
async def test_publish_product_client_error_returns_none():
    from app.services.channel_router import publish_product_to_channels

    clients = _make_clients()
    clients["shopee"].create_product = AsyncMock(side_effect=RuntimeError("boom"))
    result = await publish_product_to_channels(_canonical(), price=10.0, clients=clients)

    assert result["shopify"] is not None
    assert result["shopee"]  is None


# ─────────────────────────────────────────────────────────────────────────────
# 3. update_price_all_channels
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_update_price_calls_mapped_channels():
    from app.services.channel_router import update_price_all_channels

    clients = _make_clients()
    variant_map = {
        "shopify":     "shopify-vid-001",
        "shopee":      "shopee-vid-001",
        "tiktok_shop": "tiktok-vid-001",
    }
    result = await update_price_all_channels(
        _canonical(), 19.99,
        channel_variant_map=variant_map,
        clients=clients,
    )
    assert result == {"shopify": True, "shopee": True, "tiktok_shop": True}

    for client in clients.values():
        client.update_price.assert_called_once()


@pytest.mark.anyio
async def test_update_price_skips_unmapped_channels():
    from app.services.channel_router import update_price_all_channels

    clients = _make_clients()
    # Only shopify has a variant_id
    variant_map = {"shopify": "shopify-vid-001"}
    result = await update_price_all_channels(
        _canonical(), 19.99,
        channel_variant_map=variant_map,
        clients=clients,
    )
    assert result["shopify"] is True
    assert result["shopee"]      is False
    assert result["tiktok_shop"] is False

    clients["shopify"].update_price.assert_called_once()
    clients["shopee"].update_price.assert_not_called()
    clients["tiktok_shop"].update_price.assert_not_called()


@pytest.mark.anyio
async def test_update_price_client_error_returns_false():
    from app.services.channel_router import update_price_all_channels

    clients = _make_clients()
    clients["shopify"].update_price = AsyncMock(side_effect=RuntimeError("timeout"))
    variant_map = {"shopify": "shopify-vid-001"}
    result = await update_price_all_channels(
        _canonical(), 19.99,
        channel_variant_map=variant_map,
        clients=clients,
    )
    assert result["shopify"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. update_inventory_all_channels
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_update_inventory_calls_mapped_channels():
    from app.services.channel_router import update_inventory_all_channels

    clients = _make_clients()
    variant_map = {
        "shopify":     "shopify-vid-001",
        "shopee":      "shopee-vid-001",
        "tiktok_shop": "tiktok-vid-001",
    }
    result = await update_inventory_all_channels(
        _canonical(), 99,
        channel_variant_map=variant_map,
        clients=clients,
    )
    assert result == {"shopify": True, "shopee": True, "tiktok_shop": True}


@pytest.mark.anyio
async def test_update_inventory_zero_stock():
    from app.services.channel_router import update_inventory_all_channels

    clients = _make_clients()
    variant_map = {"shopee": "shopee-vid-001"}
    await update_inventory_all_channels(
        _canonical(), 0,
        channel_variant_map=variant_map,
        clients=clients,
    )
    clients["shopee"].update_inventory.assert_called_once_with("shopee-vid-001", 0)


@pytest.mark.anyio
async def test_update_inventory_skips_unmapped():
    from app.services.channel_router import update_inventory_all_channels

    clients = _make_clients()
    result = await update_inventory_all_channels(
        _canonical(), 50,
        channel_variant_map={},  # nothing mapped
        clients=clients,
    )
    assert all(v is False for v in result.values())
    for client in clients.values():
        client.update_inventory.assert_not_called()
