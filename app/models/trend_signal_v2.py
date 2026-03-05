from __future__ import annotations

"""
app/models/trend_signal_v2.py
──────────────────────────────
Sprint 18 – ORM models for the Trend Signals v2 collection pipeline.

Tables
------
trend_sources      – registered signal sources (amazon, tiktok, supplier)
trend_items        – raw scraped/mocked items from each source
mention_dictionary – normalized phrase → canonical_product mapping
mention_signals    – daily aggregated mention counts + scores per product
"""

import uuid

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Index, Integer,
    Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# A) TrendSource
# ─────────────────────────────────────────────────────────────────────────────

class TrendSource(Base):
    """
    Registered trend signal source.

    source  : 'amazon' | 'tiktok' | 'supplier'
    name    : unique human label, e.g. 'amazon_bestsellers_us'
    """
    __tablename__ = "trend_sources"
    __allow_unmapped__ = True

    __table_args__ = (
        UniqueConstraint("source", "name", name="uq_trend_sources_source_name"),
    )

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source     = Column(String(32), nullable=False, index=True)
    name       = Column(String(128), nullable=False)
    is_enabled = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<TrendSource source={self.source!r} name={self.name!r} enabled={self.is_enabled}>"


# ─────────────────────────────────────────────────────────────────────────────
# B) TrendItem
# ─────────────────────────────────────────────────────────────────────────────

class TrendItem(Base):
    """
    Raw trend data item from a source (Amazon product listing, TikTok doc, etc.).

    Columns
    -------
    external_id  : Amazon ASIN, TikTok video ID, etc.
    rank         : Bestseller rank (Amazon), or derived rank
    rating       : Product rating (0–5)
    review_count : Number of reviews
    raw_json     : Full raw payload for future re-processing
    """
    __tablename__ = "trend_items"
    __allow_unmapped__ = True

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id   = Column(UUID(as_uuid=True), nullable=False, index=True)
    observed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    external_id  = Column(String(128), nullable=True, index=True)
    title        = Column(String(512), nullable=True)
    brand        = Column(String(256), nullable=True)
    category     = Column(String(256), nullable=True)
    rank         = Column(Integer, nullable=True)
    price        = Column(Numeric(12, 2), nullable=True)
    currency     = Column(String(8), nullable=True)
    rating       = Column(Numeric(3, 2), nullable=True)
    review_count = Column(Integer, nullable=True)
    raw_json     = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id":           str(self.id),
            "source_id":    str(self.source_id),
            "external_id":  self.external_id,
            "title":        self.title,
            "brand":        self.brand,
            "category":     self.category,
            "rank":         self.rank,
            "price":        float(self.price) if self.price else None,
            "rating":       float(self.rating) if self.rating else None,
            "review_count": self.review_count,
            "observed_at":  self.observed_at.isoformat() if self.observed_at else None,
        }

    def __repr__(self) -> str:
        return f"<TrendItem source={self.source_id} rank={self.rank} title={self.title!r}>"


# ─────────────────────────────────────────────────────────────────────────────
# C) MentionDictionary
# ─────────────────────────────────────────────────────────────────────────────

class MentionDictionary(Base):
    """
    Maps a normalized phrase to a canonical product.

    Example: phrase='cosrx snail mucin' → canonical_product_id=<uuid>
    Used by extract_mentions() for fast substring matching.
    """
    __tablename__ = "mention_dictionary"
    __allow_unmapped__ = True

    __table_args__ = (
        UniqueConstraint(
            "canonical_product_id", "phrase",
            name="uq_mention_dict_canonical_phrase",
        ),
    )

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_product_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    phrase               = Column(String(256), nullable=False, index=True)
    weight               = Column(Float, nullable=False, default=1.0)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (f"<MentionDictionary phrase={self.phrase!r} "
                f"cp={self.canonical_product_id} weight={self.weight}>")


# ─────────────────────────────────────────────────────────────────────────────
# D) MentionSignal
# ─────────────────────────────────────────────────────────────────────────────

class MentionSignal(Base):
    """
    Daily aggregated mention signal for a canonical product from a source.

    Columns
    -------
    mentions  : Raw mention count for the day
    velocity  : Growth rate heuristic (0.0 – 1.0+)
    score     : Final signal score = mentions * (1 + velocity)
    raw_json  : Intermediate debug data (matched phrases, doc ids, etc.)
    """
    __tablename__ = "mention_signals"
    __allow_unmapped__ = True

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_product_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    source_id            = Column(UUID(as_uuid=True), nullable=False, index=True)
    observed_at          = Column(DateTime(timezone=True), server_default=func.now(),
                                  nullable=False)
    mentions             = Column(Integer, nullable=False, default=0)
    velocity             = Column(Float, nullable=False, default=0.0)
    score                = Column(Float, nullable=False, default=0.0)
    raw_json             = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id":                    str(self.id),
            "canonical_product_id":  str(self.canonical_product_id),
            "source_id":             str(self.source_id),
            "mentions":              self.mentions,
            "velocity":              round(self.velocity, 4),
            "score":                 round(self.score, 4),
            "observed_at":           self.observed_at.isoformat() if self.observed_at else None,
        }

    def __repr__(self) -> str:
        return (f"<MentionSignal cp={self.canonical_product_id} "
                f"score={self.score:.2f} mentions={self.mentions}>")
