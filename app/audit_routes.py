from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import get_db, require_staff
from .models import StaffEvent, User

router = APIRouter(tags=["staff"])

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

PAGE_SIZE = 50


@router.get("/staff/audit", response_class=HTMLResponse)
async def audit_log(
    request: Request,
    entity_type: str = Query(""),
    action: str = Query(""),
    actor: str = Query(""),
    q: str = Query(""),
    page: int = Query(1, ge=1),
    actor_user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    stmt = select(StaffEvent)
    count_stmt = select(func.count(StaffEvent.id))

    if entity_type:
        stmt = stmt.where(StaffEvent.entity_type == entity_type)
        count_stmt = count_stmt.where(StaffEvent.entity_type == entity_type)
    if action:
        stmt = stmt.where(StaffEvent.action == action)
        count_stmt = count_stmt.where(StaffEvent.action == action)
    if actor:
        stmt = stmt.where(StaffEvent.actor == actor)
        count_stmt = count_stmt.where(StaffEvent.actor == actor)
    if q:
        needle = f"%{q.strip()}%"
        stmt = stmt.where(StaffEvent.entity_ref.ilike(needle))
        count_stmt = count_stmt.where(StaffEvent.entity_ref.ilike(needle))

    total = db.execute(count_stmt).scalar_one()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)
    offset = (page - 1) * PAGE_SIZE

    events = db.scalars(
        stmt.order_by(StaffEvent.at.desc(), StaffEvent.id.desc())
        .offset(offset)
        .limit(PAGE_SIZE)
    ).all()

    distinct_entity_types = [
        r[0] for r in db.execute(
            select(StaffEvent.entity_type).distinct().order_by(StaffEvent.entity_type)
        ).all()
    ]
    distinct_actions = [
        r[0] for r in db.execute(
            select(StaffEvent.action).distinct().order_by(StaffEvent.action)
        ).all()
    ]
    distinct_actors = [
        r[0] for r in db.execute(
            select(StaffEvent.actor).distinct().order_by(StaffEvent.actor)
        ).all()
    ]

    return templates.TemplateResponse(
        request=request,
        name="staff/audit.html",
        context={
            "events": events,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "page_size": PAGE_SIZE,
            "filters": {
                "entity_type": entity_type,
                "action": action,
                "actor": actor,
                "q": q,
            },
            "distinct_entity_types": distinct_entity_types,
            "distinct_actions": distinct_actions,
            "distinct_actors": distinct_actors,
            "user": actor_user,
        },
    )


@router.get("/staff/audit/json")
async def audit_log_json(
    entity_type: str = Query(""),
    action: str = Query(""),
    actor: str = Query(""),
    q: str = Query(""),
    page: int = Query(1, ge=1),
    actor_user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    count_stmt = select(func.count(StaffEvent.id))
    stmt = select(StaffEvent)
    if entity_type:
        stmt = stmt.where(StaffEvent.entity_type == entity_type)
        count_stmt = count_stmt.where(StaffEvent.entity_type == entity_type)
    if action:
        stmt = stmt.where(StaffEvent.action == action)
        count_stmt = count_stmt.where(StaffEvent.action == action)
    if actor:
        stmt = stmt.where(StaffEvent.actor == actor)
        count_stmt = count_stmt.where(StaffEvent.actor == actor)
    if q:
        needle = f"%{q.strip()}%"
        stmt = stmt.where(StaffEvent.entity_ref.ilike(needle))
        count_stmt = count_stmt.where(StaffEvent.entity_ref.ilike(needle))

    total = db.execute(count_stmt).scalar_one()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)
    offset = (page - 1) * PAGE_SIZE
    events = db.scalars(
        stmt.order_by(StaffEvent.at.desc(), StaffEvent.id.desc())
        .offset(offset)
        .limit(PAGE_SIZE)
    ).all()
    return {
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "latest_ids": [e.id for e in events],
        "latest_at": events[0].at.isoformat() if events else None,
    }
