from __future__ import annotations

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.logging import configure_logging

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    configure_logging()
    settings = get_settings()
    logger.info("starting up", env=settings.APP_ENV)

    # Import lazily to avoid module-level engine creation in tests
    from app.db.session import engine
    from app.models.order import Base as OrderBase
    from app.models.event_log import Base as EventBase
    from app.models.product import Base as ProductBase
    from app.models.admin_user import Base as AdminUserBase
    from app.models.ticket import Base as TicketBase
    from app.models.webhook_event import Base as WebhookEventBase      # Sprint 10
    from app.models.channel_order import Base as ChannelOrderBase      # Sprint 10

    async with engine.begin() as conn:
        await conn.run_sync(OrderBase.metadata.create_all)
        await conn.run_sync(EventBase.metadata.create_all)
        await conn.run_sync(ProductBase.metadata.create_all)
        await conn.run_sync(AdminUserBase.metadata.create_all)
        await conn.run_sync(TicketBase.metadata.create_all)
        await conn.run_sync(WebhookEventBase.metadata.create_all)      # Sprint 10
        await conn.run_sync(ChannelOrderBase.metadata.create_all)      # Sprint 10

    logger.info("database tables ensured")
    yield
    logger.info("shutting down")
    await engine.dispose()


def create_app(use_lifespan: bool = True) -> FastAPI:
    configure_logging()
    settings = get_settings()

    from app.webhooks.shopify import router as shopify_router
    from app.webhooks.ingress import router as ingress_router   # Sprint 10
    from app.routers.admin import router as admin_router

    app = FastAPI(
        title="KBeauty AutoCommerce API",
        version="0.6.0",
        debug=settings.DEBUG,
        lifespan=lifespan if use_lifespan else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(shopify_router, prefix="/webhooks/shopify", tags=["webhooks-legacy"])
    app.include_router(ingress_router, prefix="/webhook",          tags=["webhooks"])   # Sprint 10
    app.include_router(admin_router,   prefix="/admin",            tags=["admin"])

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.APP_ENV}

    return app


app = create_app()
