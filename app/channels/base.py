from __future__ import annotations

"""
app/channels/base.py
─────────────────────
Sprint 9 – Abstract ChannelClient interface.

All channel implementations (Shopify, Shopee, TikTok Shop …) must inherit
from ChannelClient and implement all abstract methods.

Design goals
------------
* All methods are async so they can be freely awaited in Celery tasks
  (which run their own asyncio event loop).
* All implementations must be fully mockable – no real network calls in
  unit tests.
* The interface is intentionally minimal; per-channel quirks live in
  the concrete implementations.
"""

from abc import ABC, abstractmethod
from typing import Any


class ChannelClient(ABC):
    """
    Abstract base class for all sales-channel clients.

    Parameters
    ----------
    channel_name : str
        Canonical slug for this channel (e.g. 'shopify', 'shopee',
        'tiktok_shop').  Used for logging and DB writes.
    """

    def __init__(self, channel_name: str) -> None:
        self.channel_name = channel_name

    # ── Product management ────────────────────────────────────────────────────

    @abstractmethod
    async def create_product(
        self,
        canonical_product: Any,
        *,
        price: float | None = None,
    ) -> dict[str, Any]:
        """
        Create (or register) a product on the channel.

        Parameters
        ----------
        canonical_product :
            CanonicalProduct ORM instance or dict with at minimum
            ``canonical_sku``, ``name``, ``brand``.
        price :
            Override sell price.  If None, derive from canonical pricing.

        Returns
        -------
        dict with at minimum:
            ``external_product_id``  : str  – platform product ID
            ``external_variant_id``  : str  – platform variant/SKU ID
            ``price``                : float
            ``currency``             : str
        """

    @abstractmethod
    async def update_price(
        self,
        external_variant_id: str,
        new_price: float,
        currency: str = "USD",
    ) -> bool:
        """
        Update the sell price of an existing variant/listing.

        Returns True on success, False on error / stub no-op.
        """

    @abstractmethod
    async def update_inventory(
        self,
        external_variant_id: str,
        quantity: int,
    ) -> bool:
        """
        Update the available inventory quantity for a variant.

        Returns True on success, False on error / stub no-op.
        """

    @abstractmethod
    async def fetch_orders(
        self,
        *,
        limit: int = 50,
        status: str = "pending",
    ) -> list[dict[str, Any]]:
        """
        Fetch recent orders from the channel.

        Each dict must contain at minimum:
            ``external_order_id``       : str
            ``external_product_id``     : str | None
            ``external_variant_id``     : str | None
            ``quantity``                : int
            ``price``                   : float
            ``currency``                : str
            ``status``                  : str
        """
