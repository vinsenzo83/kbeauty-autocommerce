-- Migration: 0005_sprint5_admin_dashboard
-- Description: Add admin_users and tickets tables (Sprint 5)
-- Idempotent: uses IF NOT EXISTS

-- ── admin_users ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS admin_users (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email         VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(16)  NOT NULL DEFAULT 'VIEWER',
    is_active     VARCHAR(1)   NOT NULL DEFAULT '1',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT admin_users_email_unique UNIQUE (email)
);

CREATE INDEX IF NOT EXISTS idx_admin_users_email
    ON admin_users (email);

-- ── tickets ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tickets (
    id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id   UUID,
    type       VARCHAR(64)  NOT NULL DEFAULT 'OTHER',
    status     VARCHAR(16)  NOT NULL DEFAULT 'OPEN',
    subject    VARCHAR(256),
    payload    JSONB,
    note       TEXT,
    created_by VARCHAR(255),
    closed_at  TIMESTAMPTZ,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tickets_order_id
    ON tickets (order_id);

CREATE INDEX IF NOT EXISTS idx_tickets_status
    ON tickets (status);

-- ── order CANCELED status support ─────────────────────────────────────────────
-- The orders.status column is VARCHAR(16) so no ALTER needed; CANCELED fits.
-- No schema change required.
