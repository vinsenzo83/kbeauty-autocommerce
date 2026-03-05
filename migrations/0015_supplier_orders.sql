-- Migration 0015: Supplier Orders — Auto-Fulfillment Pipeline (Sprint 14)
-- Idempotent: all CREATE TABLE / CREATE INDEX use IF NOT EXISTS.

-- ── supplier_orders ──────────────────────────────────────────────────────────
-- One row per (channel_order, supplier) fulfillment attempt.
-- Tracks placement → shipping → delivery lifecycle.

CREATE TABLE IF NOT EXISTS supplier_orders (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),

    -- FK to channel_orders_v2 (the original inbound Shopify / Shopee / TikTok order)
    channel_order_id    UUID         NOT NULL,

    -- Supplier identity
    supplier            VARCHAR(64)  NOT NULL,      -- 'STYLEKOREAN' | 'JOLSE' | 'OLIVEYOUNG'

    -- Supplier-side references (populated after placement)
    supplier_order_id   VARCHAR(128),               -- order reference on supplier site
    supplier_status     VARCHAR(32),                -- raw status string from supplier API

    -- Tracking (populated once shipped)
    tracking_number     VARCHAR(128),
    tracking_carrier    VARCHAR(64),

    -- Cost
    cost                NUMERIC(18,6),
    currency            VARCHAR(8)   NOT NULL DEFAULT 'USD',

    -- Internal lifecycle status
    -- ENUM: pending | placed | confirmed | shipped | delivered | failed
    status              VARCHAR(32)  NOT NULL DEFAULT 'pending',

    -- Error handling
    failure_reason      VARCHAR(256),               -- NO_SUPPLIER_AVAILABLE | SUPPLIER_API_ERROR | etc.
    retry_count         SMALLINT     NOT NULL DEFAULT 0,

    -- Audit timestamps
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- One fulfillment attempt per (order, supplier) pair
    UNIQUE (channel_order_id, supplier)
);

CREATE INDEX IF NOT EXISTS idx_supplier_orders_channel_order
    ON supplier_orders (channel_order_id);

CREATE INDEX IF NOT EXISTS idx_supplier_orders_status
    ON supplier_orders (status);

CREATE INDEX IF NOT EXISTS idx_supplier_orders_supplier
    ON supplier_orders (supplier);

CREATE INDEX IF NOT EXISTS idx_supplier_orders_created_at
    ON supplier_orders (created_at DESC);
