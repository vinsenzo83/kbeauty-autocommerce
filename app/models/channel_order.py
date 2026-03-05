"""
app/models/channel_order.py
────────────────────────────
Sprint 10 – Canonical order row for multi-channel webhooks.
"""
from __future__ import annotations

import uuid
from sqlalchemy import Column, DateTime, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class ChannelOrderV2(Base):
    """
    Unified order record created from any channel webhook.

    Identified by (channel, external_order_id) — UNIQUE together.
    """
    __tablename__ = "channel_orders_v2"

    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_order_id = Column(String(128), nullable=False, index=True)
    channel           = Column(String(32),  nullable=False, index=True)
    currency          = Column(String(10),  nullable=True)
    total_price       = Column(Numeric(12, 2), nullable=True)
    buyer_name        = Column(String(255), nullable=True)
    buyer_email       = Column(String(255), nullable=True)
    status            = Column(String(32),  nullable=False, default="received")
    raw_payload       = Column(JSONB,       nullable=False, default=dict)
    webhook_event_id  = Column(String(128), nullable=True)
    created_at        = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at        = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<ChannelOrderV2 channel={self.channel} "
            f"ext={self.external_order_id} status={self.status}>"
        )
