-- migrations/0007_supplier_products.sql
-- Sprint 7: Multi Supplier Engine
-- Idempotent: uses IF NOT EXISTS throughout.

-- ── supplier_products table ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supplier_products (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id          UUID        NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    supplier            TEXT        NOT NULL
                            CHECK (supplier IN ('STYLEKOREAN', 'JOLSE', 'OLIVEYOUNG')),
    supplier_product_id TEXT        NOT NULL,
    price               NUMERIC(12, 2) NULL,
    stock_status        TEXT        NOT NULL DEFAULT 'IN_STOCK'
                            CHECK (stock_status IN ('IN_STOCK', 'OUT_OF_STOCK')),
    last_checked_at     TIMESTAMPTZ NULL,

    -- Ensure each supplier only has one record per product
    UNIQUE (product_id, supplier)
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_supplier_products_product_id
    ON supplier_products (product_id);

CREATE INDEX IF NOT EXISTS idx_supplier_products_supplier
    ON supplier_products (supplier);

CREATE INDEX IF NOT EXISTS idx_supplier_products_stock_status
    ON supplier_products (stock_status);
