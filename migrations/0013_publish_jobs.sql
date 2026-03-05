-- Migration 0013: Publish Jobs (Sprint 12)
-- Tracks auto-publish runs and per-product outcomes.
-- Idempotent: uses IF NOT EXISTS throughout.

-- ── 1. publish_jobs ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS publish_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    channel         VARCHAR(32)      NOT NULL DEFAULT 'shopify',
    status          VARCHAR(16)      NOT NULL DEFAULT 'running',
    -- running / success / failed / partial
    dry_run         BOOLEAN          NOT NULL DEFAULT FALSE,
    target_count    INTEGER          NOT NULL DEFAULT 0,
    published_count INTEGER          NOT NULL DEFAULT 0,
    failed_count    INTEGER          NOT NULL DEFAULT 0,
    skipped_count   INTEGER          NOT NULL DEFAULT 0,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_publish_jobs_status
    ON publish_jobs (status);
CREATE INDEX IF NOT EXISTS idx_publish_jobs_created_at
    ON publish_jobs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_publish_jobs_channel
    ON publish_jobs (channel);

-- ── 2. publish_job_items ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS publish_job_items (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    publish_job_id       UUID         NOT NULL
        REFERENCES publish_jobs(id) ON DELETE CASCADE,
    canonical_product_id UUID         NOT NULL
        REFERENCES canonical_products(id),
    shopify_product_id   VARCHAR(128),
    status               VARCHAR(16)  NOT NULL DEFAULT 'queued',
    -- queued / published / failed / skipped
    reason               VARCHAR(512),
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (publish_job_id, canonical_product_id)
);

CREATE INDEX IF NOT EXISTS idx_publish_job_items_job_id
    ON publish_job_items (publish_job_id);
CREATE INDEX IF NOT EXISTS idx_publish_job_items_status
    ON publish_job_items (status);
CREATE INDEX IF NOT EXISTS idx_publish_job_items_canonical_product_id
    ON publish_job_items (canonical_product_id);
