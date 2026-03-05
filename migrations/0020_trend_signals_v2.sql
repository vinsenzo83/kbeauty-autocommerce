-- =============================================================================
-- Migration 0020 – Sprint 18: Trend Signals v2
-- =============================================================================
-- Tables: trend_sources, trend_items, mention_dictionary, mention_signals
-- All statements are idempotent (IF NOT EXISTS / IF EXISTS guards).
-- =============================================================================

-- ── A) trend_sources ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trend_sources (
    id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    source     VARCHAR(32)  NOT NULL,   -- amazon | tiktok | supplier
    name       VARCHAR(128) NOT NULL,   -- e.g. "amazon_bestsellers_us"
    is_enabled BOOLEAN      NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_trend_sources_source_name
    ON trend_sources (source, name);

-- ── B) trend_items ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trend_items (
    id           UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id    UUID          NOT NULL
                     REFERENCES trend_sources(id) ON DELETE CASCADE,
    observed_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    external_id  VARCHAR(128)  NULL,   -- amazon ASIN, tiktok video id, etc.
    title        VARCHAR(512)  NULL,
    brand        VARCHAR(256)  NULL,
    category     VARCHAR(256)  NULL,
    rank         INT           NULL,
    price        NUMERIC(12,2) NULL,
    currency     VARCHAR(8)    NULL,
    rating       NUMERIC(3,2)  NULL,
    review_count INT           NULL,
    raw_json     JSONB         NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trend_items_source_observed
    ON trend_items (source_id, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_trend_items_rank
    ON trend_items (rank);

CREATE INDEX IF NOT EXISTS idx_trend_items_external
    ON trend_items (external_id);

-- ── C) mention_dictionary ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mention_dictionary (
    id                   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_product_id UUID         NOT NULL
                             REFERENCES canonical_products(id) ON DELETE CASCADE,
    phrase               VARCHAR(256) NOT NULL,   -- normalized search phrase
    weight               FLOAT        NOT NULL DEFAULT 1.0,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_mention_dict_canonical_phrase
    ON mention_dictionary (canonical_product_id, phrase);

CREATE INDEX IF NOT EXISTS idx_mention_dict_phrase
    ON mention_dictionary (phrase);

-- ── D) mention_signals ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mention_signals (
    id                   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_product_id UUID         NOT NULL
                             REFERENCES canonical_products(id) ON DELETE CASCADE,
    source_id            UUID         NOT NULL
                             REFERENCES trend_sources(id) ON DELETE CASCADE,
    observed_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    mentions             INT          NOT NULL DEFAULT 0,
    velocity             FLOAT        NOT NULL DEFAULT 0.0,  -- growth score
    score                FLOAT        NOT NULL DEFAULT 0.0,  -- mentions * (1 + velocity)
    raw_json             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Daily unique: one row per (canonical_product_id, source_id, day)
-- Service enforces upsert logic; this index avoids exact-day duplicates
CREATE UNIQUE INDEX IF NOT EXISTS idx_mention_signals_daily_unique
    ON mention_signals (canonical_product_id, source_id, date_trunc('day', observed_at));

CREATE INDEX IF NOT EXISTS idx_mention_signals_score
    ON mention_signals (score DESC);

CREATE INDEX IF NOT EXISTS idx_mention_signals_observed
    ON mention_signals (observed_at DESC);
