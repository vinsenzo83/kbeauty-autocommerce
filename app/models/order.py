from __future__ import annotations

import uuid
from enum import Enum as PyEnum

from sqlalchemy import Column, DateTime, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class OrderStatus(str, PyEnum):
    RECEIVED  = "RECEIVED"
    VALIDATED = "VALIDATED"
    PLACING   = "PLACING"
    PLACED    = "PLACED"
    SHIPPED   = "SHIPPED"
    FAILED    = "FAILED"


class Order(Base):
    __tablename__ = "orders"

    id                    = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    shopify_order_id      = Column(String(64),   unique=True, nullable=False, index=True)
    email                 = Column(String(255),  nullable=True)
    total_price           = Column(Numeric(12, 2), nullable=True)
    currency              = Column(String(10),   nullable=True)
    shipping_address_json = Column(JSON,         nullable=True)
    line_items_json       = Column(JSON,         nullable=True)
    financial_status      = Column(String(64),   nullable=True)
    status                = Column(String(16),   nullable=False, default=OrderStatus.RECEIVED)
    fail_reason           = Column(Text,         nullable=True)

    # ── Sprint 2: supplier placement ─────────────────────────────────────────
    supplier              = Column(String(64),   nullable=True)
    supplier_order_id     = Column(String(128),  nullable=True)
    placed_at             = Column(DateTime(timezone=True), nullable=True)

    # ── Sprint 3: tracking ────────────────────────────────────────────────────
    tracking_number       = Column(String(128),  nullable=True)
    tracking_url          = Column(String(512),  nullable=True)
    shipped_at            = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<Order id={self.id} shopify_order_id={self.shopify_order_id} "
            f"status={self.status} supplier={self.supplier}>"
        )
