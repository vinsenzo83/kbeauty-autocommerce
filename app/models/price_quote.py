from __future__ import annotations

"""
app/models/price_quote.py
──────────────────────────
Sprint 8 – PriceQuote ORM model.

Records the output of the pricing engine for audit / idempotency purposes.
Each time a price is computed for a canonical_product, one row is written here.
"""

import uuid

from sqlalchemy import Column, DateTime, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class PriceQuote(Base):
    """
    Pricing engine output for one canonical_product at one point in time.

    Fields
    ------
    id                   : UUID PK
    canonical_product_id : FK → canonical_products.id
    supplier             : Supplier whose price was used as cost basis
    supplier_price       : Supplier's price (cost)
    shipping_cost        : Shipping cost added to the computation
    fee_rate             : Platform/payment fee rate (e.g. 0.03 = 3 %)
    target_margin_rate   : Target gross margin rate (e.g. 0.30 = 30 %)
    min_margin_abs       : Minimum absolute margin enforced (USD)
    computed_price       : Raw computed sell price before rounding
    rounded_price        : Final sell price after *.99 rounding
    reason               : Human-readable note (e.g. 'min_margin_enforced')
    created_at           : Timestamp
    """

    __tablename__ = "price_quotes"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    canonical_product_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    supplier       = Column(Text,           nullable=False)
    supplier_price = Column(Numeric(12, 2), nullable=False)
    shipping_cost  = Column(Numeric(12, 2), nullable=False, default="3.00")
    fee_rate       = Column(Numeric(6, 4),  nullable=False, default="0.03")
    target_margin_rate = Column(Numeric(6, 4),  nullable=False, default="0.30")
    min_margin_abs     = Column(Numeric(10, 2), nullable=False, default="3.00")
    computed_price     = Column(Numeric(12, 2), nullable=False)
    rounded_price      = Column(Numeric(12, 2), nullable=False)
    reason             = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<PriceQuote canonical_product_id={self.canonical_product_id} "
            f"supplier={self.supplier!r} rounded_price={self.rounded_price}>"
        )
