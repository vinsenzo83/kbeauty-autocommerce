from __future__ import annotations

import uuid
from enum import Enum as PyEnum

from sqlalchemy import Column, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class AdminRole(str, PyEnum):
    ADMIN    = "ADMIN"
    OPERATOR = "OPERATOR"
    VIEWER   = "VIEWER"


class AdminUser(Base):
    """
    Admin users for the ops dashboard.

    For MVP the single built-in admin is configured via env vars
    (ADMIN_EMAIL / ADMIN_PASSWORD).  This table is created automatically
    and can hold additional users added later.
    """

    __tablename__ = "admin_users"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    email        = Column(String(255), unique=True, nullable=False, index=True)
    password_hash= Column(String(255), nullable=False)
    role         = Column(String(16),  nullable=False, default=AdminRole.VIEWER)
    is_active    = Column(String(1),   nullable=False, default="1")

    created_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at   = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<AdminUser id={self.id} email={self.email} role={self.role}>"
