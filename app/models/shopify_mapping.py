from __future__ import annotations

"""
app/models/shopify_mapping.py
──────────────────────────────
Sprint 8 – ShopifyMapping ORM model.

Maps one canonical_product to its Shopify product / variant / inventory IDs.
One-to-one: a canonical_product can have at most one Shopify variant.
"""

import uuid

from sqlalchemy import Column, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class ShopifyMapping(Base):
    """
    Shopify product/variant/inventory IDs for a canonical product.

    Fields
    ------
    id                      : UUID PK
    canonical_product_id    : FK → canonical_products.id (unique)
    shopify_product_id      : Shopify product ID string
    shopify_variant_id      : Shopify variant ID string (unique)
    shopify_inventory_item_id : Shopify inventory item ID (nullable)
    currency                : ISO currency code (default 'USD')
    """

    __tablename__ = "shopify_mappings"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    canonical_product_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
        index=True,
    )

    shopify_product_id    = Column(Text, nullable=False)
    shopify_variant_id    = Column(Text, nullable=False, unique=True)
    shopify_inventory_item_id = Column(Text, nullable=True)
    currency              = Column(String(10), nullable=False, default="USD")

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
            f"<ShopifyMapping canonical_product_id={self.canonical_product_id} "
            f"shopify_variant_id={self.shopify_variant_id!r}>"
        )
