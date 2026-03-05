-- Migration 0014: Market Price Intelligence + Auto Repricing (Sprint 13)
-- Idempotent: all CREATE TABLE / CREATE INDEX use IF NOT EXISTS.

-- ── 1. market_sources ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_sources (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name       VARCHAR(64) NOT NULL UNIQUE,   -- 'amazon', 'shopee', 'competitor_manual'
    type       VARCHAR(16) NOT NULL DEFAULT 'manual', -- api / manual / import
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO market_sources (name, type) VALUES
    ('competitor_manual', 'manual'),
    ('amazon',            'api'),
    ('shopee',            'api'),
    ('tiktok_shop',       'api')
ON CONFLICT (name) DO NOTHING;

-- ── 2. market_prices ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_prices (
    id                   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_product_id UUID         NOT NULL
        REFERENCES canonical_products(id) ON DELETE CASCADE,
    source_id            UUID         NOT NULL
        REFERENCES market_sources(id),
    external_url         VARCHAR(512),
    external_sku         VARCHAR(128),
    currency             VARCHAR(8)   NOT NULL DEFAULT 'USD',
    price                NUMERIC(18,6) NOT NULL,
    in_stock             BOOLEAN      NOT NULL DEFAULT TRUE,
    last_seen_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (canonical_product_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_market_prices_canonical
    ON market_prices (canonical_product_id);
CREATE INDEX IF NOT EXISTS idx_market_prices_source
    ON market_prices (source_id);
CREATE INDEX IF NOT EXISTS idx_market_prices_last_seen
    ON market_prices (last_seen_at DESC);

-- ── 3. repricing_runs ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS repricing_runs (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    channel       VARCHAR(32) NOT NULL DEFAULT 'shopify',
    status        VARCHAR(16) NOT NULL DEFAULT 'running',
    -- running / success / failed / partial
    dry_run       BOOLEAN     NOT NULL DEFAULT FALSE,
    target_count  INTEGER     NOT NULL DEFAULT 0,
    updated_count INTEGER     NOT NULL DEFAULT 0,
    skipped_count INTEGER     NOT NULL DEFAULT 0,
    failed_count  INTEGER     NOT NULL DEFAULT 0,
    notes         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_repricing_runs_status
    ON repricing_runs (status);
CREATE INDEX IF NOT EXISTS idx_repricing_runs_created_at
    ON repricing_runs (created_at DESC);

-- ── 4. repricing_run_items ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS repricing_run_items (
    id                   UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    repricing_run_id     UUID          NOT NULL
        REFERENCES repricing_runs(id) ON DELETE CASCADE,
    canonical_product_id UUID          NOT NULL,
    old_price            NUMERIC(12,2),
    recommended_price    NUMERIC(12,2),
    applied_price        NUMERIC(12,2),
    status               VARCHAR(16)   NOT NULL DEFAULT 'skipped',
    -- updated / skipped / failed
    reason               VARCHAR(256),
    created_at           TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (repricing_run_id, canonical_product_id)
);

CREATE INDEX IF NOT EXISTS idx_repricing_run_items_run
    ON repricing_run_items (repricing_run_id);
CREATE INDEX IF NOT EXISTS idx_repricing_run_items_canonical
    ON repricing_run_items (canonical_product_id);
