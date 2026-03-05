from __future__ import annotations

import uuid
from enum import Enum as PyEnum

from sqlalchemy import Column, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class TicketStatus(str, PyEnum):
    OPEN   = "OPEN"
    CLOSED = "CLOSED"


class TicketType(str, PyEnum):
    TRACKING_ISSUE   = "TRACKING_ISSUE"
    PAYMENT_DISPUTE  = "PAYMENT_DISPUTE"
    SUPPLIER_ERROR   = "SUPPLIER_ERROR"
    REFUND_REQUEST   = "REFUND_REQUEST"
    OTHER            = "OTHER"


class Ticket(Base):
    """Support/ops ticket linked to an order."""

    __tablename__ = "tickets"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    order_id    = Column(UUID(as_uuid=True), nullable=True, index=True)
    type        = Column(String(64),  nullable=False, default=TicketType.OTHER)
    status      = Column(String(16),  nullable=False, default=TicketStatus.OPEN, index=True)
    subject     = Column(String(256), nullable=True)
    payload     = Column(JSON,        nullable=True)
    note        = Column(Text,        nullable=True)
    created_by  = Column(String(255), nullable=True)   # admin email
    closed_at   = Column(DateTime(timezone=True), nullable=True)

    created_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at  = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Ticket id={self.id} type={self.type} status={self.status}>"
