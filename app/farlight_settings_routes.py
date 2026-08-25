"""
Owner-only Farlight JWT management in /staff/settings.

- GET  /staff/settings/farlight-status  -> JSON with current JWT state
- POST /staff/settings/farlight-jwt     -> rotate JWT from web UI
- POST /staff/settings/farlight-test    -> ping Farlight API to validate stored JWT
"""
from __future__ import annotations

from datetime import datetime
import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from .auth import require_owner
from .db import get_session
from .farlight_client import (
    decode_jwt_payload,
    jwt_expiry,
    validate_jwt_shape,
    fetch_topn,
)
from .models import User
from .secrets_store import get_secret, get_secret_meta, set_secret

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/staff/settings", tags=["staff-settings-farlight"])

SECRET_KEY_JWT = "farlight_jwt"


@router.get("/farlight-status")
def farlight_status(
    session: Session = Depends(get_session),
    owner: User = Depends(require_owner),
) -> dict:
    """Return current JWT state (no secret leaked). Owner-only."""
    meta = get_secret_meta(session, SECRET_KEY_JWT)
    if meta is None:
        return {"present": False}

    # Real structure: {"key", "expires_at" (datetime), "metadata" (dict),
    # "updated_at" (datetime), "updated_by"}
    exp_dt = meta.get("expires_at")
    payload = meta.get("metadata") or {}

    days_left = None
    expired = None
    if exp_dt is not None:
        delta_secs = (exp_dt - datetime.utcnow()).total_seconds()
        days_left = int(delta_secs // 86400)
        expired = delta_secs < 0

    updated_at = meta.get("updated_at")
    return {
        "present": True,
        "account": payload.get("account"),
        "expires_at": exp_dt.isoformat() if exp_dt else None,
        "days_left": days_left,
        "expired": expired,
        "updated_by": meta.get("updated_by"),
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


@router.post("/farlight-jwt")
async def farlight_jwt_update(
    request: Request,
    token: str = Form(...),
    owner: User = Depends(require_owner),
    session: Session = Depends(get_session),
):
    """Rotate the Farlight JWT from the web UI. Owner-only."""
    token = (token or "").strip()
    if token.startswith("Bearer "):
        token = token[7:].strip()

    if len(token) < 100:
        return RedirectResponse(
            url="/staff/settings?farlight_error=Token+looks+too+short.",
            status_code=303,
        )

    try:
        payload = decode_jwt_payload(token)
        validate_jwt_shape(payload)
    except Exception as e:
        return RedirectResponse(
            url=f"/staff/settings?farlight_error=Invalid+JWT:+{e}",
            status_code=303,
        )

    exp = jwt_expiry(payload)
    meta = {
        "account": payload.get("account"),
        "jti": payload.get("jti"),
        "iss": payload.get("iss"),
        "aud": payload.get("aud"),
        "client_id": payload.get("client_id"),
        "iat": payload.get("iat"),
        "exp": payload.get("exp"),
    }
    set_secret(
        session,
        SECRET_KEY_JWT,
        token,
        expires_at=exp,
        metadata=meta,
        updated_by=owner.username[:64],
    )
    days_left = (exp - datetime.utcnow()).days
    logger.info(
        "farlight_jwt_update: stored new JWT account=%s exp=%s (%dd) by=%s",
        payload.get("account"), exp.isoformat(), days_left, owner.username,
    )
    return RedirectResponse(
        url=f"/staff/settings?farlight_saved=1&exp_days={days_left}",
        status_code=303,
    )


@router.post("/farlight-test")
def farlight_test(
    session: Session = Depends(get_session),
    owner: User = Depends(require_owner),
) -> JSONResponse:
    """Ping Farlight API with the stored JWT. Owner-only."""
    token = get_secret(session, SECRET_KEY_JWT)
    if not token:
        return JSONResponse({"ok": False, "error": "No JWT stored."}, status_code=400)

    # Try a minimal call. fetch_topn signature: (jwt, server_id, date_start, date_end, limit)
    # We only care about auth-level errors; use a tiny date range.
    try:
        # Import settings dynamically to get current kingdom
        from sqlalchemy import text
        import json as _json
        row = session.execute(
            text("SELECT value FROM settings WHERE key = 'alliance.server_id'")
        ).first()
        server_id = 0
        if row and row[0] is not None:
            raw = row[0]
            # SQLite/SQLAlchemy may return either a str (raw JSON) or an int (parsed)
            if isinstance(raw, (int, float)):
                server_id = int(raw)
            elif isinstance(raw, str):
                try:
                    server_id = int(_json.loads(raw))
                except Exception:
                    server_id = int(raw) if raw.isdigit() else 0

        if server_id == 0:
            return JSONResponse(
                {"ok": False, "error": "Server ID not set in Alliance identity."},
                status_code=400,
            )

        today = datetime.utcnow().date().isoformat()
        # Minimal ping: try fetching a single row for today
        _ = fetch_topn(token, start_date=today, end_date=today, server_id=server_id, timeout=8.0)
        return JSONResponse({"ok": True, "message": f"Connection OK (kingdom {server_id})."})
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"{type(e).__name__}: {e}"},
            status_code=502,
        )


@router.post("/farlight-force-pull")
def farlight_force_pull(
    session: Session = Depends(get_session),
    owner: User = Depends(require_owner),
) -> JSONResponse:
    """Trigger a full Farlight pull immediately. Owner-only. Blocking."""
    from .farlight_pull import run_pull
    try:
        summary = run_pull(session, force=True)
        status_code = 200 if summary.get("status") in ("ok", "skipped_manual") else 502
        return JSONResponse(summary, status_code=status_code)
    except Exception as e:
        return JSONResponse(
            {"status": "exception", "error": f"{type(e).__name__}: {e}"},
            status_code=500,
        )

