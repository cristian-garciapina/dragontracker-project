"""
Player profile page.

Public (any logged-in user):
  GET  /player/{character_id}                     profile view

Staff only:
  POST /player/{character_id}/notes               add a note
  POST /player/{character_id}/notes/{id}/delete   remove a note

Notes are traceability for the staff. They are never rendered for
non-staff users.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import get_db, require_user, require_staff
from .models import Member, PlayerNote, Score, Season, Stat, User

router = APIRouter(tags=["player"])
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("/player/{character_id}", response_class=HTMLResponse)
async def player_profile(
    request: Request,
    character_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    member = db.get(Member, character_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Player not found")

    is_staff = user.role in ("staff", "owner")

    # Season history: keep the latest computed score per season.
    rows = db.execute(
        select(Score, Season)
        .join(Season, Score.season_id == Season.id)
        .where(Score.character_id == character_id)
        .order_by(Season.start_date.desc(), Score.computed_at.desc())
    ).all()

    seen: set[int] = set()
    history: list[dict] = []
    for score, season in rows:
        if season.id in seen:
            continue
        seen.add(season.id)
        history.append({
            "season_id": season.id,
            "season_name": season.name,
            "season_start": season.start_date.isoformat(),
            "season_end": season.end_date.isoformat() if season.end_date else None,
            "season_active": season.is_active,
            "grade": score.grade or "—",
            "status": score.status,
            "mp_ratio": round(score.mp_ratio, 2),
            "start_power": score.start_power,
            "end_power": score.end_power,
            "merits_cumulative": score.merits_cumulative,
            "is_farm_account": score.is_farm_account,
        })

    current = history[0] if history else None
    previous = history[1] if len(history) > 1 else None
    delta = None
    if current and previous:
        delta = round(current["mp_ratio"] - previous["mp_ratio"], 2)

    latest_stat = db.scalar(
        select(Stat)
        .where(Stat.character_id == character_id)
        .order_by(Stat.snapshot_id.desc())
        .limit(1)
    )

    notes = []
    if is_staff:
        notes = list(db.scalars(
            select(PlayerNote)
            .where(PlayerNote.character_id == character_id)
            .order_by(PlayerNote.created_at.desc())
        ).all())

    return templates.TemplateResponse(
        request=request,
        name="player/profile.html",
        context={
            "user": user,
            "is_staff": is_staff,
            "member": member,
            "current": current,
            "previous": previous,
            "delta": delta,
            "history": history,
            "latest_stat": latest_stat,
            "notes": notes,
        },
    )


@router.post("/player/{character_id}/notes")
async def add_note(
    character_id: int,
    body: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    member = db.get(Member, character_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Player not found")

    body_clean = body.strip()
    if not body_clean:
        return RedirectResponse(url=f"/player/{character_id}#notes", status_code=303)
    if len(body_clean) > 4000:
        body_clean = body_clean[:4000]

    note = PlayerNote(
        character_id=character_id,
        body=body_clean,
        created_by=user.username,
        created_at=datetime.utcnow(),
    )
    db.add(note)
    db.commit()
    return RedirectResponse(url=f"/player/{character_id}#notes", status_code=303)


@router.post("/player/{character_id}/notes/{note_id}/delete")
async def delete_note(
    character_id: int,
    note_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    note = db.get(PlayerNote, note_id)
    if note is not None and note.character_id == character_id:
        db.delete(note)
        db.commit()
    return RedirectResponse(url=f"/player/{character_id}#notes", status_code=303)
