"""Farming windows — staff declares periods where merits should be excluded from scoring."""
from datetime import date, datetime
from pathlib import Path
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import get_db, require_staff
from . import queries as q
from .models import SeasonFarmingWindow, Snapshot
from .scoring import recompute_scores_for_active_season

router = APIRouter(prefix="/staff/farming-windows", tags=["farming-windows"])

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _windows_with_bounds_status(db: Session, season_id: int):
    """List windows + info on whether bounds snapshots exist."""
    windows = db.execute(
        select(SeasonFarmingWindow)
        .where(SeasonFarmingWindow.season_id == season_id)
        .order_by(SeasonFarmingWindow.date_start.desc())
    ).scalars().all()

    all_snaps = db.execute(select(Snapshot).order_by(Snapshot.date_end)).scalars().all()

    rows = []
    for w in windows:
        upper = next((s for s in all_snaps if s.date_end >= w.date_end), None)
        lower_candidates = [s for s in all_snaps if s.date_end < w.date_start]
        lower = lower_candidates[-1] if lower_candidates else None
        rows.append({
            "w": w,
            "upper_ok": upper is not None,
            "upper_date": upper.date_end if upper else None,
            "lower_ok": lower is not None,
            "lower_date": lower.date_end if lower else None,
        })
    return rows


@router.get("", response_class=HTMLResponse)
def farming_windows_list(request: Request, user=Depends(require_staff), db: Session = Depends(get_db)):
    season = q.get_active_season(db)
    rows = _windows_with_bounds_status(db, season.id) if season else []
    return templates.TemplateResponse("staff/farming_windows.html", {
        "request": request, "user": user, "season": season, "rows": rows,
    })


@router.post("/new")
def create_farming_window(
    date_start: str = Form(...),
    date_end: str = Form(...),
    reason: str = Form(""),
    user=Depends(require_staff),
    db: Session = Depends(get_db),
):
    season = q.get_active_season(db)
    if not season:
        raise HTTPException(400, "No active season")
    try:
        ds = date.fromisoformat(date_start.strip())
        de = date.fromisoformat(date_end.strip())
    except ValueError:
        raise HTTPException(400, "Invalid date format (expected YYYY-MM-DD).")
    if de < ds:
        raise HTTPException(400, "End date must be on or after start date")

    w = SeasonFarmingWindow(
        season_id=season.id,
        date_start=ds,
        date_end=de,
        reason=reason.strip() or None,
        created_by=getattr(user, "username", None),
        created_at=datetime.utcnow(),
    )
    db.add(w)
    db.commit()

    recompute_scores_for_active_season(db)
    return RedirectResponse("/staff/farming-windows", status_code=303)


@router.post("/{window_id}/delete")
def delete_farming_window(
    window_id: int,
    user=Depends(require_staff),
    db: Session = Depends(get_db),
):
    w = db.get(SeasonFarmingWindow, window_id)
    if w is None:
        raise HTTPException(404, "Window not found")
    db.delete(w)
    db.commit()

    recompute_scores_for_active_season(db)
    return RedirectResponse("/staff/farming-windows", status_code=303)
