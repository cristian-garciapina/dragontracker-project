"""Site-wide gate: maintenance / closed modes.

Two independent flags in the settings table:
  - site.maintenance_mode -> shows "Site under maintenance"
  - site.closed           -> shows "Site closed" (takes precedence)

Owner is always allowed through. Public infra paths (login, static,
healthz, api, internal, csp) are always allowed regardless of flags.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import Request
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .db import SessionLocal
from .models import Setting, User, UserSession

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Paths that must never be gated.
ALLOWED_PREFIXES = (
    "/login",
    "/logout",
    "/static/",
    "/api/",
    "/internal/",
    "/healthz",
    "/csp-report",
    "/favicon.ico",
    "/_docs",
    "/openapi.json",
)


def _get_flag(db, key: str) -> bool:
    row = db.get(Setting, key)
    if row is None or not isinstance(row.value, dict):
        return False
    return bool(row.value.get("v"))


def get_site_status(db) -> dict:
    return {
        "maintenance": _get_flag(db, "site.maintenance_mode"),
        "closed": _get_flag(db, "site.closed"),
    }


def _current_user_role(db, request: Request) -> Optional[str]:
    """Read the session cookie and return the user's role, or None."""
    from .auth import COOKIE_NAME
    from datetime import datetime
    sid = request.cookies.get(COOKIE_NAME)
    if not sid:
        return None
    from sqlalchemy import select
    us = db.execute(
        select(UserSession).where(UserSession.session_id == sid)
    ).scalar_one_or_none()
    if us is None or us.expires_at < datetime.utcnow():
        return None
    user = db.get(User, us.user_id)
    if user is None or not user.is_active:
        return None
    return user.role


def _render_static(name: str, status: int) -> HTMLResponse:
    path = TEMPLATES_DIR / name
    html = path.read_text(encoding="utf-8")
    return HTMLResponse(html, status_code=status)


class SiteGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Always allow infra paths.
        if any(path.startswith(p) for p in ALLOWED_PREFIXES):
            return await call_next(request)

        db = SessionLocal()
        try:
            status = get_site_status(db)
            if not status["maintenance"] and not status["closed"]:
                return await call_next(request)

            role = _current_user_role(db, request)
            if role == "owner":
                return await call_next(request)

            # Gated. closed wins over maintenance.
            if status["closed"]:
                return _render_static("site_closed.html", 503)
            return _render_static("site_maintenance.html", 503)
        finally:
            db.close()
