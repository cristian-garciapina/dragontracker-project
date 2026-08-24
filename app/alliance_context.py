"""
Alliance context middleware.

Loads the 6 alliance.* settings from DB once per request and attaches them
to `request.state.alliance` as a simple namespace object. Also exposed as
`alliance` in all Jinja templates via a template global.

Kept intentionally minimal: this is where multi-tenant resolution will
plug in later (currently returns the single-tenant alliance from settings).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .db import SessionLocal


ALLIANCE_KEYS = [
    "alliance.name",
    "alliance.tag",
    "alliance.kingdom_id",
    "alliance.server_id",
    "alliance.tagline",
    "alliance.motto",
]

DEFAULTS = {
    "name": "Your Alliance",
    "tag": "AL",
    "kingdom_id": 0,
    "server_id": 0,
    "tagline": "",
    "motto": "",
}


def _load_alliance() -> SimpleNamespace:
    """Read alliance.* settings from DB, return a namespace with flat attrs."""
    values = dict(DEFAULTS)
    try:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT key, value FROM settings "
                    "WHERE key IN :keys"
                ).bindparams(
                    __import__("sqlalchemy").bindparam("keys", expanding=True)
                ),
                {"keys": ALLIANCE_KEYS},
            ).all()
            for key, raw_value in rows:
                attr = key.split(".", 1)[1]  # 'alliance.name' -> 'name'
                try:
                    values[attr] = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
                except Exception:
                    values[attr] = DEFAULTS.get(attr, "")
    except Exception:
        # DB not available yet (e.g. during first migration) — fall back to defaults
        pass
    return SimpleNamespace(**values)


class AllianceContextMiddleware(BaseHTTPMiddleware):
    """Attach the current alliance identity to every request."""

    async def dispatch(self, request: Request, call_next):
        request.state.alliance = _load_alliance()
        return await call_next(request)
