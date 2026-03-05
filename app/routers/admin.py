from __future__ import annotations

"""
app/routers/admin.py
─────────────────────
Full Admin API router (Sprint 5).

All endpoints except /admin/auth/* require a valid Bearer JWT.
Role hierarchy: ADMIN > OPERATOR > VIEWER.
"""

import os
import uuid
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_db
from app.models.event_log import EventLog
from app.models.order import Order, OrderStatus
from app.services.auth_service import (
    CurrentUser,
    authenticate_user,
    create_access_token,
    get_current_user,
    require_role,
)
from app.services.dashboard_service import (
    compute_alerts,
    compute_health,
    compute_kpi,
    compute_metrics,
    get_orders_chart,
)
from app.services.order_service import (
    get_order_by_id,
    list_orders,
    mark_canceled,
)
from app.services.ticket_service import (
    close_ticket,
    create_ticket,
    get_ticket_by_id,
    list_tickets,
)
from app.services.shopify_service import get_shopify_client
from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════════

class LoginRequest(BaseModel):
    email: str
    password: str


@router.post(
    "/auth/login",
    tags=["auth"],
    summary="Admin login — returns JWT access token",
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    result = await authenticate_user(body.email, body.password, db_session=db)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    email, role = result
    token = create_access_token(subject=email, role=role)
    logger.info("admin.login.success", email=email, role=role)
    return {"access_token": token, "token_type": "bearer", "role": role}


@router.get(
    "/auth/me",
    tags=["auth"],
    summary="Return current authenticated admin user info",
)
async def me(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    return {"email": current_user.email, "role": current_user.role}


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD KPI
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/dashboard/kpi",
    tags=["dashboard"],
    summary="Dashboard KPIs (Asia/Seoul day boundary)",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def dashboard_kpi(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    settings = get_settings()
    return await compute_kpi(db, settings)


@router.get(
    "/dashboard/alerts",
    tags=["dashboard"],
    summary="Alert cards",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def dashboard_alerts(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    settings = get_settings()
    return await compute_alerts(db, settings, redis_url=settings.REDIS_URL)


@router.get(
    "/dashboard/chart",
    tags=["dashboard"],
    summary="7-day orders + revenue chart data",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def dashboard_chart(
    days: int = Query(default=7, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    return await get_orders_chart(db, days=days)


# ═══════════════════════════════════════════════════════════════════════════════
# ORDERS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/orders",
    tags=["orders"],
    summary="List orders with filters + pagination",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def list_orders_endpoint(
    status_filter: Optional[str]   = Query(None, alias="status"),
    supplier:      Optional[str]   = Query(None),
    country:       Optional[str]   = Query(None),
    q:             Optional[str]   = Query(None),
    margin_min:    Optional[float] = Query(None),
    margin_max:    Optional[float] = Query(None),
    date_from:     Optional[str]   = Query(None),
    date_to:       Optional[str]   = Query(None),
    page:          int             = Query(1, ge=1),
    page_size:     int             = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    orders, total = await list_orders(
        db,
        status_filter   = status_filter,
        supplier_filter = supplier,
        country_filter  = country,
        q               = q,
        margin_min      = margin_min,
        margin_max      = margin_max,
        date_from       = date_from,
        date_to         = date_to,
        page            = page,
        page_size       = page_size,
    )
    return {
        "total":     total,
        "page":      page,
        "page_size": page_size,
        "items":     [_order_summary(o) for o in orders],
    }


@router.get(
    "/orders/{order_id}",
    tags=["orders"],
    summary="Order detail with event_log + artifact paths",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def get_order_detail(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    order = await get_order_by_id(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    # Last 50 event_log entries for this order
    from sqlalchemy import select
    res = await db.execute(
        select(EventLog)
        .where(EventLog.payload_ref == order.shopify_order_id)
        .order_by(EventLog.created_at.desc())  # type: ignore[attr-defined]
        .limit(50)
    )
    try:
        events = res.scalars().all()
    except Exception:
        events = []

    # Artifact paths
    artifacts = _list_artifacts(str(order_id))

    return {
        **_order_summary(order),
        "shipping_address": order.shipping_address_json,
        "line_items":       order.line_items_json,
        "events":           [_event_summary(e) for e in events],
        "artifacts":        artifacts,
    }


def _order_summary(o: Order) -> dict[str, Any]:
    return {
        "id":                str(o.id),
        "shopify_order_id":  o.shopify_order_id,
        "email":             o.email,
        "total_price":       str(o.total_price) if o.total_price else None,
        "currency":          o.currency,
        "financial_status":  o.financial_status,
        "status":            o.status,
        "supplier":          o.supplier,
        "supplier_order_id": o.supplier_order_id,
        "placed_at":         o.placed_at.isoformat()  if o.placed_at  else None,
        "shipped_at":        o.shipped_at.isoformat() if o.shipped_at else None,
        "tracking_number":   o.tracking_number,
        "tracking_url":      o.tracking_url,
        "fail_reason":       o.fail_reason,
        "created_at":        o.created_at.isoformat(),
        "updated_at":        o.updated_at.isoformat(),
    }


def _event_summary(e: EventLog) -> dict[str, Any]:
    return {
        "id":         str(e.id),
        "source":     e.source,
        "event_type": e.event_type,
        "note":       e.note,
        "created_at": e.created_at.isoformat(),
    }


def _list_artifacts(order_id: str) -> list[str]:
    settings   = get_settings()
    storage    = Path(os.getenv("STORAGE_PATH", getattr(settings, "STORAGE_PATH", "./storage")))
    bot_dir    = storage / "bot_failures" / order_id
    paths: list[str] = []
    if bot_dir.exists():
        for p in bot_dir.iterdir():
            if p.is_file():
                paths.append(str(p.relative_to(storage)))
    return paths


# ═══════════════════════════════════════════════════════════════════════════════
# OPS ACTIONS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/orders/{order_id}/retry-place",
    status_code=202,
    tags=["ops"],
    summary="Re-enqueue supplier placement for a FAILED order",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def retry_place_order(
    order_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    order = await _get_order_or_404(db, order_id)
    if order.status != OrderStatus.FAILED:
        raise HTTPException(
            status_code=409,
            detail=f"Order status is '{order.status}'. Only FAILED orders can be retried.",
        )
    celery_app.send_task("workers.tasks_order.retry_place_order", args=[str(order_id)])
    await _log_event(db, order, "order/retry_place", f"Retry enqueued by {current_user.email}")
    logger.info("admin.retry_place", order_id=str(order_id), by=current_user.email)
    return {"status": "accepted", "order_id": str(order_id), "message": "retry-place enqueued"}


@router.post(
    "/orders/{order_id}/force-tracking",
    status_code=202,
    tags=["ops"],
    summary="Force a tracking poll for a PLACED order",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def force_tracking(
    order_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    order = await _get_order_or_404(db, order_id)
    if order.status not in (OrderStatus.PLACED, OrderStatus.SHIPPED):
        raise HTTPException(
            status_code=409,
            detail=f"Order status is '{order.status}'. Force-tracking only for PLACED/SHIPPED.",
        )
    celery_app.send_task("workers.tasks_tracking.poll_tracking")
    await _log_event(db, order, "order/force_tracking", f"Forced by {current_user.email}")
    return {"status": "accepted", "order_id": str(order_id), "message": "tracking poll enqueued"}


@router.post(
    "/orders/{order_id}/cancel-refund",
    status_code=202,
    tags=["ops"],
    summary="Cancel order + issue Shopify refund stub",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def cancel_refund(
    order_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    order = await _get_order_or_404(db, order_id)
    if order.status in ("CANCELED", OrderStatus.SHIPPED):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel order in status '{order.status}'.",
        )
    # MVP: stub Shopify refund
    shopify = get_shopify_client()
    await shopify.cancel_order(order.shopify_order_id, reason="admin_cancel_refund")

    await mark_canceled(db, order, reason=f"Admin cancel by {current_user.email}")
    await _log_event(db, order, "order/canceled", f"Canceled & refund stub by {current_user.email}")
    logger.info("admin.cancel_refund", order_id=str(order_id), by=current_user.email)
    return {"status": "accepted", "order_id": str(order_id), "message": "order canceled"}


class CreateTicketRequest(BaseModel):
    type:    str = "OTHER"
    subject: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    note:    Optional[str] = None


@router.post(
    "/orders/{order_id}/create-ticket",
    status_code=201,
    tags=["ops"],
    summary="Create a support ticket for an order",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def create_order_ticket(
    order_id: UUID,
    body: CreateTicketRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _get_order_or_404(db, order_id)
    ticket = await create_ticket(
        db,
        order_id   = order_id,
        ticket_type= body.type,
        subject    = body.subject,
        payload    = body.payload,
        note       = body.note,
        created_by = current_user.email,
    )
    await db.commit()
    return {"ticket_id": str(ticket.id), "status": "created"}


@router.post(
    "/orders/{order_id}/switch-supplier",
    status_code=409,
    tags=["ops"],
    summary="Switch supplier — not yet implemented (MVP stub)",
    dependencies=[Depends(require_role("ADMIN"))],
)
async def switch_supplier(order_id: UUID) -> dict[str, Any]:
    raise HTTPException(
        status_code=409,
        detail="switch-supplier is not yet implemented. Planned for Sprint 6.",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TICKETS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/tickets",
    tags=["tickets"],
    summary="List support tickets",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def list_tickets_endpoint(
    status_filter: Optional[str] = Query(None, alias="status"),
    type_filter:   Optional[str] = Query(None, alias="type"),
    q:             Optional[str] = Query(None),
    page:          int           = Query(1, ge=1),
    page_size:     int           = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tickets, total = await list_tickets(
        db,
        status_filter = status_filter,
        type_filter   = type_filter,
        q             = q,
        page          = page,
        page_size     = page_size,
    )
    return {
        "total":     total,
        "page":      page,
        "page_size": page_size,
        "items":     [_ticket_summary(t) for t in tickets],
    }


@router.get(
    "/tickets/{ticket_id}",
    tags=["tickets"],
    summary="Ticket detail",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def get_ticket_detail(
    ticket_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ticket = await get_ticket_by_id(db, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return _ticket_summary(ticket, full=True)


@router.post(
    "/tickets/{ticket_id}/close",
    tags=["tickets"],
    summary="Close a ticket",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def close_ticket_endpoint(
    ticket_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ticket = await close_ticket(db, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    await db.commit()
    logger.info("admin.ticket_closed", ticket_id=str(ticket_id), by=current_user.email)
    return {"ticket_id": str(ticket_id), "status": "closed"}


def _ticket_summary(t: Any, full: bool = False) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id":         str(t.id),
        "order_id":   str(t.order_id) if t.order_id else None,
        "type":       t.type,
        "status":     t.status,
        "subject":    t.subject,
        "created_by": t.created_by,
        "closed_at":  t.closed_at.isoformat() if t.closed_at else None,
        "created_at": t.created_at.isoformat(),
    }
    if full:
        d["payload"] = t.payload
        d["note"]    = t.note
    return d


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/metrics",
    tags=["metrics"],
    summary="Order count metrics: today / pending / processing / failed",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def admin_metrics(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await compute_metrics(db)


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/health",
    tags=["health"],
    summary="System health: DB, Redis, Celery, recent failures",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def admin_health(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    settings = get_settings()
    return await compute_health(db, redis_url=settings.REDIS_URL)


# ═══════════════════════════════════════════════════════════════════════════════
# ARTIFACTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/artifacts",
    tags=["artifacts"],
    summary="Serve bot failure artifact file (path traversal protected)",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def get_artifact(
    path: str = Query(..., description="Relative path under STORAGE_PATH"),
) -> FileResponse:
    settings     = get_settings()
    storage_root = Path(
        os.getenv("STORAGE_PATH", getattr(settings, "STORAGE_PATH", "./storage"))
    ).resolve()

    requested = (storage_root / path).resolve()

    # Path traversal guard
    if not str(requested).startswith(str(storage_root)):
        raise HTTPException(status_code=400, detail="Invalid artifact path")
    if not requested.exists() or not requested.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")

    return FileResponse(str(requested))


# ═══════════════════════════════════════════════════════════════════════════════
# INVENTORY (Sprint 6)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/inventory/stale",
    tags=["inventory"],
    summary="Products not checked in the last 24 h (stale inventory)",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def get_stale_inventory(
    hours: int = Query(24, ge=1, le=168, description="Staleness threshold in hours"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return products whose ``last_checked_at`` is older than ``hours`` hours
    (or NULL — never checked).

    Response
    --------
    {
        "stale_count": int,
        "items": [
            {
                "id": str,
                "name": str,
                "supplier_product_id": str,
                "supplier_product_url": str,
                "stock_status": str,
                "last_checked_at": str | null,
                "last_seen_price": float | null,
                "shopify_product_id": str | null,
            },
            ...
        ]
    }
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import or_, select

    from app.models.product import Product

    threshold = datetime.now(timezone.utc) - timedelta(hours=hours)

    result = await db.execute(
        select(Product).where(
            or_(
                Product.last_checked_at.is_(None),  # type: ignore[attr-defined]
                Product.last_checked_at < threshold,  # type: ignore[operator]
            )
        ).order_by(Product.last_checked_at.asc().nullsfirst())  # type: ignore[attr-defined]
    )
    products = result.scalars().all()

    items = [
        {
            "id":                   str(p.id),
            "name":                 p.name,
            "supplier_product_id":  p.supplier_product_id,
            "supplier_product_url": p.supplier_product_url,
            "stock_status":         p.stock_status,
            "last_checked_at":      p.last_checked_at.isoformat() if p.last_checked_at else None,
            "last_seen_price":      float(p.last_seen_price) if p.last_seen_price is not None else None,
            "shopify_product_id":   p.shopify_product_id,
        }
        for p in products
    ]

    return {"stale_count": len(items), "items": items}


# ═══════════════════════════════════════════════════════════════════════════════
# SUPPLIERS (Sprint 7)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/suppliers/products/{product_id}",
    tags=["suppliers"],
    summary="Supplier products for a given product_id",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def get_supplier_products_for_product(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return all supplier_products rows for the given product_id.

    Response
    --------
    {
        "product_id": str,
        "items": [
            {
                "supplier":             str,
                "supplier_product_id":  str,
                "price":                float | null,
                "stock_status":         str,
                "last_checked_at":      str | null,
            },
            ...
        ]
    }
    """
    from app.services.supplier_product_service import get_supplier_products
    rows = await get_supplier_products(db, product_id)
    items = [
        {
            "supplier":            r.supplier,
            "supplier_product_id": r.supplier_product_id,
            "price":               float(r.price) if r.price is not None else None,
            "stock_status":        r.stock_status,
            "last_checked_at":     r.last_checked_at.isoformat() if r.last_checked_at else None,
        }
        for r in rows
    ]
    return {"product_id": str(product_id), "items": items}


@router.get(
    "/suppliers/summary",
    tags=["suppliers"],
    summary="Supplier product counts by supplier and stock status",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def get_supplier_summary(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Aggregate supplier_products rows by supplier and stock_status.

    Response
    --------
    {
        "summary": [
            {"supplier": "STYLEKOREAN", "stock_status": "IN_STOCK",    "count": 42},
            {"supplier": "STYLEKOREAN", "stock_status": "OUT_OF_STOCK","count": 3},
            ...
        ]
    }
    """
    from sqlalchemy import func as sa_func, select as sa_select
    from app.models.supplier_product import SupplierProduct

    stmt = (
        sa_select(
            SupplierProduct.supplier,
            SupplierProduct.stock_status,
            sa_func.count().label("count"),
        )
        .group_by(SupplierProduct.supplier, SupplierProduct.stock_status)
        .order_by(SupplierProduct.supplier, SupplierProduct.stock_status)
    )
    result = await db.execute(stmt)
    rows   = result.all()
    summary = [
        {"supplier": r.supplier, "stock_status": r.stock_status, "count": r.count}
        for r in rows
    ]
    return {"summary": summary}


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

async def _get_order_or_404(db: AsyncSession, order_id: UUID) -> Order:
    order = await get_order_by_id(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    return order


async def _log_event(
    db: AsyncSession,
    order: Order,
    event_type: str,
    note: str,
) -> None:
    import hashlib, time
    raw_hash = f"{event_type}:{order.shopify_order_id}:{time.time()}"
    event_hash = hashlib.sha256(raw_hash.encode()).hexdigest()
    event = EventLog(
        event_hash  = event_hash,
        source      = "admin",
        event_type  = event_type,
        payload_ref = order.shopify_order_id,
        note        = note,
    )
    db.add(event)
    await db.flush()
