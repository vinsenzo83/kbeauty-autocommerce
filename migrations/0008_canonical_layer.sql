-- ============================================================
-- Migration 0008: Canonical Layer
-- Sprint 8 – Introduce canonical_products as the primary identity
--             for real-world products, reform supplier_products to
--             reference canonical_products, and add shopify_mappings.
-- Idempotent (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- 1. canonical_products
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS canonical_products (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_sku   TEXT        UNIQUE NOT NULL,
    name            TEXT        NOT NULL,
    brand           TEXT,
    size_ml         INTEGER,
    ean             TEXT,
    image_urls_json TEXT,

    -- Pricing engine fields
    pricing_enabled         BOOLEAN     NOT NULL DEFAULT TRUE,
    target_margin_rate      NUMERIC(6,4) NOT NULL DEFAULT 0.30,
    min_margin_abs          NUMERIC(10,2) NOT NULL DEFAULT 3.00,
    shipping_cost_default   NUMERIC(10,2) NOT NULL DEFAULT 3.00,
    last_price              NUMERIC(12,2),
    last_price_at           TIMESTAMPTZ,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_canonical_products_canonical_sku
    ON canonical_products (canonical_sku);
CREATE INDEX IF NOT EXISTS idx_canonical_products_brand
    ON canonical_products (brand);

-- Auto-update updated_at
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'update_canonical_products_updated_at'
    ) THEN
        CREATE TRIGGER update_canonical_products_updated_at
            BEFORE UPDATE ON canonical_products
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;

-- ────────────────────────────────────────────────────────────
-- 2. shopify_mappings
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS shopify_mappings (
    id                      UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_product_id    UUID    NOT NULL REFERENCES canonical_products(id) ON DELETE CASCADE,
    shopify_product_id      TEXT    NOT NULL,
    shopify_variant_id      TEXT    NOT NULL,
    shopify_inventory_item_id TEXT,
    currency                TEXT    NOT NULL DEFAULT 'USD',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_shopify_mappings_variant      UNIQUE (shopify_variant_id),
    CONSTRAINT uq_shopify_mappings_canonical    UNIQUE (canonical_product_id)
);

CREATE INDEX IF NOT EXISTS idx_shopify_mappings_canonical_product_id
    ON shopify_mappings (canonical_product_id);
CREATE INDEX IF NOT EXISTS idx_shopify_mappings_shopify_product_id
    ON shopify_mappings (shopify_product_id);

-- ────────────────────────────────────────────────────────────
-- 3. products table – add canonical_product_id (nullable, legacy compat)
-- ────────────────────────────────────────────────────────────
ALTER TABLE products
    ADD COLUMN IF NOT EXISTS canonical_product_id UUID
        REFERENCES canonical_products(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_products_canonical_product_id
    ON products (canonical_product_id);

-- ────────────────────────────────────────────────────────────
-- 4. supplier_products – add canonical_product_id + supplier_product_url
--    Keep product_id for backward compat; new code uses canonical_product_id.
-- ────────────────────────────────────────────────────────────
ALTER TABLE supplier_products
    ADD COLUMN IF NOT EXISTS canonical_product_id UUID
        REFERENCES canonical_products(id) ON DELETE CASCADE;

ALTER TABLE supplier_products
    ADD COLUMN IF NOT EXISTS supplier_product_url TEXT;

CREATE INDEX IF NOT EXISTS idx_supplier_products_canonical_product_id
    ON supplier_products (canonical_product_id);

-- Unique constraint: one row per (canonical_product_id, supplier)
-- Note: this is a partial unique index – only enforced when canonical_product_id IS NOT NULL
CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_products_canonical_supplier
    ON supplier_products (canonical_product_id, supplier)
    WHERE canonical_product_id IS NOT NULL;

-- Unique constraint: (supplier, supplier_product_id) – supplier-scoped uniqueness
CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_products_supplier_sku
    ON supplier_products (supplier, supplier_product_id);

-- ────────────────────────────────────────────────────────────
-- 5. Backfill: create canonical_products from existing products rows
--    and link them via products.canonical_product_id
-- ────────────────────────────────────────────────────────────
-- Create one canonical_product per distinct product row (if not already linked)
INSERT INTO canonical_products (
    canonical_sku,
    name,
    brand,
    image_urls_json
)
SELECT DISTINCT
    -- stable canonical_sku: lower(brand-name-supplier_product_id) with slug sanitisation
    lower(
        regexp_replace(
            COALESCE(brand, '') || '-' || name || '-' || supplier_product_id,
            '[^a-z0-9\-]', '-', 'gi'
        )
    ) AS canonical_sku,
    name,
    brand,
    CAST(image_urls_json AS TEXT)
FROM products
WHERE canonical_product_id IS NULL
ON CONFLICT (canonical_sku) DO NOTHING;

-- Link products rows to their canonical_product
UPDATE products p
SET canonical_product_id = cp.id
FROM canonical_products cp
WHERE p.canonical_product_id IS NULL
  AND cp.canonical_sku = lower(
        regexp_replace(
            COALESCE(p.brand, '') || '-' || p.name || '-' || p.supplier_product_id,
            '[^a-z0-9\-]', '-', 'gi'
        )
  );

-- ────────────────────────────────────────────────────────────
-- 6. Backfill shopify_mappings from products (where shopify ids exist)
-- ────────────────────────────────────────────────────────────
INSERT INTO shopify_mappings (
    canonical_product_id,
    shopify_product_id,
    shopify_variant_id,
    shopify_inventory_item_id
)
SELECT
    p.canonical_product_id,
    p.shopify_product_id,
    p.shopify_variant_id,
    p.shopify_inventory_item_id
FROM products p
WHERE p.canonical_product_id IS NOT NULL
  AND p.shopify_product_id    IS NOT NULL
  AND p.shopify_variant_id    IS NOT NULL
ON CONFLICT (shopify_variant_id)      DO NOTHING;

-- ────────────────────────────────────────────────────────────
-- 7. Backfill supplier_products.canonical_product_id from products
-- ────────────────────────────────────────────────────────────
UPDATE supplier_products sp
SET canonical_product_id = p.canonical_product_id
FROM products p
WHERE sp.product_id         = p.id
  AND p.canonical_product_id IS NOT NULL
  AND sp.canonical_product_id IS NULL;
