from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── App ──────────────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ── Database ─────────────────────────────────────────────────────────────
    POSTGRES_USER: str = "kbeauty"
    POSTGRES_PASSWORD: str = "kbeauty"
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "kbeauty"

    # When set, this URL is used instead of the components above.
    # CI sets DATABASE_URL_TEST so tests never touch the production DB.
    DATABASE_URL_TEST: Optional[str] = None

    @property
    def DATABASE_URL(self) -> str:  # noqa: N802
        """
        Effective async database URL.

        Priority:
        1. DATABASE_URL_TEST env-var (set by CI / pytest env) → uses test DB
        2. Constructed from POSTGRES_* components
        """
        override = self.DATABASE_URL_TEST or os.environ.get("DATABASE_URL_TEST")
        if override:
            return override
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def DATABASE_URL_SYNC(self) -> str:  # noqa: N802
        """Synchronous (psycopg2) URL derived from the same host/creds."""
        override = self.DATABASE_URL_TEST or os.environ.get("DATABASE_URL_TEST")
        if override:
            # swap asyncpg driver → psycopg2
            return override.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # ── Redis / Celery ────────────────────────────────────────────────────────
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    @property
    def REDIS_URL(self) -> str:  # noqa: N802
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def CELERY_BROKER_URL(self) -> str:  # noqa: N802
        return self.REDIS_URL

    @property
    def CELERY_RESULT_BACKEND(self) -> str:  # noqa: N802
        return self.REDIS_URL

    # ── Shopify ───────────────────────────────────────────────────────────────
    SHOPIFY_WEBHOOK_SECRET: str = "test-secret"
    SHOPIFY_API_KEY: Optional[str] = None
    SHOPIFY_API_SECRET: Optional[str] = None
    SHOPIFY_STORE_DOMAIN: Optional[str] = None

    # ── Admin / JWT (Sprint 5) ────────────────────────────────────────────────
    # JWT signing secret — CHANGE in production!
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 480  # 8 hours

    # Built-in admin credentials (used when no DB user exists)
    ADMIN_EMAIL: str = "admin@kbeauty.local"
    ADMIN_PASSWORD: str = "admin1234"  # bcrypt-hashed in prod via ADMIN_PASSWORD_HASH

    # Margin guard — alert when margin_pct < this value
    MARGIN_GUARD_PCT: float = 15.0

    # StyleKorean base cost ratio (cost / retail price) — used for margin calc
    SUPPLIER_COST_RATIO: float = 0.75

    # ── Sprint 4: Crawler ─────────────────────────────────────────────────────
    STYLEKOREAN_BASE_URL: str = "https://www.stylekorean.com"
    PRODUCT_CRAWL_LIMIT: int = 500
    PRODUCT_CRAWL_INTERVAL: int = 43200   # 12 h
    PRODUCT_SYNC_INTERVAL: int = 1800     # 30 min

    # ── Storage ───────────────────────────────────────────────────────────────
    STORAGE_PATH: str = "./storage"

    # ── Tracking ─────────────────────────────────────────────────────────────
    TRACKING_POLL_INTERVAL: int = 600


@lru_cache
def get_settings() -> Settings:
    return Settings()
