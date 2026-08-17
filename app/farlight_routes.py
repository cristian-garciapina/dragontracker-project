"""Farlight admin API: JWT/pull status + on-demand pull trigger.

Consumed by the Discord bot (slash /farlight-status, /farlight-pull-now).
Bearer-auth via EV_API_KEY (same as other /api endpoints).
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .api_routes import require_api_key
from .db import SessionLocal, get_session
from .farlight_client import decode_jwt_payload, jwt_expiry, validate_jwt_shape
from .farlight_pull import (
    SECRET_KEY_JWT,
    WARN_EXPIRY_DAYS,
    notify_bot,
    run_pull,
)
from .models import Score, Snapshot
from .secrets_store import get_secret, get_secret_meta

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/farlight", tags=["farlight"])


def _jwt_status(session: Session) -> dict:
    """Best-effort JWT status. Never raises: fields are None if missing/invalid."""
    meta = get_secret_meta(session, SECRET_KEY_JWT)
    if meta is None:
        return {"present": False}
    jwt = get_secret(session, SECRET_KEY_JWT)
    out: dict = {"present": True, "updated_at": meta.get("updated_at"),
                 "updated_by": meta.get("updated_by")}
    if not jwt:
        out["valid_shape"] = False
        return out
    try:
        payload = decode_jwt_payload(jwt)
        validate_jwt_shape(payload)
        exp = jwt_expiry(payload)
        days_left = (exp - datetime.utcnow()).days
        out.update({
            "valid_shape": True,
            "account": payload.get("account"),
            "expires_at": exp,
            "expires_in_days": days_left,
            "expiring_soon": days_left < WARN_EXPIRY_DAYS,
        })
    except Exception as e:  # noqa: BLE001
        out["valid_shape"] = False
        out["error"] = str(e)
    return out


def _last_pull_status(session: Session) -> dict:
    """Look up the most recent snapshot ingested by farlight-cron."""
    last_daily = session.execute(
        select(Snapshot)
        .where(Snapshot.ingested_by == "farlight-cron")
        .where(Snapshot.date_start == Snapshot.date_end)
        .order_by(desc(Snapshot.ingested_at))
        .limit(1)
    ).scalar_one_or_none()
    last_cum = session.execute(
        select(Snapshot)
        .where(Snapshot.ingested_by == "farlight-cron")
        .where(Snapshot.date_start != Snapshot.date_end)
        .order_by(desc(Snapshot.ingested_at))
        .limit(1)
    ).scalar_one_or_none()

    result: dict = {}
    if last_daily:
        result["daily"] = {
            "snapshot_id": last_daily.id,
            "date": last_daily.date_end,
            "rows": last_daily.row_count,
            "ingested_at": last_daily.ingested_at,
        }
    if last_cum:
        result["cumulative"] = {
            "snapshot_id": last_cum.id,
            "start": last_cum.date_start,
            "end": last_cum.date_end,
            "rows": last_cum.row_count,
            "ingested_at": last_cum.ingested_at,
        }
        rows = session.execute(
            select(Score.grade, Score.status)
            .where(Score.snapshot_id == last_cum.id)
        ).all()
        distribution: dict[str, int] = {}
        for grade, st in rows:
            key = grade if grade is not None else st
            distribution[key] = distribution.get(key, 0) + 1
        result["distribution"] = distribution
        result["score_count"] = len(rows)
    return result


@router.get("/status", dependencies=[Depends(require_api_key)])
def farlight_status(session: Session = Depends(get_session)) -> dict:
    return {
        "jwt": _jwt_status(session),
        "last_pull": _last_pull_status(session),
    }


def _background_pull() -> None:
    """Runs in a FastAPI BackgroundTask. Independent Session, posts result
    to the bot via notify_bot() exactly like the cron does.
    """
    logger.info("pull-now: starting background pull")
    try:
        with SessionLocal() as session:
            result = run_pull(session)
    except Exception as e:  # noqa: BLE001
        logger.exception("pull-now: run_pull crashed")
        result = {
            "status": "ingest_error",
            "error": f"{type(e).__name__}: {e}",
            "started_at": datetime.utcnow().isoformat(),
        }
    try:
        notify_bot(result)
    except Exception:  # noqa: BLE001
        logger.exception("pull-now: notify_bot failed (soft)")
    logger.info("pull-now: done, status=%s", result.get("status"))


@router.post(
    "/pull-now",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
)
def farlight_pull_now(background_tasks: BackgroundTasks) -> dict:
    background_tasks.add_task(_background_pull)
    return {"status": "accepted", "message": "Pull queued; result will be posted to Discord."}
