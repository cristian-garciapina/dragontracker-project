from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from datetime import datetime
from fastapi import Body
from .models import Application, Score, Season, User

router = APIRouter(prefix="/api", tags=["api"])


def require_api_key(authorization: Optional[str] = Header(None)) -> None:
    expected = os.environ.get("EV_API_KEY")
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="API not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    provided = authorization.removeprefix("Bearer ").strip()
    if provided != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")


@router.get("/players/grades", dependencies=[Depends(require_api_key)])
def players_grades(session: Session = Depends(get_session)) -> list[dict]:
    active_season = session.execute(
        select(Season).where(Season.is_active == True)  # noqa: E712
    ).scalar_one_or_none()
    if active_season is None:
        return []

    rows = session.execute(
        select(
            User.character_id,
            User.discord_id,
            Score.grade,
            Score.status,
            Score.is_farm_account,
        )
        .join(Score, Score.character_id == User.character_id)
        .where(User.discord_id.is_not(None))
        .where(Score.season_id == active_season.id)
    ).all()

    return [
        {
            "character_id": r.character_id,
            "discord_id": r.discord_id,
            "grade": r.grade,
            "status": r.status,
            "is_farm_account": bool(r.is_farm_account),
        }
        for r in rows
    ]


@router.get("/staff/pending", dependencies=[Depends(require_api_key)])
def staff_pending(session: Session = Depends(get_session)) -> dict:
    """Snapshot of items awaiting staff action. Used by the bot to resync on startup."""
    apps = session.execute(
        select(Application).where(Application.status.in_(("new", "reviewing")))
    ).scalars().all()
    signups = session.execute(
        select(User).where(User.pending_approval == True)  # noqa: E712
    ).scalars().all()
    return {
        "applications": [
            {
                "id": a.id,
                "in_game_name": a.in_game_name,
                "player_id": a.player_id,
                "current_alliance": a.current_alliance,
                "server": a.server,
                "discord_handle": a.discord_handle,
                "motivation": a.motivation,
                "status": a.status,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in apps
        ],
        "signups": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "submitted_in_game_name": u.submitted_in_game_name,
                "submitted_rank": u.submitted_rank,
                "submitted_server": u.submitted_server,
                "submitted_alliance_tag": u.submitted_alliance_tag,
                "submitted_at": u.submitted_at.isoformat() if u.submitted_at else None,
            }
            for u in signups
        ],
    }


@router.post("/applications/{app_id}/accept", dependencies=[Depends(require_api_key)])
def api_accept_application(
    app_id: int,
    payload: dict = Body(default={}),
    session: Session = Depends(get_session),
) -> dict:
    app = session.get(Application, app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    if app.status not in ("new", "reviewing"):
        return {"status": "noop", "current_status": app.status}
    acted_by = str(payload.get("acted_by", "discord:unknown"))[:64]
    now = datetime.utcnow()
    app.status = "accepted"
    app.reviewed_by = acted_by
    app.reviewed_at = now
    app.status_updated_at = now
    session.commit()
    return {"status": "accepted", "id": app.id}


@router.post("/applications/{app_id}/reject", dependencies=[Depends(require_api_key)])
def api_reject_application(
    app_id: int,
    payload: dict = Body(...),
    session: Session = Depends(get_session),
) -> dict:
    app = session.get(Application, app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    if app.status == "rejected":
        return {"status": "noop", "current_status": "rejected"}
    reason = str(payload.get("public_reason", "")).strip()
    if not reason:
        raise HTTPException(status_code=400, detail="public_reason required")
    if len(reason) > 500:
        reason = reason[:500]
    acted_by = str(payload.get("acted_by", "discord:unknown"))[:64]
    now = datetime.utcnow()
    app.status = "rejected"
    app.public_reason = reason
    app.reviewed_by = acted_by
    app.reviewed_at = now
    app.status_updated_at = now
    session.commit()
    return {"status": "rejected", "id": app.id}


@router.post("/registrations/{user_id}/approve", dependencies=[Depends(require_api_key)])
def api_approve_registration(
    user_id: int,
    payload: dict = Body(default={}),
    session: Session = Depends(get_session),
) -> dict:
    u = session.get(User, user_id)
    if u is None or not u.pending_approval:
        raise HTTPException(status_code=404, detail="pending user not found")
    grant_role = payload.get("grant_role", "member")
    if grant_role not in ("external", "member", "staff"):
        grant_role = "member"
    # API cannot grant owner role — only interactive staff can (existing web rule).
    u.pending_approval = False
    u.is_active = True
    u.role = grant_role
    session.commit()
    return {"status": "approved", "id": u.id, "role": grant_role}


@router.post("/registrations/{user_id}/reject", dependencies=[Depends(require_api_key)])
def api_reject_registration(
    user_id: int,
    session: Session = Depends(get_session),
) -> dict:
    u = session.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="user not found")
    if not u.pending_approval:
        raise HTTPException(status_code=409, detail="user not pending")
    session.delete(u)
    session.commit()
    return {"status": "rejected", "id": user_id}
