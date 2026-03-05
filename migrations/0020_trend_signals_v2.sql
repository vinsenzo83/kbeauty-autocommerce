-- =============================================================================
-- Migration 0020 – Sprint 18: Trend Signals v2
-- =============================================================================
-- Tables: trend_sources, trend_items, mention_dictionary, mention_signals
-- All statements are idempotent (IF NOT EXISTS / IF EXISTS guards).
-- Note: FK constraints use soft references (no REFERENCES clause) for
--       portability across test environments and migration order independence.
-- Fix: observed_date DATE column used for daily-unique index instead of
--      date_trunc/cast expression (avoids IMMUTABLE requirement in PostgreSQL).
-- =============================================================================

-- ── A) trend_sources ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trend_sources (
    id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    source     VARCHAR(32)  NOT NULL,
    name       VARCHAR(128) NOT NULL,
    is_enabled BOOLEAN      NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_trend_sources_source_name
    ON trend_sources (source, name);

-- ── B) trend_items ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trend_items (
    id           UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id    UUID          NOT NULL,
    observed_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    external_id  VARCHAR(128)  NULL,
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
    canonical_product_id UUID         NOT NULL,
    phrase               VARCHAR(256) NOT NULL,
    weight               FLOAT        NOT NULL DEFAULT 1.0,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_mention_dict_canonical_phrase
    ON mention_dictionary (canonical_product_id, phrase);

CREATE INDEX IF NOT EXISTS idx_mention_dict_phrase
    ON mention_dictionary (phrase);

-- ── D) mention_signals ───────────────────────────────────────────────────────
-- observed_date DATE column stores the truncated date for daily-unique index.
-- This avoids the IMMUTABLE requirement that date_trunc/::date cast expressions
-- impose on functional indexes in PostgreSQL.
CREATE TABLE IF NOT EXISTS mention_signals (
    id                   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_product_id UUID         NOT NULL,
    source_id            UUID         NOT NULL,
    observed_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    observed_date        DATE         NOT NULL DEFAULT CURRENT_DATE,
    mentions             INT          NOT NULL DEFAULT 0,
    velocity             FLOAT        NOT NULL DEFAULT 0.0,
    score                FLOAT        NOT NULL DEFAULT 0.0,
    raw_json             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Daily unique: one signal row per (canonical_product_id, source_id, date)
-- Uses plain DATE column for immutability compliance.
CREATE UNIQUE INDEX IF NOT EXISTS idx_mention_signals_daily_unique
    ON mention_signals (canonical_product_id, source_id, observed_date);

CREATE INDEX IF NOT EXISTS idx_mention_signals_score
    ON mention_signals (score DESC);

CREATE INDEX IF NOT EXISTS idx_mention_signals_observed
    ON mention_signals (observed_at DESC);
