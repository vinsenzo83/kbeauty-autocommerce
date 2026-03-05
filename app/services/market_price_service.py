from __future__ import annotations

"""
app/services/market_price_service.py
──────────────────────────────────────
Sprint 13 – Market price intelligence service.

Provides
--------
upsert_market_source(session, name, type) -> MarketSource
upsert_market_price(session, canonical_product_id, source_name, price, ...) -> MarketPrice
get_market_prices(session, canonical_product_id) -> list[dict]
get_competitor_band(session, canonical_product_id) -> CompetitorBand | None

Design notes
------------
- No web scraping. Prices come from:
    a) Manual admin entry  (POST /admin/market-prices)
    b) CSV bulk import     (POST /admin/market-prices/import)
    c) Future: official API adapters that write via upsert_market_price()
- Only in_stock=True prices are used when computing the competitor band.
"""

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_price import MarketPrice, MarketSource

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Competitor band dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CompetitorBand:
    min_price:    Decimal
    median_price: Decimal
    max_price:    Decimal
    sample_count: int


# ─────────────────────────────────────────────────────────────────────────────
# Source helpers
# ─────────────────────────────────────────────────────────────────────────────

async def upsert_market_source(
    session: AsyncSession,
    name: str,
    source_type: str = "manual",
) -> MarketSource:
    """
    Get-or-create a MarketSource by name (case-insensitive slug).
    """
    name = name.strip().lower()
    result = await session.execute(
        select(MarketSource).where(MarketSource.name == name)
    )
    src = result.scalar_one_or_none()
    if src is None:
        src = MarketSource(name=name, type=source_type)
        session.add(src)
        await session.flush()
        logger.info("market_price_service.source_created", name=name, type=source_type)
    return src


# ─────────────────────────────────────────────────────────────────────────────
# Price upsert
# ─────────────────────────────────────────────────────────────────────────────

async def upsert_market_price(
    session: AsyncSession,
    *,
    canonical_product_id: uuid.UUID,
    source_name: str,
    price: float | Decimal,
    currency: str = "USD",
    in_stock: bool = True,
    external_url: str | None = None,
    external_sku: str | None = None,
) -> MarketPrice:
    """
    Insert or update a competitor price for (canonical_product_id, source).

    If a row already exists for the (canonical_product_id, source_id) pair,
    it is updated in-place (price, currency, in_stock, external_url, external_sku,
    last_seen_at, updated_at).
    """
    src = await upsert_market_source(session, source_name)
    price_dec = Decimal(str(price))
    now = datetime.now(timezone.utc)

    result = await session.execute(
        select(MarketPrice).where(
            MarketPrice.canonical_product_id == canonical_product_id,
            MarketPrice.source_id == src.id,
        )
    )
    mp = result.scalar_one_or_none()

    if mp is None:
        mp = MarketPrice(
            canonical_product_id = canonical_product_id,
            source_id            = src.id,
            price                = price_dec,
            currency             = currency,
            in_stock             = in_stock,
            external_url         = external_url,
            external_sku         = external_sku,
            last_seen_at         = now,
        )
        session.add(mp)
        logger.info(
            "market_price_service.price_created",
            canonical_product_id=str(canonical_product_id),
            source=source_name,
            price=float(price_dec),
        )
    else:
        mp.price        = price_dec
        mp.currency     = currency
        mp.in_stock     = in_stock
        mp.external_url = external_url or mp.external_url
        mp.external_sku = external_sku or mp.external_sku
        mp.last_seen_at = now
        mp.updated_at   = now
        logger.info(
            "market_price_service.price_updated",
            canonical_product_id=str(canonical_product_id),
            source=source_name,
            price=float(price_dec),
        )

    await session.flush()
    return mp


# ─────────────────────────────────────────────────────────────────────────────
# Get prices
# ─────────────────────────────────────────────────────────────────────────────

async def get_market_prices(
    session: AsyncSession,
    canonical_product_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """
    Return all competitor prices for a canonical product as plain dicts.
    """
    result = await session.execute(
        select(MarketPrice, MarketSource)
        .join(MarketSource, MarketSource.id == MarketPrice.source_id)
        .where(MarketPrice.canonical_product_id == canonical_product_id)
        .order_by(MarketPrice.price.asc())
    )
    rows = result.all()
    return [
        {
            "id":                   str(mp.id),
            "source":               src.name,
            "source_type":          src.type,
            "price":                float(mp.price),
            "currency":             mp.currency,
            "in_stock":             mp.in_stock,
            "external_url":         mp.external_url,
            "external_sku":         mp.external_sku,
            "last_seen_at":         mp.last_seen_at.isoformat() if mp.last_seen_at else None,
        }
        for mp, src in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Competitor band computation
# ─────────────────────────────────────────────────────────────────────────────

async def get_competitor_band(
    session: AsyncSession,
    canonical_product_id: uuid.UUID,
) -> CompetitorBand | None:
    """
    Compute min / median / max from in-stock competitor prices.

    Returns None when no in-stock prices exist.
    """
    result = await session.execute(
        select(MarketPrice.price)
        .where(
            MarketPrice.canonical_product_id == canonical_product_id,
            MarketPrice.in_stock.is_(True),
        )
        .order_by(MarketPrice.price.asc())
    )
    prices = [Decimal(str(row[0])) for row in result.all()]

    if not prices:
        return None

    sorted_prices = sorted(prices)
    n = len(sorted_prices)
    if n % 2 == 1:
        median = sorted_prices[n // 2]
    else:
        median = (sorted_prices[n // 2 - 1] + sorted_prices[n // 2]) / Decimal("2")

    return CompetitorBand(
        min_price    = sorted_prices[0],
        median_price = median,
        max_price    = sorted_prices[-1],
        sample_count = n,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CSV bulk import parser
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_CSV_COLS = {"canonical_sku", "source", "price", "currency"}
OPTIONAL_CSV_COLS = {"in_stock", "external_url", "external_sku"}


def parse_market_price_csv(
    csv_content: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Parse a CSV string into market price records.

    Expected columns (header row required):
        canonical_sku, source, price, currency[, in_stock, external_url, external_sku]

    Returns
    -------
    (records, errors)
        records : list of validated dicts
        errors  : list of human-readable error strings
    """
    records: list[dict[str, Any]] = []
    errors:  list[str] = []

    reader = csv.DictReader(io.StringIO(csv_content.strip()))
    if not reader.fieldnames:
        return [], ["CSV has no header row"]

    header_set = {f.strip().lower() for f in reader.fieldnames}
    missing = REQUIRED_CSV_COLS - header_set
    if missing:
        return [], [f"CSV missing required columns: {missing}"]

    for line_num, row in enumerate(reader, start=2):
        # Normalise keys
        row = {k.strip().lower(): v.strip() for k, v in row.items()}
        try:
            price_val = Decimal(row["price"])
            if price_val <= 0:
                errors.append(f"Line {line_num}: price must be > 0")
                continue

            in_stock_raw = row.get("in_stock", "true").lower()
            in_stock = in_stock_raw not in ("false", "0", "no", "")

            records.append({
                "canonical_sku": row["canonical_sku"],
                "source":        row["source"].lower(),
                "price":         price_val,
                "currency":      row["currency"].upper(),
                "in_stock":      in_stock,
                "external_url":  row.get("external_url") or None,
                "external_sku":  row.get("external_sku") or None,
            })
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Line {line_num}: {exc}")

    return records, errors
