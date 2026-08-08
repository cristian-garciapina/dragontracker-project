"""
Auth core — argon2 hashing, session lifecycle, FastAPI dependencies.

Session model:
- Server-side record in `user_sessions` table.
- The session_id (random 32 bytes hex) is the only value sent to the browser.
- Cookie name `ev_session`, httpOnly, SameSite=Lax, secure in production.
- Default lifetime: 30 days. Renewed on each request.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import User, UserSession

# --- Config ---------------------------------------------------------------
COOKIE_NAME = "ev_session"
SESSION_LIFETIME = timedelta(days=30)
SECURE_COOKIES = os.environ.get("EV_ENV", "production") == "production"

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


# --- Custom exceptions ----------------------------------------------------
class RequiresLoginException(Exception):
    """Raised by require_user when the visitor is anonymous.

    Caught by the app-level handler in main.py and turned into a real
    HTTP redirect to /login?next=<original_path>. We use a custom
    exception rather than HTTPException because the latter renders a
    JSON error body that browsers won't follow as a navigation.
    """
    def __init__(self, next_url: str):
        self.next_url = next_url


class ExternalGateException(Exception):
    """Raised by require_user when a role=external user tries to access
    a page outside their allowlist. Caught in main.py, redirects to /apply.
    """
    pass


class MissingEmailException(Exception):
    """Raised by require_user when a user has no email set. Caught in main.py,
    redirects to /profile with a banner asking to add one.
    """
    pass


# --- DB dependency --------------------------------------------------------
def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Hashing --------------------------------------------------------------
def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# --- Session lifecycle ----------------------------------------------------
def create_session(
    db: Session, user: User, ip: Optional[str], user_agent: Optional[str]
) -> UserSession:
    """Issue a fresh session row + return it. Caller sets the cookie."""
    now = datetime.utcnow()
    session = UserSession(
        session_id=secrets.token_hex(32),
        user_id=user.id,
        created_at=now,
        expires_at=now + SESSION_LIFETIME,
        last_seen_at=now,
        ip=ip,
        user_agent=user_agent[:256] if user_agent else None,
    )
    db.add(session)
    user.last_login_at = now
    db.commit()
    db.refresh(session)
    return session


def delete_session(db: Session, session_id: str) -> None:
    """Logout: drop the session row."""
    obj = db.scalar(select(UserSession).where(UserSession.session_id == session_id))
    if obj is not None:
        db.delete(obj)
        db.commit()


def _set_cookie(response, session_id: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=SECURE_COOKIES,
        samesite="lax",
        path="/",
    )


def _clear_cookie(response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


# --- Dependencies ---------------------------------------------------------
def get_current_user(
    request: Request,
    ev_session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Returns the logged-in User, or None. Refreshes last_seen_at on each call."""
    if not ev_session:
        return None

    session = db.scalar(
        select(UserSession).where(UserSession.session_id == ev_session)
    )
    if session is None:
        return None
    if session.expires_at < datetime.utcnow():
        db.delete(session)
        db.commit()
        return None

    session.last_seen_at = datetime.utcnow()
    db.commit()

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None
    return user


def require_user(
    request: Request,
    user: Optional[User] = Depends(get_current_user),
) -> User:
    """Forces login. Anonymous visitors trigger a redirect to /login.
    External users are gated to /apply, /profile, /logout only."""
    if user is None:
        raise RequiresLoginException(next_url=request.url.path)
    if user.role == "external":
        path = request.url.path
        allowed = (
            path.startswith("/apply")
            or path.startswith("/profile")
            or path == "/logout"
        )
        if not allowed:
            raise ExternalGateException()
    if not user.email:
        path = request.url.path
        email_allowed = (
            path.startswith("/profile")
            or path.startswith("/auth/discord")
            or path == "/logout"
        )
        if not email_allowed:
            raise MissingEmailException()
    return user


def require_staff(user: User = Depends(require_user)) -> User:
    """Forces staff-level access (staff or owner). 403 if member-only."""
    if user.role not in ("staff", "owner"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff only.",
        )
    return user


def require_owner(user: User = Depends(require_user)) -> User:
    """Forces owner role. 403 otherwise."""
    if user.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner only.",
        )
    return user
