-- ============================================================
-- Migration 0010: Sales Channels (Multi-Channel Commerce Engine)
-- Sprint 9 – Introduce sales_channels, channel_products, channel_orders.
-- Idempotent (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- 1. sales_channels
--    Registry of supported sales channels (shopify, shopee, tiktok_shop …)
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sales_channels (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(32)  UNIQUE NOT NULL,
    type        VARCHAR(32)  NOT NULL DEFAULT 'marketplace',
    enabled     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sales_channels_name
    ON sales_channels (name);
CREATE INDEX IF NOT EXISTS idx_sales_channels_enabled
    ON sales_channels (enabled);

-- Seed known channels (idempotent)
INSERT INTO sales_channels (name, type, enabled)
VALUES
    ('shopify',      'owned_store',  TRUE),
    ('shopee',       'marketplace',  TRUE),
    ('tiktok_shop',  'marketplace',  TRUE)
ON CONFLICT (name) DO NOTHING;

-- ────────────────────────────────────────────────────────────
-- 2. channel_products
--    Maps a canonical_product to its external listing on each channel.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS channel_products (
    id                      UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_product_id    UUID          NOT NULL REFERENCES canonical_products(id) ON DELETE CASCADE,
    channel                 VARCHAR(32)   NOT NULL,
    external_product_id     VARCHAR(128),
    external_variant_id     VARCHAR(128),
    price                   NUMERIC(12,2),
    currency                VARCHAR(8)    NOT NULL DEFAULT 'USD',
    status                  VARCHAR(32)   NOT NULL DEFAULT 'active',
    created_at              TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_channel_products_variant  UNIQUE (channel, external_variant_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_products_canonical_product_id
    ON channel_products (canonical_product_id);
CREATE INDEX IF NOT EXISTS idx_channel_products_channel
    ON channel_products (channel);
CREATE INDEX IF NOT EXISTS idx_channel_products_status
    ON channel_products (status);

-- ────────────────────────────────────────────────────────────
-- 3. channel_orders
--    Orders received from any channel, linked to canonical_product.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS channel_orders (
    id                      UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    channel                 VARCHAR(32)   NOT NULL,
    external_order_id       VARCHAR(128)  NOT NULL,
    canonical_product_id    UUID          REFERENCES canonical_products(id) ON DELETE SET NULL,
    quantity                INTEGER       NOT NULL DEFAULT 1,
    price                   NUMERIC(12,2),
    currency                VARCHAR(8)    NOT NULL DEFAULT 'USD',
    status                  VARCHAR(32)   NOT NULL DEFAULT 'pending',
    created_at              TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_channel_orders_external  UNIQUE (channel, external_order_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_orders_channel
    ON channel_orders (channel);
CREATE INDEX IF NOT EXISTS idx_channel_orders_canonical_product_id
    ON channel_orders (canonical_product_id);
CREATE INDEX IF NOT EXISTS idx_channel_orders_status
    ON channel_orders (status);
CREATE INDEX IF NOT EXISTS idx_channel_orders_created_at
    ON channel_orders (created_at DESC);
