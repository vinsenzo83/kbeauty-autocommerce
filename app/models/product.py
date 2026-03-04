from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Product(Base):
    """
    Represents a product crawled from a supplier (e.g. StyleKorean).

    Fields
    ------
    id                  : UUID primary key (auto-generated)
    supplier            : Supplier name, default 'stylekorean'
    supplier_product_id : Unique product ID on supplier side
    supplier_product_url: Canonical product URL on supplier site
    name                : Product name
    brand               : Brand name
    price               : Regular price
    sale_price          : Discounted/sale price (nullable)
    currency            : ISO currency code, e.g. 'USD'
    stock_status        : 'in_stock', 'out_of_stock', 'unknown'
    image_urls_json     : JSON array of image URLs
    shopify_product_id  : Linked Shopify product ID (nullable, set after sync)
    created_at          : Row creation timestamp
    updated_at          : Row last-updated timestamp (auto-updated)
    """

    __tablename__ = "products"

    id                   = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    supplier             = Column(String(64),  nullable=False, default="stylekorean", index=True)
    supplier_product_id  = Column(String(128), unique=True, nullable=False, index=True)
    supplier_product_url = Column(String(1024), nullable=False)
    name                 = Column(String(512), nullable=False)
    brand                = Column(String(256), nullable=True)
    price                = Column(Numeric(12, 2), nullable=True)
    sale_price           = Column(Numeric(12, 2), nullable=True)
    currency             = Column(String(10),  nullable=False, default="USD")
    stock_status         = Column(String(32),  nullable=False, default="unknown")
    image_urls_json      = Column(JSON,        nullable=True)

    # ── Set after Shopify sync ────────────────────────────────────────────────
    shopify_product_id   = Column(String(64),  nullable=True, index=True)

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
            f"<Product id={self.id} supplier={self.supplier} "
            f"supplier_product_id={self.supplier_product_id} name={self.name!r}>"
        )
