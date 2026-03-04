from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from app.suppliers.base import SupplierClient
from app.suppliers.stylekorean import StyleKoreanClient

if TYPE_CHECKING:
    from app.models.order import Order

logger = structlog.get_logger(__name__)


def choose_supplier(order: "Order") -> SupplierClient:
    """
    Return the appropriate SupplierClient for a given order.

    MVP routing logic
    -----------------
    Always returns StyleKoreanClient (playwright mode).

    Future routing hooks
    --------------------
    - Route by brand / product tag stored in line_items_json.
    - Route by country in shipping_address_json.
    - Route by time-of-day SLA requirements.
    - A/B between suppliers for load balancing.
    """
    logger.debug(
        "supplier_router.chose",
        supplier=StyleKoreanClient.name,
        order_id=str(order.id),
    )
    return StyleKoreanClient(mode="playwright", headless=True)
