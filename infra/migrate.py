#!/usr/bin/env python3
"""
infra/migrate.py
─────────────────
Production SQL migration runner.

Applies all SQL files in migrations/ in lexicographic order.
Each file is executed inside a single transaction (idempotent via
IF NOT EXISTS / ON CONFLICT DO NOTHING clauses in each migration).

Usage (inside Docker):
    python infra/migrate.py

Usage (direct with venv):
    DATABASE_URL=postgresql+psycopg://... python infra/migrate.py

Environment
-----------
DATABASE_URL     : Full sync psycopg URL  (preferred)
POSTGRES_*       : Individual components  (fallback)
"""

from __future__ import annotations

import glob
import os
import sys
import time

# ── Resolve DATABASE_URL ──────────────────────────────────────────────────────

def _build_url() -> str:
    """Build a synchronous psycopg3 URL from env vars."""
    url = os.getenv("DATABASE_URL", "")
    if url:
        # asyncpg → psycopg for sync migration runner
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg://") \
                  .replace("postgresql+asyncpg+ssl://", "postgresql+psycopg://")

    user     = os.getenv("POSTGRES_USER",     "kbeauty")
    password = os.getenv("POSTGRES_PASSWORD", "kbeauty")
    host     = os.getenv("POSTGRES_HOST",     "postgres")
    port     = os.getenv("POSTGRES_PORT",     "5432")
    db       = os.getenv("POSTGRES_DB",       "kbeauty")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


# ── Main ─────────────────────────────────────────────────────────────────────

def run_migrations() -> None:
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        print("ERROR: sqlalchemy not installed. Run: pip install sqlalchemy psycopg[binary]")
        sys.exit(1)

    url = _build_url()
    # Mask password in log output
    safe_url = url.split("@")[-1] if "@" in url else url
    print(f"[migrate] Connecting to: ...@{safe_url}")

    # Retry logic – wait for PostgreSQL to be ready
    engine = None
    for attempt in range(1, 11):
        try:
            engine = create_engine(url, pool_pre_ping=True, echo=False)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            print(f"[migrate] PostgreSQL ready (attempt {attempt})")
            break
        except Exception as exc:
            print(f"[migrate] Waiting for PostgreSQL... attempt {attempt}/10 ({exc})")
            time.sleep(3)
    else:
        print("[migrate] ERROR: Could not connect to PostgreSQL after 10 attempts.")
        sys.exit(1)

    # Discover migration files
    migration_dir = os.path.join(os.path.dirname(__file__), "..", "migrations")
    files = sorted(glob.glob(os.path.join(migration_dir, "*.sql")))

    if not files:
        print("[migrate] No migration files found.")
        return

    print(f"[migrate] Found {len(files)} migration(s):")
    for f in files:
        print(f"  {os.path.basename(f)}")

    # Apply each migration in its own transaction for idempotency
    applied = 0
    errors  = 0
    for filepath in files:
        name = os.path.basename(filepath)
        sql  = open(filepath, encoding="utf-8").read().strip()
        if not sql:
            print(f"  [skip]  {name}  (empty)")
            continue
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
            print(f"  [ok]    {name}")
            applied += 1
        except Exception as exc:
            err_msg = str(exc)
            # Treat "already exists" errors as benign (idempotent re-run)
            if any(kw in err_msg.lower() for kw in ("already exists", "duplicate column", "relation already exists")):
                print(f"  [skip]  {name}  (already applied – {err_msg.splitlines()[0]})")
                applied += 1
            else:
                print(f"  [ERROR] {name}: {exc}")
                errors += 1
                raise  # Abort on unexpected error

    print(f"\n[migrate] Done: {applied} applied, {errors} errors.")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    run_migrations()
