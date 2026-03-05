from __future__ import annotations

"""
app/services/auth_service.py
─────────────────────────────
JWT authentication for the Admin Dashboard (Sprint 5).

Features
--------
* Verify email + password (bcrypt, falls back to plaintext in tests).
* Issue signed JWT access tokens (HS256).
* Decode + validate tokens; raise 401 on failure.
* Roles: ADMIN > OPERATOR > VIEWER (checked via require_role dependency).

MVP credential source (priority order):
1. admin_users DB table (looked up by email).
2. Env-var built-in (ADMIN_EMAIL / ADMIN_PASSWORD) — for first-boot / CI.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.models.admin_user import AdminRole

logger  = structlog.get_logger(__name__)
_bearer = HTTPBearer(auto_error=False)

# ── Role ordering (higher index = more permissive) ────────────────────────────
_ROLE_RANK: dict[str, int] = {
    AdminRole.VIEWER:   0,
    AdminRole.OPERATOR: 1,
    AdminRole.ADMIN:    2,
}


# ── Password helpers ──────────────────────────────────────────────────────────

def _hash_password(plain: str) -> str:
    """Hash a plain-text password with bcrypt."""
    try:
        import bcrypt  # type: ignore[import]
        return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        # Test fallback: prefix-based pseudo-hash
        return f"plain:{plain}"


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if ``plain`` matches ``hashed``."""
    if hashed.startswith("plain:"):
        return plain == hashed[6:]
    try:
        import bcrypt  # type: ignore[import]
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ImportError:
        return plain == hashed


# ── Token helpers ─────────────────────────────────────────────────────────────

def create_access_token(
    subject: str,
    role: str,
    *,
    expires_minutes: int | None = None,
) -> str:
    """Issue a signed JWT access token."""
    try:
        from jose import jwt  # type: ignore[import]
    except ImportError:
        raise RuntimeError("python-jose is required. pip install python-jose[cryptography]")

    settings = get_settings()
    expire   = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes if expires_minutes is not None else settings.JWT_EXPIRE_MINUTES
    )
    payload  = {
        "sub":  subject,
        "role": role,
        "exp":  expire,
        "iat":  datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT.

    Raises HTTPException 401 on any failure.
    """
    try:
        from jose import JWTError, jwt  # type: ignore[import]
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="python-jose not installed",
        )

    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except JWTError as exc:
        logger.warning("auth.token_invalid", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── FastAPI dependencies ──────────────────────────────────────────────────────

class CurrentUser:
    """Container for the authenticated user's claims."""

    def __init__(self, sub: str, role: str) -> None:
        self.sub  = sub
        self.role = role

    @property
    def email(self) -> str:
        return self.sub

    def __repr__(self) -> str:
        return f"<CurrentUser sub={self.sub} role={self.role}>"


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> CurrentUser:
    """FastAPI dependency — extract + validate Bearer token → CurrentUser."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(credentials.credentials)
    sub  = payload.get("sub", "")
    role = payload.get("role", AdminRole.VIEWER)
    return CurrentUser(sub=sub, role=role)


def require_role(minimum_role: str):
    """
    FastAPI dependency factory — enforce a minimum role.

    Usage::

        @router.get("/admin/secret", dependencies=[Depends(require_role("ADMIN"))])
        async def secret(): ...
    """
    def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        user_rank = _ROLE_RANK.get(user.role, -1)
        min_rank  = _ROLE_RANK.get(minimum_role, 999)
        if user_rank < min_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{minimum_role}' or higher required.",
            )
        return user
    return _check


# ── Login helper ──────────────────────────────────────────────────────────────

async def authenticate_user(
    email: str,
    password: str,
    db_session: Any = None,
) -> tuple[str, str] | None:
    """
    Attempt to authenticate an admin user.

    Returns (email, role) on success, None on failure.

    Lookup order:
    1. admin_users DB table (if db_session provided).
    2. Env-var built-in credentials (ADMIN_EMAIL / ADMIN_PASSWORD).
    """
    settings = get_settings()

    # 1. DB lookup
    if db_session is not None:
        try:
            from sqlalchemy import select
            from app.models.admin_user import AdminUser
            result = await db_session.execute(
                select(AdminUser).where(AdminUser.email == email)
            )
            user = result.scalar_one_or_none()
            if user and user.is_active == "1" and verify_password(password, user.password_hash):
                return (user.email, user.role)
        except Exception as exc:
            logger.warning("auth.db_lookup_failed", error=str(exc))

    # 2. Env-var built-in
    if email == settings.ADMIN_EMAIL and password == settings.ADMIN_PASSWORD:
        return (email, AdminRole.ADMIN)

    return None
