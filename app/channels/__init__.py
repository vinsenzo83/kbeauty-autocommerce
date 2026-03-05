from __future__ import annotations

"""
app/channels/__init__.py
─────────────────────────
Sprint 9 – Multi-Channel Commerce Engine.

Public re-exports for channel clients.
"""

from app.channels.base import ChannelClient
from app.channels.shopify import ShopifyChannelClient
from app.channels.shopee import ShopeeChannelClient
from app.channels.tiktok_shop import TikTokShopClient

__all__ = [
    "ChannelClient",
    "ShopifyChannelClient",
    "ShopeeChannelClient",
    "TikTokShopClient",
]
