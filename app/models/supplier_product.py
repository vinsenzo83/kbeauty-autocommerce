from __future__ import annotations

"""
app/models/supplier_product.py
───────────────────────────────
Sprint 7 – SupplierProduct ORM model.

One row per (product_id, supplier) pair.
Tracks the supplier's current price and stock status so the router
can compare across suppliers and choose the best option.
"""

import uuid
from enum import Enum as PyEnum

from sqlalchemy import Column, DateTime, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class SupplierName(str, PyEnum):
    STYLEKOREAN = "STYLEKOREAN"
    JOLSE       = "JOLSE"
    OLIVEYOUNG  = "OLIVEYOUNG"


class StockStatus(str, PyEnum):
    IN_STOCK    = "IN_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"


class SupplierProduct(Base):
    """
    Tracks per-supplier availability and price for a given product.

    Fields
    ------
    id                  : UUID primary key (auto-generated)
    product_id          : FK → products.id
    supplier            : Supplier name enum ('STYLEKOREAN' | 'JOLSE' | 'OLIVEYOUNG')
    supplier_product_id : Supplier-side product ID / SKU
    price               : Latest observed price (nullable when unavailable)
    stock_status        : 'IN_STOCK' | 'OUT_OF_STOCK'
    last_checked_at     : Timestamp of last inventory check
    """

    __tablename__ = "supplier_products"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # FK to products (nullable: Sprint 8 canonical-based rows may not have product_id)
    product_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    supplier = Column(
        String(32),
        nullable=False,
        index=True,
    )

    # Sprint 8: canonical anchor (nullable for backward compat during migration)
    canonical_product_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    supplier_product_id = Column(Text, nullable=False)

    # Sprint 8: URL used by the crawler for this supplier's listing
    supplier_product_url = Column(Text, nullable=True)

    price = Column(Numeric(12, 2), nullable=True)

    stock_status = Column(
        String(16),
        nullable=False,
        default=StockStatus.IN_STOCK.value,
        index=True,
    )

    last_checked_at = Column(DateTime(timezone=True), nullable=True)

    # Audit timestamps
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
            f"<SupplierProduct product_id={self.product_id} "
            f"supplier={self.supplier} price={self.price} "
            f"stock={self.stock_status}>"
        )
