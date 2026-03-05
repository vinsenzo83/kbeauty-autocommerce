-- ============================================================
-- Migration 0012: channel_orders (Canonical order table for multi-channel)
-- Sprint 10 – Unified order storage from all channels.
-- Idempotent (IF NOT EXISTS).
-- ============================================================

CREATE TABLE IF NOT EXISTS channel_orders_v2 (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    external_order_id   VARCHAR(128) NOT NULL,
    channel             VARCHAR(32)  NOT NULL,           -- shopify|shopee|tiktok
    currency            VARCHAR(10),
    total_price         NUMERIC(12, 2),
    buyer_name          VARCHAR(255),
    buyer_email         VARCHAR(255),
    status              VARCHAR(32)  NOT NULL DEFAULT 'received',
    raw_payload         JSONB        NOT NULL DEFAULT '{}',
    webhook_event_id    VARCHAR(128),                    -- FK ref to webhook_events.event_id
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (channel, external_order_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_orders_v2_channel    ON channel_orders_v2 (channel);
CREATE INDEX IF NOT EXISTS idx_channel_orders_v2_ext        ON channel_orders_v2 (external_order_id);
CREATE INDEX IF NOT EXISTS idx_channel_orders_v2_created    ON channel_orders_v2 (created_at DESC);
