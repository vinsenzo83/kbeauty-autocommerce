-- migrations/0001_initial_schema.sql
--
-- Sprint 1: Initial schema — orders + event_logs tables.
--
-- Idempotent: every statement uses IF NOT EXISTS or DO-block guards.
-- Run with:
--   psql $DATABASE_URL -f migrations/0001_initial_schema.sql
--
-- NOTE: Subsequent sprints (0002..N) add columns / tables via ALTER / CREATE.

-- ── orders ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orders (
    id                    UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    shopify_order_id      VARCHAR(64)   NOT NULL,
    email                 VARCHAR(255),
    total_price           NUMERIC(12, 2),
    currency              VARCHAR(10),
    shipping_address_json JSONB,
    line_items_json       JSONB,
    financial_status      VARCHAR(64),
    status                VARCHAR(16)   NOT NULL DEFAULT 'RECEIVED',
    fail_reason           TEXT,

    -- Sprint 2 columns (safe to include here; 0002 migration guards with IF NOT EXISTS)
    supplier              VARCHAR(64),
    supplier_order_id     VARCHAR(128),
    placed_at             TIMESTAMPTZ,

    -- Sprint 3 columns
    tracking_number       VARCHAR(128),
    tracking_url          VARCHAR(512),
    shipped_at            TIMESTAMPTZ,

    created_at            TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_shopify_order_id
    ON orders (shopify_order_id);

CREATE INDEX IF NOT EXISTS idx_orders_status
    ON orders (status);

-- ── event_logs ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS event_logs (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    event_hash  VARCHAR(64)  NOT NULL,
    source      VARCHAR(64)  NOT NULL,
    event_type  VARCHAR(128) NOT NULL,
    payload_ref VARCHAR(128),
    note        TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_event_logs_event_hash
    ON event_logs (event_hash);

CREATE INDEX IF NOT EXISTS idx_event_logs_source
    ON event_logs (source);

-- ── updated_at auto-update trigger (optional, informational) ───────────────────
-- The application layer sets updated_at on every write via SQLAlchemy onupdate.
-- If you want a DB-level trigger uncomment the block below:
--
-- CREATE OR REPLACE FUNCTION set_updated_at()
-- RETURNS TRIGGER LANGUAGE plpgsql AS $$
-- BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $$;
--
-- DO $$ BEGIN
--     IF NOT EXISTS (
--         SELECT 1 FROM pg_trigger WHERE tgname = 'orders_set_updated_at'
--     ) THEN
--         CREATE TRIGGER orders_set_updated_at
--             BEFORE UPDATE ON orders
--             FOR EACH ROW EXECUTE FUNCTION set_updated_at();
--     END IF;
-- END $$;
