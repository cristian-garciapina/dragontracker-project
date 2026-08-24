"""
POST /staff/settings/alliance — editable alliance identity block.

Lets staff update the 6 alliance.* settings (name, tag, kingdom_id,
server_id, tagline, motto) from the /staff/settings UI. Also syncs the
alliances table row #1 so the tenant root stays coherent with the
settings vitrine.
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from .auth import require_staff
from .db import get_session
from .models import AuditLog, Setting, User

router = APIRouter(prefix="/staff/settings", tags=["staff-settings-alliance"])


ALLIANCE_FIELDS = [
    # (form_field, setting_key, kind, label)
    ("name", "alliance.name", "string", "Alliance name"),
    ("tag", "alliance.tag", "string", "Alliance tag"),
    ("kingdom_id", "alliance.kingdom_id", "int", "Kingdom ID"),
    ("server_id", "alliance.server_id", "int", "Server ID"),
    ("tagline", "alliance.tagline", "string", "Tagline"),
    ("motto", "alliance.motto", "string", "Motto"),
]


def _parse(raw: str, kind: str, label: str):
    """Return (value, error_msg). error_msg is None on success."""
    raw = (raw or "").strip()
    if kind == "int":
        if raw == "":
            return 0, None
        try:
            v = int(raw)
        except ValueError:
            return None, f"{label} must be an integer."
        if v < 0:
            return None, f"{label} cannot be negative."
        return v, None
    # string
    return raw, None


@router.post("/alliance")
async def alliance_update(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_session),
):
    form = await request.form()

    parsed = {}
    for field, key, kind, label in ALLIANCE_FIELDS:
        raw = form.get(field, "")
        value, err = _parse(raw, kind, label)
        if err:
            return RedirectResponse(
                url=f"/staff/settings?alliance_error={err}",
                status_code=303,
            )
        parsed[key] = value

    # Alliance name cannot be blank (used as reset confirmation string too)
    if not parsed["alliance.name"]:
        return RedirectResponse(
            url="/staff/settings?alliance_error=Alliance+name+cannot+be+empty.",
            status_code=303,
        )

    now = datetime.utcnow()
    try:
        # 1. Update settings rows + audit each change
        for _field, key, _kind, _label in ALLIANCE_FIELDS:
            row = db.get(Setting, key)
            if row is None:
                continue
            old_value = row.value
            new_value = parsed[key]
            if old_value == new_value:
                continue
            row.value = new_value
            row.updated_at = now
            row.updated_by = user.username
            db.add(AuditLog(
                user=user.username,
                action="update",
                target_type="setting",
                target_id=key,
                old_value={"v": old_value},
                new_value={"v": new_value},
                timestamp=now,
            ))

        # 2. Sync alliances row #1 (tenant root)
        db.execute(
            text(
                "UPDATE alliances SET name = :name, tag = :tag, "
                "kingdom_number = :kid WHERE id = 1"
            ),
            {
                "name": parsed["alliance.name"],
                "tag": parsed["alliance.tag"],
                "kid": parsed["alliance.kingdom_id"],
            },
        )

        db.commit()
    except Exception as exc:
        db.rollback()
        return RedirectResponse(
            url=f"/staff/settings?alliance_error=Save+failed:+{exc}",
            status_code=303,
        )

    return RedirectResponse(
        url="/staff/settings?alliance_saved=1",
        status_code=303,
    )
