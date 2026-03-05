from __future__ import annotations

"""
app/models/publish_job.py
─────────────────────────
Sprint 12 – ORM models for publish_jobs and publish_job_items.

publish_jobs     : One row per "publish top-N to Shopify" run.
publish_job_items: One row per canonical product in a run.
"""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class PublishJob(Base):
    __allow_unmapped__ = True
    """
    Represents a single publish run (e.g. "publish top-20 to Shopify").

    Fields
    ------
    id              : UUID primary key
    created_at      : When the run started
    updated_at      : Last update timestamp
    channel         : Target channel (default 'shopify')
    status          : running / success / failed / partial
    dry_run         : True = simulate, no real Shopify calls
    target_count    : How many products were selected
    published_count : Successfully published/updated
    failed_count    : Errors during publish
    skipped_count   : Already up-to-date or skipped
    notes           : Free-text for errors, summary etc.
    """

    __tablename__ = "publish_jobs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
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

    channel         = Column(String(32),  nullable=False, default="shopify")
    status          = Column(String(16),  nullable=False, default="running")
    dry_run         = Column(Boolean,     nullable=False, default=False)
    target_count    = Column(Integer,     nullable=False, default=0)
    published_count = Column(Integer,     nullable=False, default=0)
    failed_count    = Column(Integer,     nullable=False, default=0)
    skipped_count   = Column(Integer,     nullable=False, default=0)
    notes           = Column(Text,        nullable=True)

    # Relationship
    items = relationship(
        "PublishJobItem",
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<PublishJob id={self.id} channel={self.channel!r} "
            f"status={self.status!r} dry_run={self.dry_run}>"
        )


class PublishJobItem(Base):
    """
    Per-product outcome row for a publish run.

    Fields
    ------
    id                   : UUID PK
    publish_job_id       : FK → publish_jobs.id (CASCADE DELETE)
    canonical_product_id : FK → canonical_products.id
    shopify_product_id   : Shopify product ID (set after publish; None in dry_run)
    status               : queued / published / failed / skipped
    reason               : Human-readable reason (failure cause, skip reason, etc.)
    created_at / updated_at
    """

    __tablename__ = "publish_job_items"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    publish_job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("publish_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    canonical_product_id = Column(
        UUID(as_uuid=True),
        # FK checked at DB level via migration; not declared here to avoid
        # cross-Base metadata conflicts in test environments.
        nullable=False,
        index=True,
    )

    shopify_product_id = Column(String(128), nullable=True)
    status             = Column(String(16),  nullable=False, default="queued")
    reason             = Column(String(512), nullable=True)

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

    __allow_unmapped__ = True

    # Relationship back to job
    job = relationship("PublishJob", back_populates="items")

    def __repr__(self) -> str:
        return (
            f"<PublishJobItem job={self.publish_job_id} "
            f"canonical={self.canonical_product_id} status={self.status!r}>"
        )
