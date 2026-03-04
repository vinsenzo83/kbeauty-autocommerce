from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.order import Order


class SupplierClient(ABC):
    """
    Abstract base class for all supplier integrations.

    Every concrete supplier must implement:
      - create_order  – place an order with the supplier and return their order ID.
      - get_tracking  – fetch tracking info for a previously placed order.

    Implementations should be stateless where possible; session / browser
    lifecycle management is the responsibility of the concrete class.
    """

    # Human-readable name used for logging and stored in Order.supplier
    name: str = "unknown"

    @abstractmethod
    async def create_order(self, order: "Order") -> str:
        """
        Place an order with the supplier.

        Args:
            order: The ORM Order instance (read-only – do NOT mutate here).

        Returns:
            supplier_order_id: The supplier's confirmation / order reference ID.

        Raises:
            SupplierError: on any placement failure.
        """
        ...

    @abstractmethod
    async def get_tracking(self, supplier_order_id: str) -> tuple[str | None, str | None]:
        """
        Fetch tracking information for a placed order.

        Args:
            supplier_order_id: The ID previously returned by create_order.

        Returns:
            (tracking_number, tracking_url) – either or both may be None
            if tracking is not yet available.
        """
        ...


class SupplierError(Exception):
    """Raised by SupplierClient implementations on placement / tracking failure."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.message  = message
        self.retryable = retryable

    def __repr__(self) -> str:
        return f"SupplierError({self.message!r}, retryable={self.retryable})"
