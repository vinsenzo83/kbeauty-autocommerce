from __future__ import annotations

"""
app/models/market_price.py
───────────────────────────
Sprint 13 – ORM models for market price intelligence and repricing.

Tables
------
MarketSource      : Named competitor / channel price sources.
MarketPrice       : Competitor price record per (canonical_product, source).
RepricingRun      : One repricing run (batch job).
RepricingRunItem  : Per-product outcome in a repricing run.
"""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# ─────────────────────────────────────────────────────────────────────────────
class MarketSource(Base):
    """
    A named source of competitor market prices.

    Examples: 'competitor_manual', 'amazon', 'shopee', 'tiktok_shop'
    type: 'api' | 'manual' | 'import'
    """

    __tablename__ = "market_sources"
    __allow_unmapped__ = True

    id   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(64),  nullable=False, unique=True)
    type = Column(String(16),  nullable=False, default="manual")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<MarketSource name={self.name!r} type={self.type!r}>"


# ─────────────────────────────────────────────────────────────────────────────
class MarketPrice(Base):
    """
    Competitor price snapshot for one canonical product from one source.

    Unique on (canonical_product_id, source_id).
    """

    __tablename__ = "market_prices"
    __allow_unmapped__ = True

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_product_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    source_id            = Column(
        UUID(as_uuid=True),
        ForeignKey("market_sources.id"),
        nullable=False,
        index=True,
    )
    external_url = Column(String(512), nullable=True)
    external_sku = Column(String(128), nullable=True)
    currency     = Column(String(8),   nullable=False, default="USD")
    price        = Column(Numeric(18, 6), nullable=False)
    in_stock     = Column(Boolean,     nullable=False, default=True)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at   = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<MarketPrice canonical={self.canonical_product_id} "
            f"price={self.price} currency={self.currency}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
class RepricingRun(Base):
    """
    One repricing batch run.

    status: running / success / failed / partial
    """

    __tablename__ = "repricing_runs"
    __allow_unmapped__ = True

    id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel = Column(String(32),  nullable=False, default="shopify")
    status  = Column(String(16),  nullable=False, default="running")
    dry_run = Column(Boolean,     nullable=False, default=False)

    target_count  = Column(Integer, nullable=False, default=0)
    updated_count = Column(Integer, nullable=False, default=0)
    skipped_count = Column(Integer, nullable=False, default=0)
    failed_count  = Column(Integer, nullable=False, default=0)

    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<RepricingRun id={self.id} status={self.status!r} "
            f"dry_run={self.dry_run}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
class RepricingRunItem(Base):
    """
    Per-product outcome inside a repricing run.

    status: updated / skipped / failed
    reason: NO_CHANGE / MISSING_SHOPIFY_MAPPING / NO_IN_STOCK_SUPPLIER / etc.
    """

    __tablename__ = "repricing_run_items"
    __allow_unmapped__ = True

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repricing_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("repricing_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    canonical_product_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    old_price         = Column(Numeric(12, 2), nullable=True)
    recommended_price = Column(Numeric(12, 2), nullable=True)
    applied_price     = Column(Numeric(12, 2), nullable=True)

    status = Column(String(16),  nullable=False, default="skipped")
    reason = Column(String(256), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<RepricingRunItem run={self.repricing_run_id} "
            f"canonical={self.canonical_product_id} status={self.status!r}>"
        )
