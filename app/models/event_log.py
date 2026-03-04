from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class EventLog(Base):
    __tablename__ = "event_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    event_hash = Column(String(64), unique=True, nullable=False, index=True)
    source = Column(String(64), nullable=False)          # e.g. "shopify"
    event_type = Column(String(128), nullable=False)     # e.g. "order/created"
    payload_ref = Column(String(128), nullable=True)     # shopify_order_id or similar
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<EventLog id={self.id} source={self.source} "
            f"event_type={self.event_type} hash={self.event_hash}>"
        )
