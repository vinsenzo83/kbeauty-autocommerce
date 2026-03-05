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
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
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
# CANONICAL PRODUCTS (Sprint 8)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/canonical/products",
    tags=["canonical"],
    summary="List all canonical products",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def list_canonical_products(
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return up to *limit* canonical_products rows."""
    from sqlalchemy import select as _select
    from app.models.canonical_product import CanonicalProduct

    stmt   = _select(CanonicalProduct).limit(limit)
    result = await db.execute(stmt)
    items  = [
        {
            "id":              str(cp.id),
            "canonical_sku":   cp.canonical_sku,
            "name":            cp.name,
            "brand":           cp.brand,
            "size_ml":         cp.size_ml,
            "pricing_enabled": cp.pricing_enabled,
            "last_price":      float(cp.last_price) if cp.last_price is not None else None,
            "last_price_at":   cp.last_price_at.isoformat() if cp.last_price_at else None,
        }
        for cp in result.scalars().all()
    ]
    return {"count": len(items), "items": items}


@router.get(
    "/canonical/products/{canonical_id}",
    tags=["canonical"],
    summary="Get a single canonical product",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def get_canonical_product(
    canonical_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return one canonical_product by id."""
    from sqlalchemy import select as _select
    from app.models.canonical_product import CanonicalProduct

    stmt   = _select(CanonicalProduct).where(CanonicalProduct.id == canonical_id)
    result = await db.execute(stmt)
    cp     = result.scalar_one_or_none()
    if cp is None:
        raise HTTPException(status_code=404, detail=f"Canonical product {canonical_id} not found")

    return {
        "id":                    str(cp.id),
        "canonical_sku":         cp.canonical_sku,
        "name":                  cp.name,
        "brand":                 cp.brand,
        "size_ml":               cp.size_ml,
        "pricing_enabled":       cp.pricing_enabled,
        "target_margin_rate":    float(cp.target_margin_rate)    if cp.target_margin_rate    else None,
        "min_margin_abs":        float(cp.min_margin_abs)        if cp.min_margin_abs        else None,
        "shipping_cost_default": float(cp.shipping_cost_default) if cp.shipping_cost_default else None,
        "last_price":            float(cp.last_price)            if cp.last_price            else None,
        "last_price_at":         cp.last_price_at.isoformat()    if cp.last_price_at         else None,
        "created_at":            cp.created_at.isoformat()       if cp.created_at            else None,
    }


@router.get(
    "/canonical/products/{canonical_id}/suppliers",
    tags=["canonical"],
    summary="Supplier products for a canonical product",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def get_canonical_suppliers(
    canonical_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return all supplier_products rows for a canonical_product_id."""
    from sqlalchemy import select as _select
    from app.models.supplier_product import SupplierProduct

    stmt   = _select(SupplierProduct).where(
        SupplierProduct.canonical_product_id == canonical_id,
    )
    result = await db.execute(stmt)
    rows   = result.scalars().all()
    items  = [
        {
            "supplier":             r.supplier,
            "supplier_product_id":  r.supplier_product_id,
            "supplier_product_url": getattr(r, "supplier_product_url", None),
            "price":                float(r.price) if r.price is not None else None,
            "stock_status":         r.stock_status,
            "last_checked_at":      r.last_checked_at.isoformat() if r.last_checked_at else None,
        }
        for r in rows
    ]
    return {"canonical_product_id": str(canonical_id), "items": items}


@router.post(
    "/canonical/backfill",
    tags=["canonical"],
    summary="Backfill canonical_product_id for all legacy product rows",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def canonical_backfill(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Run the canonical backfill job: for every Product without a
    canonical_product_id, create/find a CanonicalProduct and link it.
    """
    from sqlalchemy import select as _select
    from app.models.product import Product
    from app.services.canonical_service import get_or_create_canonical_from_product

    stmt     = _select(Product).where(Product.canonical_product_id.is_(None))
    result   = await db.execute(stmt)
    products = list(result.scalars().all())

    linked = 0
    errors = 0
    for product in products:
        try:
            await get_or_create_canonical_from_product(product, db)
            linked += 1
        except Exception as exc:
            errors += 1
            logger.error(
                "admin.canonical_backfill.error",
                product_id=str(product.id),
                error=str(exc),
            )

    await db.commit()
    return {"linked": linked, "errors": errors, "total": len(products)}


# ═══════════════════════════════════════════════════════════════════════════════
# PRICING ENGINE (Sprint 8)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/pricing/quotes",
    tags=["pricing"],
    summary="List recent price quotes",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def list_price_quotes(
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return recent price_quotes ordered newest first."""
    from sqlalchemy import select as _select
    from app.models.price_quote import PriceQuote

    stmt   = _select(PriceQuote).order_by(PriceQuote.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    items  = [
        {
            "id":                    str(q.id),
            "canonical_product_id":  str(q.canonical_product_id),
            "supplier":              q.supplier,
            "supplier_price":        float(q.supplier_price),
            "shipping_cost":         float(q.shipping_cost),
            "fee_rate":              float(q.fee_rate),
            "target_margin_rate":    float(q.target_margin_rate),
            "min_margin_abs":        float(q.min_margin_abs),
            "computed_price":        float(q.computed_price),
            "rounded_price":         float(q.rounded_price),
            "reason":                q.reason,
            "created_at":            q.created_at.isoformat() if q.created_at else None,
        }
        for q in result.scalars().all()
    ]
    return {"count": len(items), "items": items}


@router.post(
    "/pricing/sync",
    tags=["pricing"],
    summary="Trigger pricing sync for all canonical products",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def trigger_pricing_sync() -> dict[str, Any]:
    """Dispatch the sync_prices Celery task asynchronously."""
    task = celery_app.send_task("workers.tasks_pricing.sync_prices")
    logger.info("admin.pricing_sync.dispatched", task_id=str(task.id))
    return {"task_id": str(task.id), "status": "dispatched"}


@router.post(
    "/pricing/canonical/{canonical_id}/sync",
    tags=["pricing"],
    summary="Trigger pricing sync for one canonical product",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def trigger_pricing_sync_for_canonical(
    canonical_id: UUID,
) -> dict[str, Any]:
    """Dispatch sync_price_for_canonical for a single product."""
    task = celery_app.send_task(
        "workers.tasks_pricing.sync_price_for_canonical",
        args=[str(canonical_id)],
    )
    logger.info(
        "admin.pricing_sync_canonical.dispatched",
        canonical_id=str(canonical_id),
        task_id=str(task.id),
    )
    return {
        "canonical_product_id": str(canonical_id),
        "task_id":              str(task.id),
        "status":               "dispatched",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Sprint 9: Multi-Channel Commerce Engine
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/channels",
    tags=["channels"],
    summary="List all sales channels",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def list_channels(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return all rows from sales_channels."""
    from sqlalchemy import select
    from app.models.sales_channel import SalesChannel

    result = await db.execute(select(SalesChannel).order_by(SalesChannel.name))
    channels = result.scalars().all()
    return {
        "channels": [
            {
                "id":         str(c.id),
                "name":       c.name,
                "type":       c.type,
                "enabled":    c.enabled,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in channels
        ]
    }


@router.get(
    "/channels/products/{canonical_id}",
    tags=["channels"],
    summary="Get channel listings for a canonical product",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def get_channel_products(
    canonical_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return channel_products rows for a given canonical_product_id."""
    from sqlalchemy import select
    from app.models.sales_channel import ChannelProduct

    result = await db.execute(
        select(ChannelProduct)
        .where(ChannelProduct.canonical_product_id == canonical_id)
        .order_by(ChannelProduct.channel)
    )
    rows = result.scalars().all()
    return {
        "canonical_product_id": str(canonical_id),
        "channel_products": [
            {
                "id":                   str(r.id),
                "channel":              r.channel,
                "external_product_id":  r.external_product_id,
                "external_variant_id":  r.external_variant_id,
                "price":                str(r.price) if r.price else None,
                "currency":             r.currency,
                "status":               r.status,
                "updated_at":           r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }


@router.post(
    "/channels/publish/{canonical_id}",
    tags=["channels"],
    summary="Publish a canonical product to all channels",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def publish_product_to_channels(
    canonical_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Dispatch publish_new_products task for a single canonical product, or
    call publish_product_to_channels inline and return results.
    """
    from sqlalchemy import select
    from app.models.canonical_product import CanonicalProduct
    from app.services.channel_router import publish_product_to_channels as _publish

    result = await db.execute(
        select(CanonicalProduct).where(CanonicalProduct.id == canonical_id)
    )
    cp = result.scalar_one_or_none()
    if cp is None:
        raise HTTPException(status_code=404, detail=f"CanonicalProduct {canonical_id} not found")

    price = float(cp.last_price) if cp.last_price else None
    results = await _publish(cp, price=price)

    logger.info(
        "admin.channels.publish.done",
        canonical_product_id=str(canonical_id),
        results=results,
    )
    return {
        "canonical_product_id": str(canonical_id),
        "results":              results,
    }


@router.post(
    "/channels/sync-prices",
    tags=["channels"],
    summary="Trigger price sync across all channels",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def trigger_channel_price_sync() -> dict[str, Any]:
    """Dispatch sync_prices_channels Celery task."""
    task = celery_app.send_task("workers.tasks_channels.sync_prices_channels")
    logger.info("admin.channels.sync_prices.dispatched", task_id=str(task.id))
    return {"task_id": str(task.id), "status": "dispatched"}


@router.post(
    "/channels/sync-inventory",
    tags=["channels"],
    summary="Trigger inventory sync across all channels",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def trigger_channel_inventory_sync() -> dict[str, Any]:
    """Dispatch sync_inventory_channels Celery task."""
    task = celery_app.send_task("workers.tasks_channels.sync_inventory_channels")
    logger.info("admin.channels.sync_inventory.dispatched", task_id=str(task.id))
    return {"task_id": str(task.id), "status": "dispatched"}


@router.get(
    "/channels/orders",
    tags=["channels"],
    summary="List channel orders",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def list_legacy_channel_orders(
    channel: str | None = Query(None, description="Filter by channel slug"),
    status: str | None  = Query(None, description="Filter by order status"),
    limit:  int         = Query(50, ge=1, le=200),
    db: AsyncSession    = Depends(get_db),
) -> dict[str, Any]:
    """Return channel_orders with optional filters."""
    from sqlalchemy import select
    from app.models.sales_channel import ChannelOrder

    q = select(ChannelOrder).order_by(ChannelOrder.created_at.desc()).limit(limit)
    if channel:
        q = q.where(ChannelOrder.channel == channel)
    if status:
        q = q.where(ChannelOrder.status == status)

    result = await db.execute(q)
    orders = result.scalars().all()
    return {
        "orders": [
            {
                "id":                   str(o.id),
                "channel":              o.channel,
                "external_order_id":    o.external_order_id,
                "canonical_product_id": str(o.canonical_product_id) if o.canonical_product_id else None,
                "quantity":             o.quantity,
                "price":                str(o.price) if o.price else None,
                "currency":             o.currency,
                "status":               o.status,
                "created_at":           o.created_at.isoformat() if o.created_at else None,
            }
            for o in orders
        ],
        "total": len(orders),
    }


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


# ═══════════════════════════════════════════════════════════════════════════════
# Sprint 10: Webhook Ingress + Multi-Channel E2E
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/webhook-events",
    tags=["sprint10"],
    summary="List webhook events (idempotency log)",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def list_webhook_events(
    limit:   int = Query(50, ge=1, le=500),
    channel: str | None = Query(None),
    topic:   str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from sqlalchemy import select, desc
    from app.models.webhook_event import WebhookEvent

    q = select(WebhookEvent).order_by(desc(WebhookEvent.received_at)).limit(limit)
    if channel:
        q = q.where(WebhookEvent.channel == channel)
    if topic:
        q = q.where(WebhookEvent.topic == topic)
    if status_filter:
        q = q.where(WebhookEvent.status == status_filter)

    rows = (await db.execute(q)).scalars().all()
    return {
        "total": len(rows),
        "items": [
            {
                "id":          str(r.id),
                "event_id":    r.event_id,
                "channel":     r.channel,
                "topic":       r.topic,
                "external_id": r.external_id,
                "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
                "received_at": r.received_at.isoformat() if r.received_at else None,
                "status":      r.status,
                "error":       r.error,
            }
            for r in rows
        ],
    }


@router.get(
    "/channel-orders",
    tags=["sprint10"],
    summary="List canonical channel orders (from webhook ingress)",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def list_channel_orders(
    limit:   int = Query(50, ge=1, le=500),
    channel: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from sqlalchemy import select, desc
    from app.models.channel_order import ChannelOrderV2

    q = select(ChannelOrderV2).order_by(desc(ChannelOrderV2.created_at)).limit(limit)
    if channel:
        q = q.where(ChannelOrderV2.channel == channel)

    rows = (await db.execute(q)).scalars().all()
    return {
        "total": len(rows),
        "items": [
            {
                "id":                str(r.id),
                "external_order_id": r.external_order_id,
                "channel":           r.channel,
                "currency":          r.currency,
                "total_price":       float(r.total_price) if r.total_price else None,
                "buyer_name":        r.buyer_name,
                "buyer_email":       r.buyer_email,
                "status":            r.status,
                "created_at":        r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.get(
    "/channel-products",
    tags=["sprint10"],
    summary="List canonical products (from webhook ingress)",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def list_channel_products(
    limit:   int = Query(50, ge=1, le=500),
    channel: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from sqlalchemy import select, desc
    from app.models.canonical_product import CanonicalProduct

    q = select(CanonicalProduct).order_by(desc(CanonicalProduct.created_at)).limit(limit)
    # filter by channel-derived SKU prefix
    if channel:
        q = q.where(CanonicalProduct.canonical_sku.like(f"{channel}-%"))

    rows = (await db.execute(q)).scalars().all()
    return {
        "total": len(rows),
        "items": [
            {
                "id":            str(r.id),
                "canonical_sku": r.canonical_sku,
                "name":          r.name,
                "brand":         r.brand,
                "last_price":    float(r.last_price) if r.last_price else None,
                "created_at":    r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


# ═════════════════════════════════════════════════════════════════════════════
# Sprint 12 – Auto-publish pipeline endpoints
# ═════════════════════════════════════════════════════════════════════════════

@router.get(
    "/publish/preview",
    tags=["sprint12"],
    summary="Preview top-N products selected for Shopify publish (no side effects)",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def publish_preview(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the candidates that *would* be published, with computed prices."""
    from app.services.publish_service import preview_top_products

    items = await preview_top_products(db, limit=limit)
    return {"total": len(items), "items": items}


@router.post(
    "/publish/shopify",
    tags=["sprint12"],
    summary="Trigger Shopify publish job (dry_run=1 for safe simulation)",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def trigger_publish_shopify(
    limit:   int  = Query(20, ge=1, le=100),
    dry_run: bool = Query(True, description="Set to false for live publish"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Enqueue a publish job via Celery.
    Returns immediately with job_id; poll GET /admin/publish/jobs/{job_id}.

    For small runs (limit ≤ 5), also runs synchronously and returns full result.
    """
    from app.workers.tasks_publish import publish_shopify_top_products  # noqa: PLC0415

    # Enqueue async Celery task
    task = publish_shopify_top_products.apply_async(
        kwargs={"limit": limit, "dry_run": dry_run},
    )

    return {
        "message":   "publish job enqueued",
        "task_id":   task.id,
        "dry_run":   dry_run,
        "limit":     limit,
        "note":      "poll GET /admin/publish/jobs for status",
    }


@router.get(
    "/publish/jobs",
    tags=["sprint12"],
    summary="List recent publish jobs",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def list_publish_jobs(
    limit:   int         = Query(50, ge=1, le=200),
    channel: str | None  = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from sqlalchemy import select, desc
    from app.models.publish_job import PublishJob

    q = select(PublishJob).order_by(desc(PublishJob.created_at)).limit(limit)
    if channel:
        q = q.where(PublishJob.channel == channel)

    rows = (await db.execute(q)).scalars().all()
    return {
        "total": len(rows),
        "items": [
            {
                "id":              str(j.id),
                "channel":         j.channel,
                "status":          j.status,
                "dry_run":         j.dry_run,
                "target_count":    j.target_count,
                "published_count": j.published_count,
                "failed_count":    j.failed_count,
                "skipped_count":   j.skipped_count,
                "notes":           j.notes,
                "created_at":      j.created_at.isoformat() if j.created_at else None,
                "updated_at":      j.updated_at.isoformat() if j.updated_at else None,
            }
            for j in rows
        ],
    }


@router.get(
    "/publish/jobs/{job_id}",
    tags=["sprint12"],
    summary="Get a publish job with its items list",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def get_publish_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from sqlalchemy import select
    from app.models.publish_job import PublishJob, PublishJobItem

    job_res = await db.execute(
        select(PublishJob).where(PublishJob.id == job_id)
    )
    job = job_res.scalar_one_or_none()
    if job is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="publish job not found")

    items_res = await db.execute(
        select(PublishJobItem).where(PublishJobItem.publish_job_id == job.id)
    )
    items = items_res.scalars().all()

    return {
        "id":              str(job.id),
        "channel":         job.channel,
        "status":          job.status,
        "dry_run":         job.dry_run,
        "target_count":    job.target_count,
        "published_count": job.published_count,
        "failed_count":    job.failed_count,
        "skipped_count":   job.skipped_count,
        "notes":           job.notes,
        "created_at":      job.created_at.isoformat() if job.created_at else None,
        "updated_at":      job.updated_at.isoformat() if job.updated_at else None,
        "items": [
            {
                "id":                   str(i.id),
                "canonical_product_id": str(i.canonical_product_id),
                "shopify_product_id":   i.shopify_product_id,
                "status":               i.status,
                "reason":               i.reason,
                "created_at":           i.created_at.isoformat() if i.created_at else None,
                "updated_at":           i.updated_at.isoformat() if i.updated_at else None,
            }
            for i in items
        ],
    }


# ═════════════════════════════════════════════════════════════════════════════
# Sprint 13 – Market Price Intelligence + Auto Repricing endpoints
# ═════════════════════════════════════════════════════════════════════════════

# ── Market Prices ─────────────────────────────────────────────────────────────

@router.post(
    "/market-prices",
    tags=["sprint13"],
    summary="Manually add/update a competitor price for a canonical product",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def create_market_price(
    body: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Body JSON:
      canonical_product_id: str (UUID)
      source: str  (e.g. 'competitor_manual', 'amazon')
      price: float
      currency: str (default 'USD')
      in_stock: bool (default true)
      external_url: str | null
      external_sku: str | null
    """
    from app.services.market_price_service import upsert_market_price

    cp_id = body.get("canonical_product_id")
    if not cp_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="canonical_product_id is required")

    mp = await upsert_market_price(
        db,
        canonical_product_id = cp_id,
        source_name  = body.get("source", "competitor_manual"),
        price        = body["price"],
        currency     = body.get("currency", "USD"),
        in_stock     = body.get("in_stock", True),
        external_url = body.get("external_url"),
        external_sku = body.get("external_sku"),
    )
    return {
        "id":                   str(mp.id),
        "canonical_product_id": str(mp.canonical_product_id),
        "source_id":            str(mp.source_id),
        "price":                float(mp.price),
        "currency":             mp.currency,
        "in_stock":             mp.in_stock,
    }


@router.post(
    "/market-prices/import",
    tags=["sprint13"],
    summary="Bulk import competitor prices via CSV upload",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def import_market_prices_csv(
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """
    Upload a CSV file with columns:
        canonical_sku, source, price, currency[, in_stock, external_url, external_sku]

    Returns { imported, skipped, errors }.
    """
    from app.services.market_price_service import parse_market_price_csv, upsert_market_price
    from app.models.canonical_product import CanonicalProduct
    from sqlalchemy import select

    # Re-read file as bytes and decode
    content = await file.read()
    text    = content.decode("utf-8-sig")  # handles BOM

    records, errors = parse_market_price_csv(text)

    imported = 0
    import_errors: list[str] = list(errors)

    for rec in records:
        # Resolve canonical_sku → canonical_product_id
        cp_res = await db.execute(
            select(CanonicalProduct).where(
                CanonicalProduct.canonical_sku == rec["canonical_sku"]
            )
        )
        cp = cp_res.scalar_one_or_none()
        if cp is None:
            import_errors.append(f"SKU not found: {rec['canonical_sku']}")
            continue

        try:
            await upsert_market_price(
                db,
                canonical_product_id = cp.id,
                source_name  = rec["source"],
                price        = rec["price"],
                currency     = rec["currency"],
                in_stock     = rec["in_stock"],
                external_url = rec["external_url"],
                external_sku = rec["external_sku"],
            )
            imported += 1
        except Exception as exc:  # noqa: BLE001
            import_errors.append(f"SKU {rec['canonical_sku']}: {exc}")

    return {
        "imported": imported,
        "skipped":  len(import_errors),
        "errors":   import_errors[:50],
    }


@router.get(
    "/market-prices/{canonical_product_id}",
    tags=["sprint13"],
    summary="Get all competitor prices for a canonical product",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def get_market_prices_for_product(
    canonical_product_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from app.services.market_price_service import get_market_prices, get_competitor_band
    import uuid as _uuid

    cp_uuid = _uuid.UUID(canonical_product_id)
    prices  = await get_market_prices(db, cp_uuid)
    band    = await get_competitor_band(db, cp_uuid)

    return {
        "canonical_product_id": canonical_product_id,
        "prices": prices,
        "competitor_band": {
            "min_price":    float(band.min_price)    if band else None,
            "median_price": float(band.median_price) if band else None,
            "max_price":    float(band.max_price)    if band else None,
            "sample_count": band.sample_count        if band else 0,
        },
    }


# ── Repricing ─────────────────────────────────────────────────────────────────

@router.get(
    "/repricing/preview",
    tags=["sprint13"],
    summary="Preview repricing: recommended vs current vs competitor (no writes)",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def repricing_preview(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from app.services.repricing_service import preview_reprice

    items = await preview_reprice(db, limit=limit)
    return {"total": len(items), "items": items}


@router.post(
    "/repricing/apply",
    tags=["sprint13"],
    summary="Trigger repricing run via Celery (dry_run=true for safe simulation)",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def repricing_apply(
    limit:   int  = Query(50, ge=1, le=200),
    dry_run: bool = Query(True, description="Set to false for live repricing"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from app.workers.tasks_repricing import run_repricing  # noqa: PLC0415

    task = run_repricing.apply_async(
        kwargs={"limit": limit, "dry_run": dry_run},
    )
    return {
        "message": "repricing job enqueued",
        "task_id": task.id,
        "dry_run": dry_run,
        "limit":   limit,
        "note":    "poll GET /admin/repricing/runs for status",
    }


@router.get(
    "/repricing/runs",
    tags=["sprint13"],
    summary="List recent repricing runs",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def list_repricing_runs(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from sqlalchemy import select, desc
    from app.models.market_price import RepricingRun

    q    = select(RepricingRun).order_by(desc(RepricingRun.created_at)).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    return {
        "total": len(rows),
        "items": [
            {
                "id":            str(r.id),
                "channel":       r.channel,
                "status":        r.status,
                "dry_run":       r.dry_run,
                "target_count":  r.target_count,
                "updated_count": r.updated_count,
                "skipped_count": r.skipped_count,
                "failed_count":  r.failed_count,
                "notes":         r.notes,
                "created_at":    r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.get(
    "/repricing/runs/{run_id}",
    tags=["sprint13"],
    summary="Get repricing run detail with per-product items",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def get_repricing_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from sqlalchemy import select
    from app.models.market_price import RepricingRun, RepricingRunItem

    run_res = await db.execute(
        select(RepricingRun).where(RepricingRun.id == run_id)
    )
    run = run_res.scalar_one_or_none()
    if run is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="repricing run not found")

    items_res = await db.execute(
        select(RepricingRunItem).where(
            RepricingRunItem.repricing_run_id == run.id
        )
    )
    items = items_res.scalars().all()

    return {
        "id":            str(run.id),
        "channel":       run.channel,
        "status":        run.status,
        "dry_run":       run.dry_run,
        "target_count":  run.target_count,
        "updated_count": run.updated_count,
        "skipped_count": run.skipped_count,
        "failed_count":  run.failed_count,
        "notes":         run.notes,
        "created_at":    run.created_at.isoformat() if run.created_at else None,
        "items": [
            {
                "id":                   str(i.id),
                "canonical_product_id": str(i.canonical_product_id),
                "old_price":            float(i.old_price) if i.old_price else None,
                "recommended_price":    float(i.recommended_price) if i.recommended_price else None,
                "applied_price":        float(i.applied_price) if i.applied_price else None,
                "status":               i.status,
                "reason":               i.reason,
                "updated_at":           i.updated_at.isoformat() if i.updated_at else None,
            }
            for i in items
        ],
    }


# ═════════════════════════════════════════════════════════════════════════════
# Sprint 14 – Supplier Order / Auto-Fulfillment Admin API
# ═════════════════════════════════════════════════════════════════════════════

@router.get(
    "/supplier-orders",
    tags=["sprint14"],
    summary="List supplier orders with optional status filter",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def list_supplier_orders(
    status: str | None = Query(None, description="Filter by status (placed, shipped, failed, ...)"),
    supplier: str | None = Query(None, description="Filter by supplier name"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from sqlalchemy import select, desc
    from app.models.supplier_order import SupplierOrder

    q = select(SupplierOrder).order_by(desc(SupplierOrder.created_at)).limit(limit)
    if status:
        q = q.where(SupplierOrder.status == status)
    if supplier:
        q = q.where(SupplierOrder.supplier == supplier.upper())

    rows = (await db.execute(q)).scalars().all()
    return {
        "total": len(rows),
        "items": [
            {
                "id":                str(r.id),
                "channel_order_id":  str(r.channel_order_id),
                "supplier":          r.supplier,
                "supplier_order_id": r.supplier_order_id,
                "supplier_status":   r.supplier_status,
                "tracking_number":   r.tracking_number,
                "tracking_carrier":  r.tracking_carrier,
                "cost":              float(r.cost) if r.cost else None,
                "currency":          r.currency,
                "status":            r.status,
                "failure_reason":    r.failure_reason,
                "retry_count":       r.retry_count,
                "created_at":        r.created_at.isoformat() if r.created_at else None,
                "updated_at":        r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }


@router.get(
    "/supplier-orders/{supplier_order_id}",
    tags=["sprint14"],
    summary="Get supplier order detail",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def get_supplier_order(
    supplier_order_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from sqlalchemy import select
    from app.models.supplier_order import SupplierOrder
    from fastapi import HTTPException

    res = await db.execute(
        select(SupplierOrder).where(SupplierOrder.id == supplier_order_id)
    )
    so = res.scalar_one_or_none()
    if so is None:
        raise HTTPException(status_code=404, detail="supplier order not found")

    return {
        "id":                str(so.id),
        "channel_order_id":  str(so.channel_order_id),
        "supplier":          so.supplier,
        "supplier_order_id": so.supplier_order_id,
        "supplier_status":   so.supplier_status,
        "tracking_number":   so.tracking_number,
        "tracking_carrier":  so.tracking_carrier,
        "cost":              float(so.cost) if so.cost else None,
        "currency":          so.currency,
        "status":            so.status,
        "failure_reason":    so.failure_reason,
        "retry_count":       so.retry_count,
        "created_at":        so.created_at.isoformat() if so.created_at else None,
        "updated_at":        so.updated_at.isoformat() if so.updated_at else None,
    }


@router.post(
    "/supplier-orders/trigger/{channel_order_id}",
    tags=["sprint14"],
    summary="Manually trigger fulfillment for a channel order",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def trigger_fulfillment(
    channel_order_id: str,
    dry_run: bool = Query(False, description="Dry run — do not call supplier API"),
) -> dict[str, Any]:
    from app.workers.tasks_fulfillment import process_order_fulfillment
    task = process_order_fulfillment.apply_async(
        kwargs={"channel_order_id": channel_order_id, "dry_run": dry_run}
    )
    return {
        "message":          "fulfillment task enqueued",
        "task_id":          task.id,
        "channel_order_id": channel_order_id,
        "dry_run":          dry_run,
        "note":             "poll GET /admin/supplier-orders?status=placed for result",
    }


# ═════════════════════════════════════════════════════════════════════════════
# Sprint 15 – AI Product Discovery Admin API
# ═════════════════════════════════════════════════════════════════════════════

@router.get(
    "/discovery/trends",
    tags=["sprint15"],
    summary="List recent trend signals (TikTok / Amazon)",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def list_discovery_trends(
    source: str | None = Query(None, description="Filter by source: tiktok | amazon_bestsellers"),
    limit:  int        = Query(50, ge=1, le=200),
    db: AsyncSession   = Depends(get_db),
) -> dict[str, Any]:
    from sqlalchemy import select, desc
    from app.models.trend_product import TrendProduct

    q = select(TrendProduct).order_by(desc(TrendProduct.trend_score)).limit(limit)
    if source:
        q = q.where(TrendProduct.source == source)

    rows = (await db.execute(q)).scalars().all()
    return {
        "total": len(rows),
        "items": [
            {
                "id":          str(r.id),
                "source":      r.source,
                "external_id": r.external_id,
                "name":        r.name,
                "brand":       r.brand,
                "category":    r.category,
                "trend_score": float(r.trend_score),
                "collected_at":r.collected_at.isoformat() if r.collected_at else None,
            }
            for r in rows
        ],
    }


@router.get(
    "/discovery/candidates",
    tags=["sprint15"],
    summary="List top product candidates from the discovery pipeline",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def list_discovery_candidates(
    status: str | None = Query(None, description="Filter by status: candidate | published | rejected"),
    limit:  int        = Query(50, ge=1, le=200),
    db: AsyncSession   = Depends(get_db),
) -> dict[str, Any]:
    from app.services.discovery_service import get_top_candidates
    from app.models.product_candidate import CandidateStatus

    _status = status or CandidateStatus.CANDIDATE
    items = await get_top_candidates(db, limit=limit, status=_status)
    return {"total": len(items), "items": items}


@router.post(
    "/discovery/run",
    tags=["sprint15"],
    summary="Trigger AI product discovery pipeline (dry_run=true is safe)",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def run_discovery(
    dry_run: bool      = Query(True,  description="Dry run – collect & score only, no DB writes"),
    top_n:   int       = Query(50,    ge=1, le=200, description="Max candidates to keep"),
    async_:  bool      = Query(False, alias="async", description="Run via Celery (background)"),
    db: AsyncSession   = Depends(get_db),
) -> dict[str, Any]:
    if async_:
        from app.workers.tasks_discovery import run_discovery_pipeline
        task = run_discovery_pipeline.apply_async(
            kwargs={"dry_run": dry_run, "top_n": top_n}
        )
        return {
            "message":  "discovery pipeline enqueued",
            "task_id":  task.id,
            "dry_run":  dry_run,
            "top_n":    top_n,
        }

    # Synchronous (in-process) run – good for interactive API exploration
    from app.services.discovery_service import run_product_discovery
    result = await run_product_discovery(db, dry_run=dry_run, top_n=top_n)
    if not dry_run:
        await db.commit()
    return {
        "status":              "ok",
        "dry_run":             dry_run,
        "signals_collected":   result.signals_collected,
        "signals_matched":     result.signals_matched,
        "candidates_created":  result.candidates_created,
        "candidates_updated":  result.candidates_updated,
        "candidates_rejected": result.candidates_rejected,
        "top_count":           len(result.top_candidates),
        "top_candidates":      result.top_candidates[:10],  # First 10 in response
        "errors":              result.errors,
    }


@router.post(
    "/discovery/publish/{candidate_id}",
    tags=["sprint15"],
    summary="Publish a specific discovery candidate to Shopify",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def publish_discovery_candidate(
    candidate_id: str,
    dry_run: bool     = Query(True, description="Dry run – don't call Shopify"),
    db: AsyncSession  = Depends(get_db),
) -> dict[str, Any]:
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.models.product_candidate import ProductCandidate, CandidateStatus
    from app.models.canonical_product import CanonicalProduct

    # Fetch candidate
    candidate_row = (await db.execute(
        select(ProductCandidate).where(ProductCandidate.id == candidate_id).limit(1)
    )).scalar_one_or_none()
    if candidate_row is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    if candidate_row.status != CandidateStatus.CANDIDATE:
        raise HTTPException(
            status_code=409,
            detail=f"candidate already in status={candidate_row.status!r}",
        )

    # Verify canonical product exists
    cp = (await db.execute(
        select(CanonicalProduct).where(
            CanonicalProduct.id == candidate_row.canonical_product_id
        ).limit(1)
    )).scalar_one_or_none()
    if cp is None:
        raise HTTPException(status_code=404, detail="canonical product not found")

    # Trigger publish via publish_service (limit=1, this specific product)
    from app.services.publish_service import publish_top_products_to_shopify
    result = await publish_top_products_to_shopify(
        session=db,
        limit=1,
        dry_run=dry_run,
    )

    # Update candidate status
    if not dry_run:
        candidate_row.status = CandidateStatus.PUBLISHED  # type: ignore[assignment]
        candidate_row.notes  = f"published via admin API job_id={result.job_id}"  # type: ignore[assignment]
        db.add(candidate_row)
        await db.commit()

    return {
        "candidate_id":        candidate_id,
        "canonical_product_id":str(candidate_row.canonical_product_id),
        "canonical_sku":       cp.canonical_sku,
        "name":                cp.name,
        "dry_run":             dry_run,
        "publish_job_id":      result.job_id,
        "publish_status":      result.status,
        "published_count":     result.published_count,
        "candidate_status":    candidate_row.status if not dry_run else "candidate",
    }


# ═════════════════════════════════════════════════════════════════════════════
# Sprint 16 – Operational Observability Admin API
# ═════════════════════════════════════════════════════════════════════════════

@router.get(
    "/ops/kpis",
    tags=["sprint16"],
    summary="Collect and return current operational KPI snapshot",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def get_ops_kpis(
    window_minutes: int = Query(60, ge=5, le=1440, description="Lookback window in minutes"),
    db: AsyncSession    = Depends(get_db),
) -> dict[str, Any]:
    from app.services.metrics_service import collect_kpis
    snapshot = await collect_kpis(db, window_minutes=window_minutes)
    return snapshot.to_dict()


@router.get(
    "/ops/alerts",
    tags=["sprint16"],
    summary="List open and acknowledged alert events",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def list_ops_alerts(
    status:   str | None = Query(None,  description="Filter: open|acknowledged|resolved"),
    severity: str | None = Query(None,  description="Filter: info|warning|critical"),
    limit:    int        = Query(50, ge=1, le=200),
    db: AsyncSession     = Depends(get_db),
) -> dict[str, Any]:
    from app.services.alert_service import list_all_alerts, list_open_alerts
    if status:
        items = await list_all_alerts(db, limit=limit, status=status)
    else:
        items = await list_open_alerts(db, limit=limit, severity=severity)
    return {"total": len(items), "items": items}


@router.post(
    "/ops/alerts",
    tags=["sprint16"],
    summary="Create a new alert rule",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def create_ops_alert_rule(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Create a custom alert rule.
    Body JSON: {name, metric, operator, threshold, window_minutes?, severity?, notes?}
    """
    from fastapi import Request
    from app.services.alert_service import create_alert_rule
    # Parse JSON body manually to avoid Pydantic model dependency
    import inspect
    frame = inspect.currentframe()
    raise NotImplementedError("Use POST /ops/alert-rules with JSON body")


@router.post(
    "/ops/alert-rules",
    tags=["sprint16"],
    summary="Create a custom alert rule",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def create_ops_alert_rule_v2(
    name:           str   = Query(...,       description="Unique rule name"),
    metric:         str   = Query(...,       description="KPI metric key"),
    operator:       str   = Query(...,       description="Operator: >, >=, <, <=, =="),
    threshold:      float = Query(...,       description="Numeric threshold"),
    window_minutes: int   = Query(60,        description="Evaluation window in minutes"),
    severity:       str   = Query("warning", description="info|warning|critical"),
    notes:          str | None = Query(None, description="Optional description"),
    db: AsyncSession      = Depends(get_db),
) -> dict[str, Any]:
    from app.services.alert_service import create_alert_rule
    rule = await create_alert_rule(
        db,
        name=name,
        metric=metric,
        operator=operator,
        threshold=threshold,
        window_minutes=window_minutes,
        severity=severity,
        notes=notes,
    )
    await db.commit()
    return {
        "id":       str(rule.id),
        "name":     rule.name,
        "metric":   rule.metric,
        "operator": rule.operator,
        "threshold":float(rule.threshold),
        "severity": rule.severity,
        "enabled":  rule.enabled,
        "notes":    rule.notes,
    }


@router.get(
    "/ops/errors",
    tags=["sprint16"],
    summary="Get recent operational error events (fulfillment + repricing failures)",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def get_ops_errors(
    window_minutes: int = Query(360, ge=5, le=10080, description="Lookback window (minutes)"),
    limit:          int = Query(20,  ge=1, le=100),
    db: AsyncSession    = Depends(get_db),
) -> dict[str, Any]:
    from app.services.metrics_service import collect_kpis
    snapshot = await collect_kpis(db, window_minutes=window_minutes)
    errors = snapshot.recent_errors[:limit]
    return {
        "window_minutes": window_minutes,
        "total":          len(errors),
        "errors":         errors,
    }


@router.post(
    "/ops/alerts/{alert_event_id}/acknowledge",
    tags=["sprint16"],
    summary="Acknowledge an open alert event",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def acknowledge_ops_alert(
    alert_event_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from fastapi import HTTPException
    from app.services.alert_service import acknowledge_alert
    event = await acknowledge_alert(db, alert_event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="alert event not found")
    await db.commit()
    return {"id": str(event.id), "status": event.status, "rule_name": event.rule_name}


@router.post(
    "/ops/alerts/{alert_event_id}/resolve",
    tags=["sprint16"],
    summary="Resolve an alert event",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def resolve_ops_alert(
    alert_event_id: str,
    notes:          str | None = Query(None, description="Optional resolution notes"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from fastapi import HTTPException
    from app.services.alert_service import resolve_alert
    event = await resolve_alert(db, alert_event_id, notes=notes)
    if event is None:
        raise HTTPException(status_code=404, detail="alert event not found")
    await db.commit()
    return {
        "id":          str(event.id),
        "status":      event.status,
        "rule_name":   event.rule_name,
        "resolved_at": event.resolved_at.isoformat() if event.resolved_at else None,
    }


# =============================================================================
# Sprint 17 – AI Discovery Engine v2 endpoints
# =============================================================================

@router.get(
    "/discovery/v2/candidates",
    tags=["sprint17"],
    summary="List Sprint 17 discovery candidates",
    dependencies=[Depends(require_role("VIEWER"))],
)
async def list_discovery_v2_candidates(
    limit:  int         = Query(50,          ge=1, le=200),
    status: str | None  = Query("candidate", description="candidate|published|rejected|all"),
    db: AsyncSession    = Depends(get_db),
) -> dict[str, Any]:
    """
    Return Sprint 17 AI Discovery Engine v2 product candidates,
    sorted by score DESC.
    """
    from app.services.discovery_service_v2 import get_top_candidates, CandidateStatusV2
    from app.models.product_candidate_v2 import ProductCandidateV2

    if status and status.lower() == "all":
        from sqlalchemy import select, desc
        rows = (await db.execute(
            select(ProductCandidateV2)
            .order_by(desc(ProductCandidateV2.score))
            .limit(limit)
        )).scalars().all()
    else:
        effective_status = status or CandidateStatusV2.CANDIDATE
        rows = await get_top_candidates(db, limit=limit, status=effective_status)

    items = []
    for c in rows:
        item = c.to_dict()
        # Optionally enrich with canonical product name
        try:
            from app.models.canonical_product import CanonicalProduct
            cp = (await db.execute(
                select(CanonicalProduct).where(
                    CanonicalProduct.id == c.canonical_product_id
                ).limit(1)
            )).scalar_one_or_none()
            if cp:
                item["canonical_sku"]  = cp.canonical_sku
                item["name"]           = cp.name
                item["brand"]          = cp.brand
                item["last_price"]     = float(cp.last_price) if cp.last_price else None
        except Exception:
            pass
        items.append(item)

    return {"total": len(items), "items": items}


@router.post(
    "/discovery/v2/run",
    tags=["sprint17"],
    summary="Trigger Sprint 17 discovery run",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def run_discovery_v2(
    limit:   int  = Query(20,   ge=1,  le=200),
    dry_run: bool = Query(True, description="True = score only, False = publish to Shopify"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Trigger the Sprint 17 AI Discovery Engine v2 pipeline.
    Scores all canonical products and optionally publishes top-N to Shopify.
    dry_run=True by default for safety.
    """
    from app.services.discovery_service_v2 import run_discovery_v2 as _run_v2
    result = await _run_v2(db, limit=200, top_n=limit, dry_run=dry_run)
    if not dry_run:
        await db.commit()
    return result


@router.post(
    "/discovery/v2/candidates/{candidate_id}/reject",
    tags=["sprint17"],
    summary="Reject a Sprint 17 discovery candidate",
    dependencies=[Depends(require_role("OPERATOR"))],
)
async def reject_discovery_v2_candidate(
    candidate_id: str,
    reason: str | None = Query(None, description="Optional rejection reason"),
    db: AsyncSession   = Depends(get_db),
) -> dict[str, Any]:
    """
    Mark a Sprint 17 discovery candidate as rejected.
    The candidate will no longer appear in GET /discovery/v2/candidates.
    """
    from fastapi import HTTPException
    from app.services.discovery_service_v2 import reject_candidate

    candidate = await reject_candidate(db, candidate_id, reason=reason)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    await db.commit()
    return {
        "id":     str(candidate.id),
        "status": candidate.status,
        "notes":  candidate.notes,
    }
