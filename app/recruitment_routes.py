"""
Recruitment endpoints.

Public:
  GET  /apply                     application form
  POST /apply                     submit form -> shows reference
  GET  /apply/status/{ref}        public follow-up (rate-limited)

Staff:
  GET  /staff/applications                       review queue
  POST /staff/applications/{id}/status           update status (non-reject)
  POST /staff/applications/{id}/reject           reject with mandatory reason
  POST /staff/applications/{id}/delete           hard delete (not for rejected)
"""
from __future__ import annotations

import secrets
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import get_db, require_staff
from .models import Application, User

router = APIRouter(tags=["recruitment"])

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

DISCORD_INVITE = "https://discord.gg/jbRycmjNJ"

# --- In-memory rate limit for /apply/status/{ref} ------------------------
# 20 hits per IP per hour. Sufficient for the scale of the site; if we ever
# need cross-process limits we'll move to a real store.
_RL_WINDOW_S = 3600
_RL_MAX = 20
_rl_hits: dict[str, deque] = {}


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    q = _rl_hits.setdefault(ip, deque())
    while q and q[0] < now - _RL_WINDOW_S:
        q.popleft()
    if len(q) >= _RL_MAX:
        return True
    q.append(now)
    return False


def _generate_reference(db: Session) -> str:
    """EV-<year>-<4 hex>. Retries if collision (extremely unlikely)."""
    year = datetime.utcnow().year
    for _ in range(8):
        token = secrets.token_hex(2).upper()  # 4 hex chars, 65k combos
        ref = f"EV-{year}-{token}"
        if not db.scalar(select(Application).where(Application.reference == ref)):
            return ref
    # fallback: extend entropy
    return f"EV-{year}-{secrets.token_hex(4).upper()}"


def _empty_form() -> dict:
    return {
        "in_game_name": "",
        "player_id": "",
        "current_alliance": "",
        "server": "",
        "motivation": "",
        "discord_handle": "",
    }


# ============================================================ PUBLIC =====

@router.get("/apply", response_class=HTMLResponse)
async def apply_form(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="recruitment/apply.html",
        context={
            "form": _empty_form(),
            "error": None,
            "submitted": False,
            "reference": None,
        },
    )


@router.post("/apply")
async def apply_submit(
    request: Request,
    in_game_name: str = Form(...),
    player_id: str = Form(...),
    current_alliance: str = Form(""),
    server: str = Form(...),
    motivation: str = Form(...),
    discord_handle: str = Form(""),
    db: Session = Depends(get_db),
):
    form = {
        "in_game_name": in_game_name,
        "player_id": player_id,
        "current_alliance": current_alliance,
        "server": server,
        "motivation": motivation,
        "discord_handle": discord_handle,
    }

    def render_error(msg: str):
        return templates.TemplateResponse(
            request=request,
            name="recruitment/apply.html",
            context={"form": form, "error": msg, "submitted": False, "reference": None},
            status_code=400,
        )

    name_clean = in_game_name.strip()
    if not name_clean or len(name_clean) > 64:
        return render_error("Please enter your in-game name.")
    try:
        player_id_int = int(player_id.strip())
    except ValueError:
        return render_error("Player ID must be a number.")
    try:
        server_int = int(server.strip())
    except ValueError:
        return render_error("Server must be a number.")
    motivation_clean = motivation.strip()
    if len(motivation_clean) < 20:
        return render_error("Please tell us a bit more about yourself (20+ characters).")

    now = datetime.utcnow()
    reference = _generate_reference(db)

    app = Application(
        in_game_name=name_clean,
        player_id=player_id_int,
        current_alliance=current_alliance.strip()[:64] or None,
        server=server_int,
        motivation=motivation_clean,
        discord_handle=discord_handle.strip()[:64] or None,
        created_at=now,
        status="new",
        reference=reference,
        status_updated_at=now,
    )
    db.add(app)
    db.commit()

    return templates.TemplateResponse(
        request=request,
        name="recruitment/apply.html",
        context={
            "form": _empty_form(),
            "error": None,
            "submitted": True,
            "reference": reference,
        },
    )


@router.get("/apply/status", response_class=HTMLResponse)
async def apply_status_lookup(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="recruitment/status_lookup.html",
        context={},
    )


