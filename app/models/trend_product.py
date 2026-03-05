from __future__ import annotations

"""
app/models/trend_product.py
────────────────────────────
Sprint 15 – ORM model for raw trend signals collected from external sources.

Table: trend_products
One row per (source, external_id) — represents one trending product observed
from a given data source (TikTok, Amazon bestsellers, Shopee trending, etc.)

Score scale
-----------
trend_score : 0.0 – 10.0 (normalised within each source collector)
              10.0 = highest trending signal
"""

import uuid

from sqlalchemy import Column, DateTime, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class TrendProduct(Base):
    """
    Raw trend signal for a product observed from an external source.

    Columns
    -------
    id              : UUID primary key
    source          : Collector identifier, e.g. 'tiktok', 'amazon_bestsellers'
    external_id     : Source-native product/content id (ASIN, video-id, etc.)
    name            : Product title as seen on the source
    brand           : Brand name (nullable)
    category        : Product category / department (nullable)
    trend_score     : Normalised trending strength – 0.0 to 10.0
    raw_data_json   : Full source JSON payload (for audit / re-processing)
    collected_at    : Timestamp when the signal was collected
    created_at / updated_at : Audit timestamps
    """

    __tablename__ = "trend_products"
    __allow_unmapped__ = True

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    source      = Column(String(64),  nullable=False, index=True)
    external_id = Column(String(256), nullable=False)

    name     = Column(Text,        nullable=False)
    brand    = Column(String(128), nullable=True)
    category = Column(String(128), nullable=True)

    # Normalised trend score: 0.0 – 10.0
    trend_score = Column(Numeric(8, 4), nullable=False, default=0)

    # Full source payload stored as JSON string
    raw_data_json = Column(Text, nullable=True)

    # When the signal was observed (may differ from created_at for backfills)
    collected_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<TrendProduct id={self.id} source={self.source!r} "
            f"name={self.name!r} score={self.trend_score}>"
        )
