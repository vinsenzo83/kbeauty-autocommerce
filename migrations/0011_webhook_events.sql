-- ============================================================
-- Migration 0011: webhook_events (Multi-channel ingress + idempotency)
-- Sprint 10 – Unified webhook event log for Shopify/Shopee/TikTok.
-- Idempotent (IF NOT EXISTS).
-- ============================================================

CREATE TABLE IF NOT EXISTS webhook_events (
    id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id     VARCHAR(128) UNIQUE NOT NULL,          -- idempotency key
    channel      VARCHAR(32)  NOT NULL,                  -- shopify|shopee|tiktok
    topic        VARCHAR(64)  NOT NULL,                  -- order.created|product.updated
    external_id  VARCHAR(128),                           -- order/product external id
    occurred_at  TIMESTAMPTZ,
    received_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    status       VARCHAR(16)  NOT NULL DEFAULT 'received', -- received|processed|failed
    error        TEXT,
    payload_json JSONB        NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_channel  ON webhook_events (channel);
CREATE INDEX IF NOT EXISTS idx_webhook_events_topic    ON webhook_events (topic);
CREATE INDEX IF NOT EXISTS idx_webhook_events_status   ON webhook_events (status);
CREATE INDEX IF NOT EXISTS idx_webhook_events_received ON webhook_events (received_at DESC);
CREATE INDEX IF NOT EXISTS idx_webhook_events_ext_id   ON webhook_events (external_id);
