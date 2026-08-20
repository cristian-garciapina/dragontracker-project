from __future__ import annotations

"""
Public manual page — GET /manual

Renders app/templates/manual.html with live values from the DB:
- Active season name
- Grading thresholds (S/A/B/C) from settings
- Farm account power threshold (in millions)

Uses raw SQL to stay independent of ORM class naming, and falls back to
sensible defaults on any error so the page always renders.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _get_setting_int(db: Session, key: str, default: int) -> int:
    """Fetch a numeric setting by key, defaulting on any failure."""
    try:
        row = db.execute(
            text("SELECT value FROM settings WHERE key = :k LIMIT 1"),
            {"k": key},
        ).fetchone()
        if not row or row[0] is None:
            return default
        return int(float(row[0]))
    except Exception:
        return default


def _get_active_season_name(db: Session, default: str = "the current season") -> str:
    try:
        row = db.execute(
            text("SELECT name FROM seasons WHERE is_active = 1 LIMIT 1")
        ).fetchone()
        if not row or not row[0]:
            return default
        return str(row[0])
    except Exception:
        return default


@router.get("/manual", response_class=HTMLResponse)
async def manual(request: Request, db: Session = Depends(get_db)):
    season_name = _get_active_season_name(db)

    farm_bytes = _get_setting_int(db, "scoring.farm_account_power_threshold", 15_000_000)
    farm_m = max(1, farm_bytes // 1_000_000)

    ctx = {
        "request": request,
        "season_name": season_name,
        "threshold_s": _get_setting_int(db, "scoring.threshold.s", 10),
        "threshold_a": _get_setting_int(db, "scoring.threshold.a", 7),
        "threshold_b": _get_setting_int(db, "scoring.threshold.b", 4),
        "threshold_c": _get_setting_int(db, "scoring.threshold.c", 1),
        "farm_threshold_m": farm_m,
    }
    return templates.TemplateResponse("manual.html", ctx)
