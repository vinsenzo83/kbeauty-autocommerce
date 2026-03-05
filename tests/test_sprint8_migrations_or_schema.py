from __future__ import annotations

"""
tests/test_sprint8_migrations_or_schema.py
───────────────────────────────────────────
Sprint 8 – schema inspection tests (mock-only, no real PostgreSQL).

Uses SQLAlchemy's inspect() against an in-memory SQLite DB to verify:
  1. canonical_products table and required columns exist in ORM metadata.
  2. shopify_mappings table and required columns exist in ORM metadata.
  3. price_quotes table and required columns exist in ORM metadata.
  4. supplier_products has canonical_product_id and supplier_product_url columns.
  5. products table has canonical_product_id column.

Note: These tests DO NOT run actual SQL migrations (0008/0009).
They verify ORM model definitions only, which is the safe approach
for CI without a live database.
"""

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase

# ── Import models ─────────────────────────────────────────────────────────────

import app.models.canonical_product as _cp_mod
import app.models.shopify_mapping as _sm_mod
import app.models.price_quote as _pq_mod
import app.models.supplier_product as _sp_mod
import app.models.product as _prod_mod

from app.models.canonical_product import CanonicalProduct
from app.models.shopify_mapping import ShopifyMapping
from app.models.price_quote import PriceQuote
from app.models.supplier_product import SupplierProduct
from app.models.product import Product


# ── Synchronous SQLite engine for inspect() ──────────────────────────────────

@pytest.fixture(scope="module")
def sync_engine():
    engine = create_engine("sqlite:///:memory:", echo=False)
    # Create all tables
    _cp_mod.Base.metadata.create_all(engine)
    _sm_mod.Base.metadata.create_all(engine)
    _pq_mod.Base.metadata.create_all(engine)
    _sp_mod.Base.metadata.create_all(engine)
    _prod_mod.Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _get_columns(engine, table_name: str) -> set[str]:
    inspector = inspect(engine)
    cols = inspector.get_columns(table_name)
    return {c["name"] for c in cols}


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_canonical_products_table_exists(sync_engine):
    inspector = inspect(sync_engine)
    assert "canonical_products" in inspector.get_table_names()


def test_canonical_products_required_columns(sync_engine):
    cols = _get_columns(sync_engine, "canonical_products")
    required = {
        "id", "canonical_sku", "name", "brand",
        "pricing_enabled", "target_margin_rate", "min_margin_abs",
        "shipping_cost_default", "last_price", "last_price_at",
        "created_at", "updated_at",
    }
    missing = required - cols
    assert not missing, f"canonical_products missing columns: {missing}"


def test_shopify_mappings_table_exists(sync_engine):
    inspector = inspect(sync_engine)
    assert "shopify_mappings" in inspector.get_table_names()


def test_shopify_mappings_required_columns(sync_engine):
    cols = _get_columns(sync_engine, "shopify_mappings")
    required = {
        "id", "canonical_product_id",
        "shopify_product_id", "shopify_variant_id",
        "shopify_inventory_item_id", "currency",
    }
    missing = required - cols
    assert not missing, f"shopify_mappings missing columns: {missing}"


def test_price_quotes_table_exists(sync_engine):
    inspector = inspect(sync_engine)
    assert "price_quotes" in inspector.get_table_names()


def test_price_quotes_required_columns(sync_engine):
    cols = _get_columns(sync_engine, "price_quotes")
    required = {
        "id", "canonical_product_id", "supplier",
        "supplier_price", "shipping_cost", "fee_rate",
        "target_margin_rate", "min_margin_abs",
        "computed_price", "rounded_price", "reason", "created_at",
    }
    missing = required - cols
    assert not missing, f"price_quotes missing columns: {missing}"


def test_supplier_products_has_canonical_column(sync_engine):
    cols = _get_columns(sync_engine, "supplier_products")
    assert "canonical_product_id" in cols, "supplier_products missing canonical_product_id"


def test_supplier_products_has_url_column(sync_engine):
    cols = _get_columns(sync_engine, "supplier_products")
    assert "supplier_product_url" in cols, "supplier_products missing supplier_product_url"


def test_products_has_canonical_column(sync_engine):
    cols = _get_columns(sync_engine, "products")
    assert "canonical_product_id" in cols, "products missing canonical_product_id"


def test_canonical_products_canonical_sku_unique(sync_engine):
    """canonical_sku should have a unique constraint."""
    inspector = inspect(sync_engine)
    unique_constraints = inspector.get_unique_constraints("canonical_products")
    unique_cols = {
        col
        for uc in unique_constraints
        for col in uc.get("column_names", [])
    }
    # Also check indexes for SQLite (which uses unique indexes)
    indexes = inspector.get_indexes("canonical_products")
    for idx in indexes:
        if idx.get("unique"):
            unique_cols.update(idx.get("column_names", []))
    assert "canonical_sku" in unique_cols, "canonical_sku should be unique"


def test_shopify_mappings_variant_id_unique(sync_engine):
    """shopify_variant_id should be unique."""
    inspector = inspect(sync_engine)
    unique_constraints = inspector.get_unique_constraints("shopify_mappings")
    unique_cols = {
        col
        for uc in unique_constraints
        for col in uc.get("column_names", [])
    }
    indexes = inspector.get_indexes("shopify_mappings")
    for idx in indexes:
        if idx.get("unique"):
            unique_cols.update(idx.get("column_names", []))
    assert "shopify_variant_id" in unique_cols, "shopify_variant_id should be unique"
