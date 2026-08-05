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
from fastapi import Depends as _EV_Depends
from sqlalchemy.orm import Session as _EV_Session
from sqlalchemy import select as _EV_select
from .models import Season as _EV_Season
from .auth import get_db as _EV_get_db
from .queries import get_player_daily_merits as _EV_gpdm

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import get_db, require_user, require_staff
from .models import Burn, Member, PlayerNote, Score, Season, Stat, User
from .queries import get_player_event_history

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
            "primary_role": score.primary_role,
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

    active_season = db.scalar(select(Season).where(Season.is_active == True))
    burns_current_season = []
    if active_season is not None:
        burns_current_season = list(db.scalars(
            select(Burn)
            .where(Burn.character_id == character_id)
            .where(Burn.season_id == active_season.id)
            .order_by(Burn.burn_date.desc(), Burn.recorded_at.desc())
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
            "active_season": active_season,
            "burns_current_season": burns_current_season,
            "event_history": get_player_event_history(db, character_id),
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



@router.post("/player/{character_id}/burns")
async def add_burn(
    character_id: int,
    merits_gained: str = Form(""),
    power_before: str = Form(""),
    power_after: str = Form(""),
    target: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    member = db.get(Member, character_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Player not found")

    active_season = db.scalar(select(Season).where(Season.is_active == True))
    if active_season is None:
        return RedirectResponse(url=f"/player/{character_id}#burns", status_code=303)

    def _to_int(v: str) -> int | None:
        v = v.strip()
        if not v:
            return None
        try:
            return int(v.replace(" ", "").replace(",", ""))
        except ValueError:
            return None

    burn = Burn(
        character_id=character_id,
        season_id=active_season.id,
        burn_date=datetime.utcnow().date(),
        merits_gained=_to_int(merits_gained),
        power_before=_to_int(power_before),
        power_after=_to_int(power_after),
        target=(target.strip()[:64] or None),
        notes=(notes.strip()[:2000] or None),
        recorded_by=user.username,
        recorded_at=datetime.utcnow(),
    )
    db.add(burn)
    db.commit()
    # Recompute scores if this burn carries a merits_gained value
    if burn.merits_gained:
        try:
            from .scoring import recompute_scores_for_active_season
            recompute_scores_for_active_season(db)
            db.commit()
        except Exception:
            pass
    return RedirectResponse(url=f"/player/{character_id}#burns", status_code=303)


@router.post("/player/{character_id}/burns/{burn_id}/delete")
async def delete_burn(
    character_id: int,
    burn_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    burn = db.get(Burn, burn_id)
    had_merits = False
    if burn is not None and burn.character_id == character_id:
        had_merits = bool(burn.merits_gained)
        db.delete(burn)
        db.commit()
        if had_merits:
            try:
                from .scoring import recompute_scores_for_active_season
                recompute_scores_for_active_season(db)
                db.commit()
            except Exception:
                pass
    return RedirectResponse(url=f"/player/{character_id}#burns", status_code=303)


@router.get("/player/{character_id}/daily-merits.json")
def daily_merits_json(character_id: int, season: int = None, db: _EV_Session = _EV_Depends(_EV_get_db)):
    all_seasons = db.execute(
        _EV_select(_EV_Season).order_by(_EV_Season.start_date.desc())
    ).scalars().all()
    if season is not None:
        target = next((s for s in all_seasons if s.id == season), None)
    else:
        target = next((s for s in all_seasons if s.is_active), None)
    if not target:
        return {"data": [], "seasons": [], "current_season_id": None}
    seasons_list = [
        {"id": s.id, "name": s.name, "is_active": bool(s.is_active)}
        for s in all_seasons
    ]
    return {
        "data": _EV_gpdm(db, character_id, target.id),
        "seasons": seasons_list,
        "current_season_id": target.id,
    }
