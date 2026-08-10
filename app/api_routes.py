from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import Score, Season, User

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
