from __future__ import annotations

"""
app/suppliers/base.py
──────────────────────
Abstract base class for all supplier integrations.

Sprint 14 adds two new abstract methods to support the auto-fulfillment pipeline:
  - place_order(order_payload)  → PlacedOrder
  - get_order_status(supplier_order_id) → OrderStatus

Legacy methods kept for backward compatibility:
  - create_order(order)  → str  (Sprint 2–7 pipeline)
  - get_tracking(supplier_order_id)  → (tracking_number, tracking_url)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.order import Order


# ─────────────────────────────────────────────────────────────────────────────
# Data structures returned by Sprint 14 supplier methods
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PlacedOrder:
    """
    Result returned by SupplierClient.place_order().

    supplier_order_id : The supplier's order reference number / ID.
    status            : Initial status string (e.g. 'placed', 'pending').
    cost              : Unit cost charged by the supplier (if available).
    currency          : ISO 4217 currency code (default 'USD').
    raw               : Raw response dict for debugging / audit.
    """
    supplier_order_id: str
    status: str = "placed"
    cost: float | None = None
    currency: str = "USD"
    raw: dict[str, Any] | None = None


@dataclass
class OrderStatus:
    """
    Result returned by SupplierClient.get_order_status().

    supplier_order_id : Echo of the queried ID.
    status            : Current status from supplier
                        ('placed' | 'confirmed' | 'shipped' | 'delivered' | 'failed').
    tracking_number   : Tracking code (populated when status == 'shipped').
    tracking_carrier  : Carrier name (e.g. 'DHL', 'FedEx').
    raw               : Raw response dict.
    """
    supplier_order_id: str
    status: str
    tracking_number: str | None = None
    tracking_carrier: str | None = None
    raw: dict[str, Any] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Exception
# ─────────────────────────────────────────────────────────────────────────────

class SupplierError(Exception):
    """Raised by SupplierClient implementations on placement / tracking failure."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.message   = message
        self.retryable = retryable

    def __repr__(self) -> str:
        return f"SupplierError({self.message!r}, retryable={self.retryable})"


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base
# ─────────────────────────────────────────────────────────────────────────────

class SupplierClient(ABC):
    """
    Abstract base class for all supplier integrations.

    Sprint 14 interface (new):
      - place_order(order_payload)         → PlacedOrder
      - get_order_status(supplier_order_id)→ OrderStatus

    Legacy interface (Sprint 2–7, kept for backward compatibility):
      - create_order(order)                → str
      - get_tracking(supplier_order_id)    → (tracking_number, tracking_url)
    """

    # Human-readable name used for logging and stored in Order.supplier
    name: str = "unknown"

    # ── Sprint 14: new fulfillment methods ────────────────────────────────────

    @abstractmethod
    async def place_order(self, order_payload: dict[str, Any]) -> PlacedOrder:
        """
        Place an order with the supplier.

        Parameters
        ----------
        order_payload : dict with keys:
            - canonical_sku       : str
            - supplier_product_id : str
            - quantity            : int  (default 1)
            - shipping_address    : dict (name, address1, city, country, zip)
            - buyer_name          : str | None
            - buyer_email         : str | None
            - channel_order_id    : str  (for reference / deduplication)

        Returns
        -------
        PlacedOrder dataclass.

        Raises
        ------
        SupplierError on any placement failure.
        """
        ...

    @abstractmethod
    async def get_order_status(self, supplier_order_id: str) -> OrderStatus:
        """
        Fetch the current status of a previously placed order.

        Parameters
        ----------
        supplier_order_id : The ID returned by place_order().

        Returns
        -------
        OrderStatus dataclass.

        Raises
        ------
        SupplierError if the status cannot be retrieved.
        """
        ...

    # ── Legacy: Sprint 2–7 methods (kept for backward compatibility) ──────────

    @abstractmethod
    async def create_order(self, order: "Order") -> str:
        """
        Place an order with the supplier (legacy Sprint 2–7 signature).

        Returns
        -------
        supplier_order_id : str

        Raises
        ------
        SupplierError on any placement failure.
        """
        ...

    @abstractmethod
    async def get_tracking(self, supplier_order_id: str) -> tuple[str | None, str | None]:
        """
        Fetch tracking information for a placed order (legacy signature).

        Returns
        -------
        (tracking_number, tracking_url) – either or both may be None.
        """
        ...
