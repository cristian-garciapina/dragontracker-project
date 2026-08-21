from __future__ import annotations

import json

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

from .auth import get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _parse_value(raw):
    """Settings values are stored as JSON like {"v": 10.0}. Extract .v."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
    if isinstance(parsed, dict) and "v" in parsed:
        return parsed["v"]
    return parsed


def _get_setting_num(db: Session, key: str, default: float) -> str:
    """Fetch a numeric setting, return formatted str (drops trailing .0)."""
    try:
        row = db.execute(
            text('SELECT value FROM settings WHERE "key" = :k LIMIT 1'),
            {"k": key},
        ).fetchone()
        raw = _parse_value(row[0]) if row else None
        val = float(raw) if raw is not None else float(default)
    except Exception:
        val = float(default)
    return str(int(val)) if val == int(val) else str(val)


def _get_setting_int(db: Session, key: str, default: int) -> int:
    """Kept for callers that need a plain int (e.g. farm cutoff in bytes)."""
    try:
        row = db.execute(
            text('SELECT value FROM settings WHERE "key" = :k LIMIT 1'),
            {"k": key},
        ).fetchone()
        raw = _parse_value(row[0]) if row else None
        if raw is None:
            return default
        return int(float(raw))
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
        "threshold_s": _get_setting_num(db, "scoring.threshold.s", 10),
        "threshold_a": _get_setting_num(db, "scoring.threshold.a", 7),
        "threshold_b": _get_setting_num(db, "scoring.threshold.b", 4),
        "threshold_c": _get_setting_num(db, "scoring.threshold.c", 1),
        "farm_threshold_m": farm_m,
    }
    return templates.TemplateResponse("manual.html", ctx)
