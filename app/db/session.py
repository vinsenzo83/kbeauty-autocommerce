from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

# ── Engine & session factory (lazy-created on first use) ─────────────────────
# We intentionally do NOT create the engine at module-level so that
# test files that import from app.models.* never trigger a real DB connection.
# The engine is created lazily when get_db() is first called.

_engine = None
_session_factory = None


def _get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.DATABASE_URL,
            echo=settings.DEBUG,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=_get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    return _session_factory


# Public accessor so app/main.py can reach the engine for table creation
@property  # type: ignore[misc]
def engine():
    return _get_engine()


# Keep a module-level 'engine' name for backward-compatibility with app/main.py
class _EngineProxy:
    """Thin proxy so ``from app.db.session import engine`` still works."""

    def __getattr__(self, name: str):
        return getattr(_get_engine(), name)

    def begin(self):
        return _get_engine().begin()

    def dispose(self):
        return _get_engine().dispose()


engine = _EngineProxy()


async def get_db() -> AsyncGenerator[AsyncSession, None]:  # type: ignore[return]
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
