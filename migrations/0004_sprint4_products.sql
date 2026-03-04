-- Migration: 0004_sprint4_products
-- Description: Create products table for crawler + Shopify sync (Sprint 4)
-- Idempotent: uses IF NOT EXISTS on table and indexes

-- ── products table ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS products (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier             VARCHAR(64)     NOT NULL DEFAULT 'stylekorean',
    supplier_product_id  VARCHAR(128)    NOT NULL,
    supplier_product_url VARCHAR(1024)   NOT NULL,
    name                 VARCHAR(512)    NOT NULL,
    brand                VARCHAR(256),
    price                NUMERIC(12, 2),
    sale_price           NUMERIC(12, 2),
    currency             VARCHAR(10)     NOT NULL DEFAULT 'USD',
    stock_status         VARCHAR(32)     NOT NULL DEFAULT 'unknown',
    image_urls_json      JSONB,
    shopify_product_id   VARCHAR(64),
    created_at           TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT products_supplier_product_id_unique UNIQUE (supplier_product_id)
);

-- ── indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_products_supplier
    ON products (supplier);

CREATE INDEX IF NOT EXISTS idx_products_supplier_product_id
    ON products (supplier_product_id);

CREATE INDEX IF NOT EXISTS idx_products_shopify_product_id
    ON products (shopify_product_id);

-- ── auto-update updated_at trigger ───────────────────────────────────────────
-- Only create the trigger function once (idempotent)
CREATE OR REPLACE FUNCTION update_products_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop trigger if it already exists, then recreate (idempotent)
DROP TRIGGER IF EXISTS trg_products_updated_at ON products;
CREATE TRIGGER trg_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW
    EXECUTE FUNCTION update_products_updated_at();