@router.get("/apply/status/{ref}", response_class=HTMLResponse)
async def apply_status(ref: str, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip):
        return templates.TemplateResponse(
            request=request,
            name="recruitment/status.html",
            context={"state": "rate_limited", "reference": ref},
            status_code=429,
        )

    ref_clean = ref.strip().upper()[:20]
    app = db.scalar(select(Application).where(Application.reference == ref_clean))

    # Unknown reference and invalid reference produce the SAME screen
    # (no enumeration signal).
    if app is None:
        return templates.TemplateResponse(
            request=request,
            name="recruitment/status.html",
            context={"state": "not_found", "reference": ref_clean},
            status_code=404,
        )

    state = app.status
    return templates.TemplateResponse(
        request=request,
        name="recruitment/status.html",
        context={
            "state": state,
            "reference": app.reference,
            "created_at": app.created_at,
            "updated_at": app.status_updated_at or app.created_at,
            "in_game_name": app.in_game_name,
            "public_reason": app.public_reason if state == "rejected" else None,
            "discord_invite": DISCORD_INVITE if state == "accepted" else None,
        },
    )


# ============================================================ STAFF ======

@router.get("/staff/applications", response_class=HTMLResponse)
async def list_applications(
    request: Request,
    status: str = "",
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    stmt = select(Application).order_by(Application.created_at.desc())
    if status in ("new", "reviewing", "accepted", "rejected"):
        stmt = stmt.where(Application.status == status)

    apps = list(db.scalars(stmt).all())
    counts = {
        st: db.scalar(select(func.count(Application.id)).where(Application.status == st)) or 0
        for st in ("new", "reviewing", "accepted", "rejected")
    }

    return templates.TemplateResponse(
        request=request,
        name="staff/applications.html",
        context={
            "user": user,
            "kingdom": 193,
            "apps": apps,
            "counts": counts,
            "current_filter": status,
        },
    )


@router.post("/staff/applications/{app_id}/status")
async def update_status(
    app_id: int,
    new_status: str = Form(...),
    notes: str = Form(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Non-reject status updates: reviewing / accepted / reopen (new)."""
    app = db.get(Application, app_id)
    if app is None:
        return RedirectResponse(url="/staff/applications", status_code=303)

    # Reject goes through /reject to enforce public_reason.
    if new_status in ("new", "reviewing", "accepted"):
        app.status = new_status
        app.reviewed_by = user.username
        app.reviewed_at = datetime.utcnow()
        app.status_updated_at = datetime.utcnow()
    if notes.strip():
        app.notes = notes.strip()
    db.commit()
    return RedirectResponse(url="/staff/applications", status_code=303)


@router.post("/staff/applications/{app_id}/reject")
async def reject_application(
    app_id: int,
    public_reason: str = Form(...),
    notes: str = Form(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    app = db.get(Application, app_id)
    if app is None:
        return RedirectResponse(url="/staff/applications", status_code=303)

    reason = public_reason.strip()
    if not reason:
        return RedirectResponse(
            url=f"/staff/applications?err=reason_required#app-{app_id}",
            status_code=303,
        )
    if len(reason) > 500:
        reason = reason[:500]

    now = datetime.utcnow()
    app.status = "rejected"
    app.public_reason = reason
    app.reviewed_by = user.username
    app.reviewed_at = now
    app.status_updated_at = now
    if notes.strip():
        app.notes = notes.strip()
    db.commit()
    return RedirectResponse(url="/staff/applications", status_code=303)


@router.post("/staff/applications/{app_id}/delete")
async def delete_application(
    app_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Hard delete. Allowed on all statuses including rejected."""
    app = db.get(Application, app_id)
    if app is None:
        return RedirectResponse(url="/staff/applications", status_code=303)
    db.delete(app)
    db.commit()
    return RedirectResponse(url="/staff/applications", status_code=303)


@router.post("/staff/applications/{app_id}/notes")
async def update_notes(
    app_id: int,
    notes: str = Form(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Save internal notes without changing status."""
    app = db.get(Application, app_id)
    if app is None:
        return RedirectResponse(url="/staff/applications", status_code=303)
    app.notes = notes.strip() or None
    db.commit()
    return RedirectResponse(
        url=f"/staff/applications?saved=notes#app-{app_id}",
        status_code=303,
    )
