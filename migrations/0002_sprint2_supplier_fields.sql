-- migrations/0002_sprint2_supplier_fields.sql
--
-- Sprint 2: add supplier placement columns to orders table.
--
-- This repo uses SQLAlchemy create_all for initial schema.
-- For subsequent schema changes we provide plain SQL migration files
-- that are safe to run via:
--
--   psql $DATABASE_URL -f migrations/0002_sprint2_supplier_fields.sql
--
-- or inside the running api container:
--   docker compose exec api psql $DATABASE_URL -f migrations/0002_sprint2_supplier_fields.sql
--
-- Each ALTER is wrapped in a DO block so it is idempotent (safe to re-run).

-- 1. supplier column
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'orders' AND column_name = 'supplier'
    ) THEN
        ALTER TABLE orders ADD COLUMN supplier VARCHAR(64);
    END IF;
END$$;

-- 2. supplier_order_id column
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'orders' AND column_name = 'supplier_order_id'
    ) THEN
        ALTER TABLE orders ADD COLUMN supplier_order_id VARCHAR(128);
    END IF;
END$$;

-- 3. placed_at column
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'orders' AND column_name = 'placed_at'
    ) THEN
        ALTER TABLE orders ADD COLUMN placed_at TIMESTAMPTZ;
    END IF;
END$$;

-- 4. Extend status check constraint (optional, informational only —
--    the application enforces the ENUM; PostgreSQL VARCHAR has no built-in check here)
-- NOTE: If you added a CHECK constraint on status in a previous migration,
--       drop and recreate it to include PLACING and PLACED:
--
-- ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
-- ALTER TABLE orders ADD CONSTRAINT orders_status_check
--     CHECK (status IN ('RECEIVED','VALIDATED','PLACING','PLACED','FAILED'));
