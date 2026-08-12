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
from .audit import record_staff_event, latest_event

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
    record_staff_event(
        session, entity_type="application", entity_id=app.id,
        entity_ref=app.reference, action="accepted", actor=acted_by,
    )
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
    record_staff_event(
        session, entity_type="application", entity_id=app.id,
        entity_ref=app.reference, action="rejected", actor=acted_by,
    )
    session.commit()
    return {"status": "rejected", "id": app.id}


@router.post("/applications/{app_id}/migrate", dependencies=[Depends(require_api_key)])
def api_migrate_application(
    app_id: int,
    payload: dict = Body(default={}),
    session: Session = Depends(get_session),
) -> dict:
    app = session.get(Application, app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    if app.status == "migrated":
        return {"status": "noop", "current_status": "migrated"}
    if app.status == "rejected":
        raise HTTPException(status_code=409, detail="cannot migrate a rejected application")
    if app.status not in ("new", "reviewing", "accepted"):
        raise HTTPException(status_code=409, detail=f"invalid current status: {app.status}")
    acted_by = str(payload.get("acted_by", "discord:unknown"))[:64]
    now = datetime.utcnow()
    app.status = "migrated"
    app.reviewed_by = acted_by
    app.reviewed_at = now
    app.status_updated_at = now
    # Auto-promote linked external user to member (mirror of update_status)
    linked = session.execute(
        select(User).where(User.character_id == app.player_id)
    ).scalar_one_or_none()
    promoted = False
    if linked is not None and linked.role == "external":
        linked.role = "member"
        promoted = True
    record_staff_event(
        session, entity_type="application", entity_id=app.id,
        entity_ref=app.reference, action="migrated", actor=acted_by,
    )
    session.commit()
    return {"status": "migrated", "id": app.id, "promoted_user_id": linked.id if promoted else None}


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
    acted_by = str(payload.get("acted_by", "discord:unknown"))[:64]
    u.pending_approval = False
    u.is_active = True
    u.role = grant_role
    record_staff_event(
        session, entity_type="registration", entity_id=u.id,
        entity_ref=u.username, action="approved",
        detail=grant_role, actor=acted_by,
    )
    session.commit()
    return {"status": "approved", "id": u.id, "role": grant_role}


@router.post("/registrations/{user_id}/reject", dependencies=[Depends(require_api_key)])
def api_reject_registration(
    user_id: int,
    payload: dict = Body(default={}),
    session: Session = Depends(get_session),
) -> dict:
    u = session.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="user not found")
    if not u.pending_approval:
        raise HTTPException(status_code=409, detail="user not pending")
    acted_by = str(payload.get("acted_by", "discord:unknown"))[:64]
    username = u.username
    record_staff_event(
        session, entity_type="registration", entity_id=u.id,
        entity_ref=username, action="rejected", actor=acted_by,
    )
    session.delete(u)
    session.commit()
    return {"status": "rejected", "id": user_id}


@router.get("/applications/{app_id}/status", dependencies=[Depends(require_api_key)])
def api_application_status(app_id: int, session: Session = Depends(get_session)) -> dict:
    """Return current state + last audit event for an application.

    Never 404s if there is history: returns state='deleted' when the row is
    gone but an audit event exists. Returns 404 only for true orphans.
    """
    app = session.get(Application, app_id)
    evt = latest_event(session, entity_type="application", entity_id=app_id)
    if app is None and evt is None:
        raise HTTPException(status_code=404, detail="unknown application")
    state = app.status if app is not None else "deleted"
    entity_ref = app.reference if app is not None else (evt.entity_ref if evt else None)
    return {
        "state": state,
        "entity_ref": entity_ref,
        "last_action": {
            "action": evt.action,
            "detail": evt.detail,
            "actor": evt.actor,
            "at": evt.at.isoformat(),
        } if evt else None,
    }


@router.get("/registrations/{user_id}/status", dependencies=[Depends(require_api_key)])
def api_registration_status(user_id: int, session: Session = Depends(get_session)) -> dict:
    """Return current state + last audit event for a registration.

    States:
      pending        -> user exists, pending_approval=True
      approved:role  -> user exists, active, not pending
      inactive       -> user exists but is_active=False
      rejected       -> user gone, last event was 'rejected'
      deleted        -> user gone, last event was 'deleted' or other
      404            -> user gone AND no audit event
    """
    u = session.get(User, user_id)
    evt = latest_event(session, entity_type="registration", entity_id=user_id)
    if u is None and evt is None:
        raise HTTPException(status_code=404, detail="unknown registration")
    if u is not None:
        if u.pending_approval:
            state = "pending"
        elif not u.is_active:
            state = "inactive"
        else:
            state = f"approved:{u.role}"
        entity_ref = u.username
    else:
        state = "rejected" if (evt and evt.action == "rejected") else "deleted"
        entity_ref = evt.entity_ref if evt else None
    return {
        "state": state,
        "entity_ref": entity_ref,
        "last_action": {
            "action": evt.action,
            "detail": evt.detail,
            "actor": evt.actor,
            "at": evt.at.isoformat(),
        } if evt else None,
    }


