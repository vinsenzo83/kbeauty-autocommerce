from __future__ import annotations

"""
app/services/canonical_service.py
───────────────────────────────────
Sprint 8 – Canonical Product service.

This is the ONLY place that decides canonical identity for a product.

Public API
----------
make_canonical_sku(name, brand=None, size_ml=None, fallback=None) -> str
    Produce a stable, URL-safe key for a product.

get_or_create_canonical_from_product(product, session) -> UUID
    Ensure a CanonicalProduct row exists for the given ORM Product and
    link products.canonical_product_id.  Returns the canonical UUID.

attach_supplier_to_canonical(canonical_id, supplier, supplier_product_id,
                             supplier_product_url, session) -> SupplierProduct
    Upsert a SupplierProduct row keyed on (canonical_product_id, supplier).
"""

import re
import uuid
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canonical_product import CanonicalProduct
from app.models.supplier_product import SupplierProduct

logger = structlog.get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SKU helpers
# ─────────────────────────────────────────────────────────────────────────────

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def make_canonical_sku(
    name: str,
    brand: str | None = None,
    size_ml: int | None = None,
    fallback: str | None = None,
) -> str:
    """
    Produce a stable, URL-safe slug to use as canonical_sku.

    Rules
    -----
    - Lower-case the input.
    - Replace any non-alphanumeric run with a single '-'.
    - Strip leading/trailing '-'.
    - Concatenate: brand-name[-size_ml] (parts that are present).
    - If the result is empty (no name/brand), use *fallback* verbatim after
      sanitisation, or raise ValueError.

    Examples
    --------
    >>> make_canonical_sku("Some Cream", brand="Laneige", size_ml=100)
    'laneige-some-cream-100'
    >>> make_canonical_sku("XYZ", fallback="sku-abc-123")
    'xyz'
    """
    parts: list[str] = []
    if brand:
        parts.append(_slug(brand))
    if name:
        parts.append(_slug(name))
    if size_ml is not None:
        parts.append(str(size_ml))

    joined = "-".join(p for p in parts if p)

    if not joined:
        if fallback:
            joined = _slug(fallback)
        if not joined:
            raise ValueError(
                "Cannot build canonical_sku: name/brand/fallback are all empty."
            )
    return joined


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")


# ─────────────────────────────────────────────────────────────────────────────
# get_or_create
# ─────────────────────────────────────────────────────────────────────────────

async def get_or_create_canonical_from_product(
    product: Any,
    session: AsyncSession,
) -> UUID:
    """
    Ensure a CanonicalProduct exists for the ORM Product and return its id.

    Steps
    -----
    1. If product.canonical_product_id is already set, return it.
    2. Compute canonical_sku from product fields.
    3. SELECT canonical_products WHERE canonical_sku = ?
       - if found: link and return.
    4. INSERT new CanonicalProduct.
    5. Flush (not commit) so the ID is available.
    6. Write products.canonical_product_id = new_id (if product is an ORM obj).
    7. Return the canonical UUID.
    """
    # Already linked
    existing_cid = getattr(product, "canonical_product_id", None)
    if existing_cid is not None:
        return existing_cid  # type: ignore[return-value]

    # Build sku
    name     = getattr(product, "name", "") or ""
    brand    = getattr(product, "brand", None)
    size_ml  = getattr(product, "size_ml", None)
    fallback = getattr(product, "supplier_product_id", None)

    sku = make_canonical_sku(name=name, brand=brand, size_ml=size_ml, fallback=fallback)

    # Look up existing
    stmt   = select(CanonicalProduct).where(CanonicalProduct.canonical_sku == sku)
    result = await session.execute(stmt)
    cp     = result.scalar_one_or_none()

    if cp is None:
        image_json = None
        raw_imgs   = getattr(product, "image_urls_json", None)
        if raw_imgs is not None:
            import json
            try:
                image_json = json.dumps(raw_imgs) if not isinstance(raw_imgs, str) else raw_imgs
            except Exception:
                image_json = None

        cp = CanonicalProduct(
            canonical_sku   = sku,
            name            = name,
            brand           = brand,
            image_urls_json = image_json,
        )
        session.add(cp)
        await session.flush()  # populate cp.id without committing
        logger.info(
            "canonical_service.created",
            canonical_sku=sku,
            canonical_id=str(cp.id),
        )
    else:
        logger.debug(
            "canonical_service.found_existing",
            canonical_sku=sku,
            canonical_id=str(cp.id),
        )

    # Link back to product row if it's an ORM object
    if hasattr(product, "canonical_product_id"):
        product.canonical_product_id = cp.id

    return cp.id  # type: ignore[return-value]


# ─────────────────────────────────────────────────────────────────────────────
# attach_supplier_to_canonical
# ─────────────────────────────────────────────────────────────────────────────

async def attach_supplier_to_canonical(
    canonical_id: UUID,
    supplier: str,
    supplier_product_id: str,
    supplier_product_url: str,
    session: AsyncSession,
) -> SupplierProduct:
    """
    Upsert a SupplierProduct row for (canonical_product_id, supplier).

    - If a row already exists for this (canonical_product_id, supplier), update it.
    - Otherwise create a new row.
    - Does NOT set price / stock_status here (done by crawler tasks).

    Returns
    -------
    The persisted SupplierProduct (not yet committed).
    """
    stmt = select(SupplierProduct).where(
        SupplierProduct.canonical_product_id == canonical_id,
        SupplierProduct.supplier             == supplier,
    )
    result = await session.execute(stmt)
    sp     = result.scalar_one_or_none()

    if sp is None:
        sp = SupplierProduct(
            canonical_product_id = canonical_id,
            supplier             = supplier,
            supplier_product_id  = supplier_product_id,
            supplier_product_url = supplier_product_url,
        )
        session.add(sp)
        logger.info(
            "canonical_service.supplier_attached",
            canonical_id=str(canonical_id),
            supplier=supplier,
            supplier_product_id=supplier_product_id,
        )
    else:
        sp.supplier_product_id  = supplier_product_id
        sp.supplier_product_url = supplier_product_url
        logger.debug(
            "canonical_service.supplier_updated",
            canonical_id=str(canonical_id),
            supplier=supplier,
        )

    await session.flush()
    return sp
