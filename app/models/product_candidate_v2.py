from __future__ import annotations

"""
app/models/product_candidate_v2.py
────────────────────────────────────
Sprint 17 – ORM model for AI Discovery Engine v2 product candidates.

Table: product_candidates_v2

Score formula
─────────────
score = amazon_rank_score * 0.35
      + supplier_rank_score * 0.25
      + margin_score        * 0.20
      + review_score        * 0.10
      + competition_score   * 0.10

Status lifecycle
────────────────
candidate  →  published   (auto-publish or admin action)
           →  rejected    (admin rejection or score below threshold)

Notes
─────
• Uses a separate table (product_candidates_v2) so Sprint 15 product_candidates
  rows remain untouched.
• Unique partial index on (canonical_product_id) WHERE status='candidate'
  prevents duplicate active candidates for the same product.
"""

import uuid

from sqlalchemy import Column, DateTime, Float, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# ── Status constants ──────────────────────────────────────────────────────────

class CandidateStatusV2:
    """Sprint 17 candidate status enum (plain string constants for SQLite compat)."""
    CANDIDATE = "candidate"
    PUBLISHED = "published"
    REJECTED  = "rejected"

    ALL = ("candidate", "published", "rejected")


# ── ORM Model ─────────────────────────────────────────────────────────────────

class ProductCandidateV2(Base):
    """
    Sprint 17 scored product candidate from the AI Discovery Engine.

    Columns
    -------
    id                   : UUID primary key
    canonical_product_id : FK → canonical_products.id (soft reference)
    score                : Final weighted composite (0.0 – 1.0)
    amazon_rank_score    : Amazon bestseller rank signal  (weight 0.35)
    supplier_rank_score  : Supplier availability / rank   (weight 0.25)
    margin_score         : (price – cost) / price headroom (weight 0.20)
    review_score         : Review rating normalised signal (weight 0.10)
    competition_score    : Inverse competitor density      (weight 0.10)
    status               : candidate | published | rejected
    notes                : Free-text notes / rejection reason
    created_at / updated_at : Audit timestamps
    """

    __tablename__ = "product_candidates_v2"
    __allow_unmapped__ = True

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Soft FK – not enforced at DB level to support SQLite in tests
    canonical_product_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    # ── Sprint 17 score components (0.0 – 1.0) ───────────────────────────────
    score             = Column(Float, nullable=False, default=0.0)
    amazon_rank_score = Column(Float, nullable=False, default=0.0)
    supplier_rank_score = Column(Float, nullable=False, default=0.0)
    margin_score      = Column(Float, nullable=False, default=0.0)
    review_score      = Column(Float, nullable=False, default=0.0)
    competition_score = Column(Float, nullable=False, default=0.0)

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    status = Column(String(32), nullable=False, default=CandidateStatusV2.CANDIDATE,
                    index=True)
    notes  = Column(Text, nullable=True)

    # ── Audit ─────────────────────────────────────────────────────────────────
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

    def to_dict(self) -> dict:
        """Serialise to plain dict for API responses."""
        return {
            "id":                    str(self.id),
            "canonical_product_id":  str(self.canonical_product_id),
            "score":                 round(float(self.score), 4),
            "amazon_rank_score":     round(float(self.amazon_rank_score), 4),
            "supplier_rank_score":   round(float(self.supplier_rank_score), 4),
            "margin_score":          round(float(self.margin_score), 4),
            "review_score":          round(float(self.review_score), 4),
            "competition_score":     round(float(self.competition_score), 4),
            "status":                self.status,
            "notes":                 self.notes,
            "created_at":            self.created_at.isoformat() if self.created_at else None,
            "updated_at":            self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<ProductCandidateV2 id={self.id} "
            f"canonical={self.canonical_product_id} "
            f"score={self.score:.4f} status={self.status!r}>"
        )