def _resolve_active_season_and_snapshot(session):
    """Return (season_id, latest_cum_snapshot_id) or (None, None)."""
    from sqlalchemy import select
    from .models import Season, Score
    season_id = session.execute(
        select(Season.id).where(Season.is_active == True)  # noqa: E712
    ).scalar_one_or_none()
    if season_id is None:
        return (None, None)
    snap_id = session.execute(
        select(Score.snapshot_id)
        .where(Score.season_id == season_id)
        .order_by(Score.snapshot_id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return (season_id, snap_id)


@router.get("/players/top", dependencies=[Depends(require_api_key)])
def players_top(n: int = 10, session: Session = Depends(get_session)) -> list[dict]:
    from .queries import get_default_roster_ordering
    from .models import Member
    n = max(1, min(int(n or 10), 50))
    season_id, snap_id = _resolve_active_season_and_snapshot(session)
    if season_id is None or snap_id is None:
        return []
    ordered = get_default_roster_ordering(session, season_id, snap_id)
    top = ordered[:n]
    name_by_cid = {
        m.character_id: m.current_name
        for m in session.execute(
            select(Member).where(Member.character_id.in_([r.character_id for r in top]))
        ).scalars().all()
    }
    return [
        {
            "rank": i + 1,
            "character_id": r.character_id,
            "current_name": name_by_cid.get(r.character_id, "?"),
            "grade": r.grade,
            "status": r.status,
            "mp_ratio": float(r.mp_ratio) if r.mp_ratio is not None else None,
            "merits_effective": int(r.merits_effective or 0),
            "primary_role": r.primary_role,
        }
        for i, r in enumerate(top)
    ]


@router.get("/players/search", dependencies=[Depends(require_api_key)])
def players_search(q: str = "", session: Session = Depends(get_session)) -> list[dict]:
    from .models import Member, Score
    q = (q or "").strip()
    if not q:
        return []
    season_id, snap_id = _resolve_active_season_and_snapshot(session)
    if season_id is None or snap_id is None:
        return []
    # search: nom LIKE %q% (case-insensitive) OR character_id exact si q est numerique
    conditions = [Member.current_name.ilike(f"%{q}%")]
    if q.isdigit():
        conditions.append(Member.character_id == int(q))
    from sqlalchemy import or_
    members = session.execute(
        select(Member).where(or_(*conditions)).limit(10)
    ).scalars().all()
    if not members:
        return []
    cids = [m.character_id for m in members]
    scores = {
        s.character_id: s
        for s in session.execute(
            select(Score)
            .where(Score.season_id == season_id)
            .where(Score.snapshot_id == snap_id)
            .where(Score.character_id.in_(cids))
        ).scalars().all()
    }
    out = []
    for m in members:
        sc = scores.get(m.character_id)
        out.append({
            "character_id": m.character_id,
            "current_name": m.current_name,
            "in_alliance": bool(m.in_alliance),
            "grade": sc.grade if sc else None,
            "status": sc.status if sc else None,
            "mp_ratio": float(sc.mp_ratio) if sc and sc.mp_ratio is not None else None,
            "merits_effective": int(sc.merits_effective or 0) if sc else None,
            "primary_role": sc.primary_role if sc else None,
        })
    return out


@router.get("/players/{character_id}", dependencies=[Depends(require_api_key)])
def player_detail(character_id: int, session: Session = Depends(get_session)) -> dict:
    from .queries import get_default_roster_ordering
    from .models import Member, Score
    member = session.get(Member, character_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Player not found")
    season_id, snap_id = _resolve_active_season_and_snapshot(session)
    payload: dict = {
        "character_id": member.character_id,
        "current_name": member.current_name,
        "in_alliance": bool(member.in_alliance),
        "grade": None,
        "status": None,
        "mp_ratio": None,
        "merits_effective": None,
        "merits_cumulative": None,
        "start_power": None,
        "end_power": None,
        "primary_role": None,
        "rank": None,
        "rank_total": None,
        "season_id": season_id,
    }
    if season_id is None or snap_id is None:
        return payload
    sc = session.execute(
        select(Score)
        .where(Score.season_id == season_id)
        .where(Score.snapshot_id == snap_id)
        .where(Score.character_id == character_id)
    ).scalar_one_or_none()
    if sc is not None:
        payload["grade"] = sc.grade
        payload["status"] = sc.status
        payload["mp_ratio"] = float(sc.mp_ratio) if sc.mp_ratio is not None else None
        payload["merits_effective"] = int(sc.merits_effective or 0)
        payload["merits_cumulative"] = int(sc.merits_cumulative or 0)
        payload["start_power"] = int(sc.start_power or 0)
        payload["end_power"] = int(sc.end_power or 0)
        payload["primary_role"] = sc.primary_role
    # rank dans l'ordre roster par defaut
    ordered = get_default_roster_ordering(session, season_id, snap_id)
    ids = [r.character_id for r in ordered]
    if character_id in ids:
        payload["rank"] = ids.index(character_id) + 1
        payload["rank_total"] = len(ids)
    return payload
