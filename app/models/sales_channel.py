from __future__ import annotations

"""
app/models/sales_channel.py
────────────────────────────
Sprint 9 – ORM models for the Multi-Channel Commerce Engine.

Tables
------
SalesChannel     → sales_channels
ChannelProduct   → channel_products
ChannelOrder     → channel_orders
"""

import uuid

from sqlalchemy import Boolean, Column, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class SalesChannel(Base):
    """
    Registry of sales channels supported by the system.

    name    : unique slug  (e.g. 'shopify', 'shopee', 'tiktok_shop')
    type    : channel type (e.g. 'owned_store', 'marketplace')
    enabled : soft-disable flag
    """

    __tablename__ = "sales_channels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    name    = Column(String(32),  unique=True, nullable=False, index=True)
    type    = Column(String(32),  nullable=False, default="marketplace")
    enabled = Column(Boolean,     nullable=False, default=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<SalesChannel name={self.name!r} enabled={self.enabled}>"


class ChannelProduct(Base):
    """
    Maps a canonical_product to its external listing on a specific channel.

    canonical_product_id : FK → canonical_products.id
    channel              : slug of the channel ('shopify', 'shopee', …)
    external_product_id  : platform product ID
    external_variant_id  : platform variant ID (unique per channel)
    price                : last-synced price
    currency             : ISO currency code
    status               : 'active' | 'inactive' | 'error'
    """

    __tablename__ = "channel_products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)

    # Using String instead of FK UUID for lightweight SQLite compat in tests
    canonical_product_id = Column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    channel             = Column(String(32),   nullable=False, index=True)
    external_product_id = Column(String(128),  nullable=True)
    external_variant_id = Column(String(128),  nullable=True)
    price               = Column(Numeric(12, 2), nullable=True)
    currency            = Column(String(8),    nullable=False, default="USD")
    status              = Column(String(32),   nullable=False, default="active", index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<ChannelProduct channel={self.channel!r} "
            f"ext_id={self.external_product_id!r} status={self.status!r}>"
        )


class ChannelOrder(Base):
    """
    An order received from any sales channel.

    channel           : slug of the originating channel
    external_order_id : platform order ID (unique per channel)
    canonical_product_id : linked canonical product (nullable)
    quantity          : units ordered
    price             : unit price at time of order
    status            : 'pending' | 'processing' | 'completed' | 'cancelled'
    """

    __tablename__ = "channel_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)

    channel           = Column(String(32),   nullable=False, index=True)
    external_order_id = Column(String(128),  nullable=False)
    canonical_product_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    quantity          = Column(Integer,       nullable=False, default=1)
    price             = Column(Numeric(12, 2), nullable=True)
    currency          = Column(String(8),    nullable=False, default="USD")
    status            = Column(String(32),   nullable=False, default="pending", index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<ChannelOrder channel={self.channel!r} "
            f"ext_order={self.external_order_id!r} status={self.status!r}>"
        )
