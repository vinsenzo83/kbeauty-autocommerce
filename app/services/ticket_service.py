from __future__ import annotations

"""
app/services/ticket_service.py
───────────────────────────────
CRUD helpers for the Ticket model (Sprint 5).
"""

from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket import Ticket, TicketStatus, TicketType

logger = structlog.get_logger(__name__)


async def create_ticket(
    session: AsyncSession,
    *,
    order_id: str | UUID | None = None,
    ticket_type: str = TicketType.OTHER,
    subject: str | None = None,
    payload: dict[str, Any] | None = None,
    note: str | None = None,
    created_by: str | None = None,
) -> Ticket:
    ticket = Ticket(
        order_id   = order_id,
        type       = ticket_type,
        subject    = subject,
        payload    = payload,
        note       = note,
        created_by = created_by,
        status     = TicketStatus.OPEN,
    )
    session.add(ticket)
    await session.flush()
    logger.info(
        "ticket_service.created",
        ticket_id=str(ticket.id),
        order_id=str(order_id) if order_id else None,
        type=ticket_type,
    )
    return ticket


async def close_ticket(session: AsyncSession, ticket_id: UUID | str) -> Ticket | None:
    result = await session.execute(
        select(Ticket).where(Ticket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        return None
    ticket.status    = TicketStatus.CLOSED
    ticket.closed_at = datetime.now(timezone.utc)
    await session.flush()
    logger.info("ticket_service.closed", ticket_id=str(ticket_id))
    return ticket


async def list_tickets(
    session: AsyncSession,
    *,
    status_filter: str | None = None,
    type_filter: str | None   = None,
    q: str | None             = None,
    page: int                 = 1,
    page_size: int            = 20,
) -> tuple[Sequence[Ticket], int]:
    """Return (tickets, total_count) with optional filters."""
    query = select(Ticket)

    if status_filter:
        query = query.where(Ticket.status == status_filter)
    if type_filter:
        query = query.where(Ticket.type == type_filter)
    if q:
        query = query.where(
            or_(
                Ticket.subject.ilike(f"%{q}%"),    # type: ignore[attr-defined]
                Ticket.note.ilike(f"%{q}%"),        # type: ignore[attr-defined]
            )
        )

    # Count
    from sqlalchemy import func
    cnt_q  = select(func.count()).select_from(query.subquery())
    cnt_r  = await session.execute(cnt_q)
    total  = cnt_r.scalar_one() or 0

    # Paginate
    offset = (page - 1) * page_size
    query  = query.order_by(Ticket.created_at.desc()).offset(offset).limit(page_size)  # type: ignore[attr-defined]
    result = await session.execute(query)
    return result.scalars().all(), total


async def get_ticket_by_id(session: AsyncSession, ticket_id: UUID | str) -> Ticket | None:
    result = await session.execute(
        select(Ticket).where(Ticket.id == ticket_id)
    )
    return result.scalar_one_or_none()
