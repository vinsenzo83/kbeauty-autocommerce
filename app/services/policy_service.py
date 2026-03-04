from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class PolicyViolation(Exception):
    """Raised when an order fails policy validation."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def validate_order_policy(order_data: dict[str, Any]) -> None:
    """
    Validate minimal order policy rules.

    Rules:
    1. financial_status must be 'paid'.
    2. shipping_address must be present and non-empty.

    Raises PolicyViolation with a descriptive reason on the first failure.
    """
    financial_status = order_data.get("financial_status", "")
    if financial_status != "paid":
        raise PolicyViolation(
            f"financial_status is '{financial_status}', expected 'paid'"
        )

    shipping_address = order_data.get("shipping_address")
    if not shipping_address:
        raise PolicyViolation("shipping_address is missing or empty")

    logger.debug(
        "policy.validated",
        shopify_order_id=str(order_data.get("id", "unknown")),
    )
