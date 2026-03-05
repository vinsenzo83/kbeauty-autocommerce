-- Migration: 0006_inventory_sync
-- Description: Add inventory-sync fields to products table (Sprint 6)
-- Idempotent: uses IF NOT EXISTS / ADD COLUMN IF NOT EXISTS

-- ── products: new inventory-tracking columns ─────────────────────────────────

-- stock_status already exists as VARCHAR(32) from 0004_sprint4_products.sql
-- Extend accepted values: IN_STOCK, OUT_OF_STOCK (uppercase enum-style),
-- plus legacy lowercase 'in_stock', 'out_of_stock', 'unknown'.
-- No ALTER needed for VARCHAR; just document the new convention.

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS last_seen_price  NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS last_checked_at  TIMESTAMPTZ;

-- ── shopify_inventory_item_id: needed for Inventory API calls ────────────────
ALTER TABLE products
    ADD COLUMN IF NOT EXISTS shopify_variant_id        VARCHAR(64),
    ADD COLUMN IF NOT EXISTS shopify_inventory_item_id VARCHAR(64);

-- ── index for stale-check query ───────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_products_last_checked_at
    ON products (last_checked_at);

CREATE INDEX IF NOT EXISTS idx_products_stock_status
    ON products (stock_status);
