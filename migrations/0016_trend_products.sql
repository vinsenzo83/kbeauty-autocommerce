-- =============================================================================
-- Migration 0016 – Sprint 15: AI Product Discovery – trend_products
-- =============================================================================
-- Stores raw trend signals collected from external sources (TikTok, Amazon, etc.)
-- Each row represents one trending product as observed from a given source.
-- =============================================================================

CREATE TABLE IF NOT EXISTS trend_products (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source          VARCHAR(64) NOT NULL,               -- 'tiktok', 'amazon_bestsellers', ...
    external_id     VARCHAR(256) NOT NULL,               -- source-specific product/video/ASIN id
    name            TEXT        NOT NULL,
    brand           VARCHAR(128),
    category        VARCHAR(128),
    trend_score     NUMERIC(8,4) NOT NULL DEFAULT 0,    -- 0.0 – 10.0 normalised score
    raw_data_json   TEXT,                               -- full source payload (JSON string)
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Prevent duplicate signals for the same product from the same source
CREATE UNIQUE INDEX IF NOT EXISTS uq_trend_products_source_external
    ON trend_products (source, external_id);

-- Fast look-ups by source
CREATE INDEX IF NOT EXISTS idx_trend_products_source
    ON trend_products (source);

-- Retrieve top-scored trends quickly
CREATE INDEX IF NOT EXISTS idx_trend_products_trend_score_desc
    ON trend_products (trend_score DESC);

-- Chronological access
CREATE INDEX IF NOT EXISTS idx_trend_products_collected_at_desc
    ON trend_products (collected_at DESC);
