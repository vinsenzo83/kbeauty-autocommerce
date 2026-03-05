-- =============================================================================
-- Migration 0019 – Sprint 17: AI Discovery Engine v2 – product_candidates_v2
-- =============================================================================
-- New scoring schema for Sprint 17.
-- Uses a separate table (product_candidates_v2) so Sprint 15 data is preserved.
-- Idempotent: all statements use IF NOT EXISTS / IF EXISTS guards.
-- =============================================================================

CREATE TABLE IF NOT EXISTS product_candidates_v2 (
    id                   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),

    -- FK → canonical_products.id (CASCADE on delete, soft reference for portability)
    canonical_product_id UUID         NOT NULL,

    -- ── Sprint 17 score components (0.0 – 1.0 each) ──────────────────────────
    score                FLOAT        NOT NULL DEFAULT 0,  -- final weighted score
    amazon_rank_score    FLOAT        NOT NULL DEFAULT 0,  -- 0.35 weight
    supplier_rank_score  FLOAT        NOT NULL DEFAULT 0,  -- 0.25 weight
    margin_score         FLOAT        NOT NULL DEFAULT 0,  -- 0.20 weight
    review_score         FLOAT        NOT NULL DEFAULT 0,  -- 0.10 weight
    competition_score    FLOAT        NOT NULL DEFAULT 0,  -- 0.10 weight

    -- ── Lifecycle ─────────────────────────────────────────────────────────────
    status               VARCHAR(32)  NOT NULL DEFAULT 'candidate',
    -- candidate  → pending publish review
    -- published  → successfully published to Shopify
    -- rejected   → manually dismissed or score below threshold

    notes                TEXT,

    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_candidates_v2_score_desc
    ON product_candidates_v2 (score DESC);

CREATE INDEX IF NOT EXISTS idx_candidates_v2_status
    ON product_candidates_v2 (status);

CREATE INDEX IF NOT EXISTS idx_candidates_v2_canonical_id
    ON product_candidates_v2 (canonical_product_id);

CREATE INDEX IF NOT EXISTS idx_candidates_v2_created_at
    ON product_candidates_v2 (created_at DESC);

-- Unique constraint: one active candidate row per canonical product
-- (prevents duplicate publish items; service enforces upsert logic)
CREATE UNIQUE INDEX IF NOT EXISTS idx_candidates_v2_canonical_unique_active
    ON product_candidates_v2 (canonical_product_id)
    WHERE status = 'candidate';
