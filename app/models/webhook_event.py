"""
app/models/webhook_event.py
────────────────────────────
Sprint 10 – ORM model for webhook_events table.
"""
from __future__ import annotations

import uuid
from sqlalchemy import Column, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id    = Column(String(128), unique=True, nullable=False, index=True)
    channel     = Column(String(32),  nullable=False, index=True)
    topic       = Column(String(64),  nullable=False)
    external_id = Column(String(128), nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status      = Column(String(16),  nullable=False, default="received")  # received|processed|failed
    error       = Column(Text,        nullable=True)
    payload_json = Column(JSONB,      nullable=False, default=dict)

    def __repr__(self) -> str:
        return (
            f"<WebhookEvent event_id={self.event_id!r} "
            f"channel={self.channel} topic={self.topic} status={self.status}>"
        )
