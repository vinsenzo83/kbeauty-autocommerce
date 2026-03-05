-- =============================================================================
-- Migration 0018 – Sprint 16: Observability — alert_rules & alert_events
-- =============================================================================
-- alert_rules  : defines thresholds for operational KPI monitoring
-- alert_events : fired events when a rule threshold is breached
-- =============================================================================

-- ── alert_rules ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alert_rules (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(128) NOT NULL UNIQUE,   -- human-readable rule name
    metric          VARCHAR(64)  NOT NULL,           -- KPI key, e.g. 'fulfillment_error_rate'
    operator        VARCHAR(8)   NOT NULL,           -- '>', '>=', '<', '<=', '=='
    threshold       NUMERIC(18,6) NOT NULL,          -- numeric threshold value
    window_minutes  INTEGER      NOT NULL DEFAULT 60,-- evaluation window in minutes
    severity        VARCHAR(16)  NOT NULL DEFAULT 'warning',  -- info|warning|critical
    enabled         BOOLEAN      NOT NULL DEFAULT TRUE,
    notes           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Default operational rules (idempotent INSERT)
INSERT INTO alert_rules (name, metric, operator, threshold, window_minutes, severity, notes)
VALUES
    ('high_fulfillment_error_rate',  'fulfillment_error_rate',  '>',  0.10, 60,  'critical', 'Fires when >10% of supplier orders fail in a 60-min window'),
    ('low_repricing_updates',        'repricing_updated_count', '<',  1.0,  360, 'warning',  'No products repriced in the last 6 hours'),
    ('high_order_backlog',           'pending_order_count',     '>',  50.0, 60,  'warning',  'More than 50 orders waiting fulfillment'),
    ('discovery_no_candidates',      'discovery_candidate_count','<', 1.0,  1440,'info',     'Discovery pipeline produced no candidates today'),
    ('publish_job_failure',          'publish_failure_count',   '>',  2.0,  360, 'warning',  'More than 2 publish failures in 6 hours')
ON CONFLICT (name) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_alert_rules_metric  ON alert_rules (metric);
CREATE INDEX IF NOT EXISTS idx_alert_rules_enabled ON alert_rules (enabled);


-- ── alert_events ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alert_events (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id         UUID        NOT NULL,            -- FK → alert_rules.id (soft)
    rule_name       VARCHAR(128) NOT NULL,           -- denormalised for quick reads
    metric          VARCHAR(64)  NOT NULL,
    observed_value  NUMERIC(18,6) NOT NULL,
    threshold       NUMERIC(18,6) NOT NULL,
    severity        VARCHAR(16)  NOT NULL,
    status          VARCHAR(16)  NOT NULL DEFAULT 'open',  -- open|acknowledged|resolved
    notes           TEXT,
    fired_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alert_events_rule_id    ON alert_events (rule_id);
CREATE INDEX IF NOT EXISTS idx_alert_events_status     ON alert_events (status);
CREATE INDEX IF NOT EXISTS idx_alert_events_fired_at   ON alert_events (fired_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_events_severity   ON alert_events (severity);
