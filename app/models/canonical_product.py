from __future__ import annotations

"""
app/models/canonical_product.py
────────────────────────────────
Sprint 8 – CanonicalProduct ORM model.

A canonical_product represents a single real-world product identity,
independent of which supplier carries it or how it is listed on Shopify.

One canonical_product can have:
  * many SupplierProduct rows (one per supplier that carries it)
  * zero or one ShopifyMapping row (Shopify variant/inventory ids)
  * many PriceQuote rows (pricing engine history)
"""

import uuid

from sqlalchemy import Boolean, Column, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class CanonicalProduct(Base):
    """
    Canonical product identity.

    Fields
    ------
    id                  : UUID primary key
    canonical_sku       : Stable slug-like key (unique), e.g. 'some-brand-product-name-100'
    name                : Display name
    brand               : Brand name (nullable)
    size_ml             : Volume in ml (nullable)
    ean                 : EAN/barcode (nullable)
    image_urls_json     : JSON string of image URL list (nullable)

    -- Pricing engine defaults --
    pricing_enabled     : Whether auto-pricing is on for this product
    target_margin_rate  : e.g. 0.30 = 30 % margin target
    min_margin_abs      : Minimum absolute margin in USD (e.g. 3.00)
    shipping_cost_default: Default shipping cost added to computation
    last_price          : Last computed Shopify sell price
    last_price_at       : When last_price was set

    created_at / updated_at : Audit timestamps
    """

    __tablename__ = "canonical_products"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    canonical_sku = Column(Text, unique=True, nullable=False, index=True)
    name          = Column(Text, nullable=False)
    brand         = Column(Text, nullable=True)
    size_ml       = Column(Integer, nullable=True)
    ean           = Column(Text, nullable=True)
    image_urls_json = Column(Text, nullable=True)

    # ── Pricing engine fields ─────────────────────────────────────────────────
    pricing_enabled       = Column(Boolean, nullable=False, default=True)
    target_margin_rate    = Column(Numeric(6, 4),  nullable=False, default="0.30")
    min_margin_abs        = Column(Numeric(10, 2), nullable=False, default="3.00")
    shipping_cost_default = Column(Numeric(10, 2), nullable=False, default="3.00")
    last_price            = Column(Numeric(12, 2), nullable=True)
    last_price_at         = Column(DateTime(timezone=True), nullable=True)

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
            f"<CanonicalProduct id={self.id} "
            f"canonical_sku={self.canonical_sku!r} name={self.name!r}>"
        )
