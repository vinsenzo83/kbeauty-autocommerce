-- ============================================================
-- Migration 0009: Pricing Engine
-- Sprint 8 – price_quotes table for the pricing engine.
-- Idempotent (IF NOT EXISTS).
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- price_quotes
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS price_quotes (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_product_id UUID       NOT NULL REFERENCES canonical_products(id) ON DELETE CASCADE,
    supplier            TEXT        NOT NULL,
    supplier_price      NUMERIC(12,2) NOT NULL,
    shipping_cost       NUMERIC(12,2) NOT NULL DEFAULT 3.00,
    fee_rate            NUMERIC(6,4)  NOT NULL DEFAULT 0.03,
    target_margin_rate  NUMERIC(6,4)  NOT NULL DEFAULT 0.30,
    min_margin_abs      NUMERIC(10,2) NOT NULL DEFAULT 3.00,
    computed_price      NUMERIC(12,2) NOT NULL,
    rounded_price       NUMERIC(12,2) NOT NULL,
    reason              TEXT,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_price_quotes_canonical_product_id
    ON price_quotes (canonical_product_id);
CREATE INDEX IF NOT EXISTS idx_price_quotes_created_at
    ON price_quotes (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_price_quotes_supplier
    ON price_quotes (supplier);
