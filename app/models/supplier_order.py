from __future__ import annotations

"""
app/models/supplier_order.py
──────────────────────────────
Sprint 14 – ORM model for the supplier order fulfillment lifecycle.

Table: supplier_orders
One row per (channel_order_id, supplier) pair.

Status lifecycle
----------------
pending   → placed → confirmed → shipped → delivered
         ↘ failed  (any stage can fail)

Failure reason codes
--------------------
NO_SUPPLIER_AVAILABLE     – no IN_STOCK supplier found
SUPPLIER_API_ERROR        – network / API call to supplier failed
PAYMENT_FAILED            – supplier rejected payment
OUT_OF_STOCK_AFTER_CHECK  – stock confirmed out-of-stock during placement
MAX_RETRIES_EXCEEDED      – exhausted retry attempts
"""

import uuid

from sqlalchemy import Column, DateTime, Numeric, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# ── Status constants ──────────────────────────────────────────────────────────

class SupplierOrderStatus:
    PENDING   = "pending"
    PLACED    = "placed"
    CONFIRMED = "confirmed"
    SHIPPED   = "shipped"
    DELIVERED = "delivered"
    FAILED    = "failed"


# ── Failure reason constants ──────────────────────────────────────────────────

class FailureReason:
    NO_SUPPLIER_AVAILABLE    = "NO_SUPPLIER_AVAILABLE"
    SUPPLIER_API_ERROR       = "SUPPLIER_API_ERROR"
    PAYMENT_FAILED           = "PAYMENT_FAILED"
    OUT_OF_STOCK_AFTER_CHECK = "OUT_OF_STOCK_AFTER_CHECK"
    MAX_RETRIES_EXCEEDED     = "MAX_RETRIES_EXCEEDED"


# ── ORM Model ─────────────────────────────────────────────────────────────────

class SupplierOrder(Base):
    """
    Tracks one supplier fulfillment attempt for a channel order.

    UNIQUE on (channel_order_id, supplier) — only one active attempt
    per supplier per inbound order.  If a supplier fails, create a new
    row with a different supplier name.
    """

    __tablename__ = "supplier_orders"
    __allow_unmapped__ = True

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # FK to channel_orders_v2.id (not enforced at ORM level for flexibility)
    channel_order_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Supplier identity
    supplier = Column(String(64), nullable=False)

    # Supplier-side reference populated after successful placement
    supplier_order_id = Column(String(128), nullable=True)
    supplier_status   = Column(String(32),  nullable=True)

    # Tracking — populated once the order is shipped
    tracking_number = Column(String(128), nullable=True)
    tracking_carrier = Column(String(64), nullable=True)

    # Cost
    cost     = Column(Numeric(18, 6), nullable=True)
    currency = Column(String(8),  nullable=False, default="USD")

    # Internal lifecycle status
    status = Column(String(32), nullable=False, default=SupplierOrderStatus.PENDING)

    # Failure info
    failure_reason = Column(String(256), nullable=True)
    retry_count    = Column(SmallInteger, nullable=False, default=0)

    # Audit timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<SupplierOrder id={self.id} supplier={self.supplier!r} "
            f"status={self.status!r} channel_order={self.channel_order_id}>"
        )
