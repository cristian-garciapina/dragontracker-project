"""
Staff settings page.

GET  /staff/settings        view current values
POST /staff/settings        save new values + audit log + recompute scores

Both 'staff' and 'owner' can access. Every modification is recorded in
audit_log (one row per changed key) with the actor's username and the
old/new values.
"""
import json
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import queries
from .auth import get_db, require_staff
from .models import AuditLog, Setting, User
from .scoring import recompute_scores_for_active_season
from .site_gate import get_site_status

router = APIRouter(tags=["staff-settings"])

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _get_alliance_name(db: Session) -> str:
    """Read current alliance.name from settings (JSON string)."""
    from sqlalchemy import text as _text
    row = db.execute(_text("SELECT value FROM settings WHERE key = 'alliance.name'")).first()
    if row is None:
        return "Your Alliance"
    try:
        return str(__import__('json').loads(row[0])) if isinstance(row[0], str) else str(row[0])
    except Exception:
        return "Your Alliance"


def _render(request: Request, db: Session, user: User,
            error: Optional[str] = None, success: Optional[str] = None,
            recompute_info: Optional[dict] = None,
            reset_done: bool = False):
    return templates.TemplateResponse(
        request=request,
        name="staff/settings.html",
        context={
            "user": user,
            "kingdom": 193,
            "settings": queries.load_editable_settings(db),
            "error": error,
            "success": success,
            "recompute": recompute_info,
            "site_status": get_site_status(db),
            "alliance_name": _get_alliance_name(db),
            "reset_done": reset_done,
        },
    )


@router.get("/staff/settings", response_class=HTMLResponse)
async def settings_view(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    reset_done = request.query_params.get("reset") == "done"
    return _render(request, db, user, reset_done=reset_done)


@router.post("/staff/settings")
async def settings_update(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    form = await request.form()
    specs = queries.load_editable_settings(db)
    changes: list[tuple[str, object, object]] = []  # (key, old, new)
    error_msg = None

    for spec in specs:
        key = spec["key"]
        raw = form.get(key, "").strip()
        if raw == "":
            error_msg = f"{spec['label']} cannot be empty."
            break

        try:
            new_value = float(raw) if spec["kind"] == "float" else int(raw)
        except ValueError:
            error_msg = f"{spec['label']} must be a number."
            break

        if new_value < 0:
            error_msg = f"{spec['label']} cannot be negative."
            break

        old_value = spec["current"]
        if new_value != old_value:
            changes.append((key, old_value, new_value))

    if error_msg:
        return _render(request, db, user, error=error_msg)

    if not changes:
        return _render(request, db, user, success="Nothing to save — values unchanged.")

    # --- Validate threshold ordering: S > A > B > C
    proposed = {spec["key"]: spec["current"] for spec in specs}
    for key, _old, new in changes:
        proposed[key] = new
    threshold_keys = [
        "scoring.threshold.s",
        "scoring.threshold.a",
        "scoring.threshold.b",
        "scoring.threshold.c",
    ]
    missing = [k for k in threshold_keys if not isinstance(proposed.get(k), (int, float))]
    if missing:
        return _render(
            request, db, user,
            error=(
                f"Threshold settings missing or null: {', '.join(missing)}. "
                "Set all four before saving."
            ),
        )
    s = proposed["scoring.threshold.s"]
    a = proposed["scoring.threshold.a"]
    b = proposed["scoring.threshold.b"]
    c = proposed["scoring.threshold.c"]
    if not (s > a > b > c):
        return _render(
            request, db, user,
            error="Thresholds must satisfy S > A > B > C strictly.",
        )

    # --- Persist + audit
    now = datetime.utcnow()
    for key, old, new in changes:
        row = db.get(Setting, key)
        if row is None:
            continue
        row.value = {"v": new}
        row.updated_at = now
        row.updated_by = user.username
        db.add(AuditLog(
            user=user.username,
            action="update",
            target_type="setting",
            target_id=key,
            old_value={"v": old},
            new_value={"v": new},
            timestamp=now,
        ))
    db.commit()

    # --- Recompute scores so the new thresholds take effect immediately
    recompute_info = recompute_scores_for_active_season(db)

    return _render(
        request, db, user,
        success=f"Saved {len(changes)} change(s) and recomputed scores.",
        recompute_info=recompute_info,
    )


# ---- Site status flags (maintenance / closed) ------------------------------

def _set_bool_setting(db: Session, key: str, value: bool) -> None:
    row = db.get(Setting, key)
    if row is None:
        row = Setting(
            key=key,
            value={"v": bool(value)},
            value_type="bool",
            category="site",
            description="Site gate flag (owner-only bypass when ON).",
            editable_by="owner",
        )
        db.add(row)
    else:
        row.value = {"v": bool(value)}


@router.post("/staff/settings/site-status")
async def site_status_update(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    form = await request.form()
    maintenance = form.get("maintenance_mode") == "on"
    closed = form.get("closed") == "on"

    prev = get_site_status(db)
    _set_bool_setting(db, "site.maintenance_mode", maintenance)
    _set_bool_setting(db, "site.closed", closed)

    for key, old, new in (
        ("site.maintenance_mode", prev["maintenance"], maintenance),
        ("site.closed", prev["closed"], closed),
    ):
        if old != new:
            db.add(AuditLog(
                user=user.username,
                action="setting_update",
                target_type="setting",
                target_id=key,
                old_value={"v": old},
                new_value={"v": new},
                timestamp=datetime.utcnow(),
            ))
    db.commit()

    return _render(request, db, user, success="Site status updated.")
