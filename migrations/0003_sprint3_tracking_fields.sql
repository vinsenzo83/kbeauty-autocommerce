-- migrations/0003_sprint3_tracking_fields.sql
--
-- Sprint 3: add tracking columns + SHIPPED status to orders table.
--
-- Safe to run via:
--   psql $DATABASE_URL -f migrations/0003_sprint3_tracking_fields.sql
--
-- Each ALTER is wrapped in a DO block → idempotent (safe to re-run).

-- 1. tracking_number
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'orders' AND column_name = 'tracking_number'
    ) THEN
        ALTER TABLE orders ADD COLUMN tracking_number VARCHAR(128);
    END IF;
END$$;

-- 2. tracking_url
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'orders' AND column_name = 'tracking_url'
    ) THEN
        ALTER TABLE orders ADD COLUMN tracking_url VARCHAR(512);
    END IF;
END$$;

-- 3. shipped_at
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'orders' AND column_name = 'shipped_at'
    ) THEN
        ALTER TABLE orders ADD COLUMN shipped_at TIMESTAMPTZ;
    END IF;
END$$;

-- 4. (Optional) update status check constraint to include SHIPPED
-- ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
-- ALTER TABLE orders ADD CONSTRAINT orders_status_check
--     CHECK (status IN ('RECEIVED','VALIDATED','PLACING','PLACED','SHIPPED','FAILED'));
