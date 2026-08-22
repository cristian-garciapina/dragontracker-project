"""
Staff seasons management: upload exports + season lifecycle.

GET  /staff/seasons                 unified view (active season + snapshots + upload + wizard CTA)
POST /staff/seasons/upload          upload a cumulative export (ingest + recompute)
GET  /staff/seasons/new             wizard step 1: upload start snapshot
POST /staff/seasons/new/upload      wizard step 1 handler
GET  /staff/seasons/new/confirm     wizard step 2: confirm season metadata
POST /staff/seasons/new/confirm     wizard step 3: commit (close previous, create new)

Both 'staff' and 'owner' can access. Every state-changing action is
recorded in audit_log.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import get_db, require_staff
from .ingest import (
    _ingest_upload,
    _extract_dates_from_filename,
    find_conflicting_snapshot,
    ingest_rows,
    parse_xlsx_bytes,
)
from .models import AuditLog, Season, Snapshot, User
from .pending_uploads import (
    TOKEN_TTL_SECONDS,
    ConsumedUpload,
    TokenExpired,
    TokenInvalid,
    TokenNotFound,
    cancel_pending_upload,
    consume_pending_upload,
    create_pending_upload,
)
from .scoring import recompute_scores_for_active_season

router = APIRouter(tags=["staff-seasons"])

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# --- Helpers --------------------------------------------------------------
def _active_season(db: Session) -> Optional[Season]:
    return db.scalar(select(Season).where(Season.is_active == True))


def _list_snapshots(db: Session, season_id: int) -> list[Snapshot]:
    return list(db.scalars(
        select(Snapshot)
        .where(Snapshot.season_id == season_id)
        .order_by(Snapshot.date_end.desc(), Snapshot.ingested_at.desc())
    ).all())


def _audit(db: Session, user: User, action: str, target_type: str,
           target_id: str, old: Optional[dict], new: Optional[dict]):
    db.add(AuditLog(
        user=user.username,
        action=action,
        target_type=target_type,
        target_id=target_id,
        old_value=old,
        new_value=new,
        timestamp=datetime.utcnow(),
    ))


def _render(request: Request, db: Session, user: User,
            error: Optional[str] = None, success: Optional[str] = None,
            recompute_info: Optional[dict] = None):
    season = _active_season(db)
    snapshots = _list_snapshots(db, season.id) if season else []
    start_snap = (
        db.get(Snapshot, season.start_snapshot_id)
        if season and season.start_snapshot_id else None
    )
    latest_cum = next(
        (s for s in snapshots
         if season and s.date_start == season.start_date and s.date_end > season.start_date),
        None,
    )

    return templates.TemplateResponse(
        request=request,
        name="staff/seasons.html",
        context={
            "user": user,
            "kingdom": 193,
            "season": season,
            "snapshots": snapshots,
            "start_snap": start_snap,
            "latest_cum": latest_cum,
            "error": error,
            "success": success,
            "recompute": recompute_info,
        },
    )


# --- Main page -----------------------------------------------------------
@router.get("/staff/seasons", response_class=HTMLResponse)
async def seasons_view(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    return _render(request, db, user)


# --- Upload cumulative export -------------------------------------------
@router.post("/staff/seasons/upload")
async def seasons_upload(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    season = _active_season(db)
    if season is None:
        return _render(request, db, user, error="No active season. Start one first.")

    # Read + parse first so we can peek at (date_start, date_end) BEFORE
    # deciding whether to ingest directly or stash for confirm-replace.
    raw = await file.read()
    try:
        rows, date_start, date_end = parse_xlsx_bytes(raw, file.filename or "")
    except Exception as exc:
        return _render(request, db, user, error=f"Upload failed: {exc}")

    conflict = find_conflicting_snapshot(db, date_start, date_end)

    if conflict is not None:
        # Guard: cannot replace a snapshot that anchors a season. Bail
        # early with a clear message rather than staging a doomed pending.
        anchoring = db.scalar(
            select(Season.id).where(Season.start_snapshot_id == conflict.id)
        )
        if anchoring:
            return _render(
                request, db, user,
                error=(
                    f"Cannot replace snapshot #{conflict.id}: it anchors "
                    f"season {anchoring} as start_snapshot_id."
                ),
            )

        token = create_pending_upload(
            db,
            filename=file.filename or "upload.xlsx",
            content=raw,
            season_id=season.id,
            date_start=date_start,
            date_end=date_end,
            conflict_snapshot_id=conflict.id,
            uploaded_by=user.username,
        )
        db.commit()
        return RedirectResponse(
            url=f"/staff/seasons/upload/confirm-replace?token={token}",
            status_code=303,
        )

    # No conflict: go straight through the normal path.
    try:
        result = ingest_rows(
            db, rows,
            source_filename=file.filename,
            date_start=date_start,
            date_end=date_end,
            ingested_by=user.username,
            on_conflict="fail",
        )
    except Exception as exc:
        return _render(request, db, user, error=f"Upload failed: {exc}")

    _audit(db, user, "ingest", "snapshot", str(result["snapshot_id"]),
           None, {"file": result["filename"], "rows": result["rows"]})
    db.commit()

    recompute_info = recompute_scores_for_active_season(db)
    return _render(
        request, db, user,
        success=f"Ingested {result['rows']} rows from {result['filename']} and recomputed scores.",
        recompute_info=recompute_info,
    )


# --- Confirm-replace flow (B2.2) ----------------------------------------
def _render_confirm(request, db, user, *, conflict, pending, token, error=None):
    return templates.TemplateResponse(
        request=request,
        name="staff/seasons_upload_conflict.html",
        context={
            "user": user,
            "kingdom": 193,
            "conflict": {
                "id": conflict.id,
                "date_start": conflict.date_start,
                "date_end": conflict.date_end,
                "source_filename": conflict.source_filename,
                "ingested_by": conflict.ingested_by,
                "ingested_at": conflict.ingested_at.isoformat(sep=" ", timespec="seconds") if conflict.ingested_at else "",
                "row_count": conflict.row_count,
            },
            "pending": pending,
            "token": token,
            "error": error,
        },
    )


@router.get("/staff/seasons/upload/confirm-replace", response_class=HTMLResponse)
async def confirm_replace_get(
    request: Request,
    token: str,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Render the confirmation page. We peek at the pending upload via
    consume, but DO NOT delete it — so we run consume+cancel dance? No:
    we look up by hash directly without consuming."""
    from .pending_uploads import _hash_token, _serializer  # controlled internal use
    from itsdangerous import BadSignature, SignatureExpired
    ser = _serializer()
    try:
        raw = ser.loads(token, max_age=TOKEN_TTL_SECONDS)
    except SignatureExpired:
        return _render(request, db, user, error="Upload confirmation link has expired. Please re-upload.")
    except BadSignature:
        return _render(request, db, user, error="Upload confirmation link is invalid.")

    from .models import PendingUpload
    row = db.scalar(select(PendingUpload).where(PendingUpload.token_hash == _hash_token(raw)))
    if row is None:
        return _render(request, db, user, error="Pending upload not found. It may have been consumed or expired.")

    conflict = db.get(Snapshot, row.conflict_snapshot_id) if row.conflict_snapshot_id else None
    if conflict is None:
        # The conflicting snapshot was deleted while we were waiting.
        # Re-ingesting is now a normal insert; consume + do it.
        consumed = consume_pending_upload(db, token)
        rows, _ds, _de = parse_xlsx_bytes(consumed.content, consumed.filename)
        try:
            result = ingest_rows(
                db, rows,
                source_filename=consumed.filename,
                date_start=consumed.date_start,
                date_end=consumed.date_end,
                ingested_by=user.username,
                on_conflict="fail",
            )
        except Exception as exc:
            return _render(request, db, user, error=f"Ingest failed: {exc}")
        _audit(db, user, "ingest", "snapshot", str(result["snapshot_id"]),
               None, {"file": result["filename"], "rows": result["rows"], "via": "pending-upload-auto"})
        db.commit()
        recompute_info = recompute_scores_for_active_season(db)
        return _render(
            request, db, user,
            success=f"Ingested {result['rows']} rows from {result['filename']} (previous conflict resolved).",
            recompute_info=recompute_info,
        )

    age_seconds = (datetime.utcnow() - row.created_at).total_seconds()
    ttl_left = max(0, int((TOKEN_TTL_SECONDS - age_seconds) // 60))
    pending = {
        "filename": row.filename,
        "size_kb": f"{row.content_size / 1024:.1f}",
        "date_start": row.date_start,
        "date_end": row.date_end,
        "ttl_minutes": ttl_left,
    }
    return _render_confirm(request, db, user, conflict=conflict, pending=pending, token=token)


@router.post("/staff/seasons/upload/confirm-replace")
async def confirm_replace_post(
    request: Request,
    token: str = Form(...),
    action: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    if action == "cancel":
        cancel_pending_upload(db, token)
        db.commit()
        return _render(request, db, user, error="Upload cancelled. No changes made.")

    if action != "replace":
        return _render(request, db, user, error=f"Unknown action: {action!r}")

    try:
        consumed: ConsumedUpload = consume_pending_upload(db, token)
    except TokenExpired:
        db.commit()
        return _render(request, db, user, error="Upload confirmation token has expired. Please re-upload.")
    except TokenInvalid:
        return _render(request, db, user, error="Upload confirmation token is invalid.")
    except TokenNotFound:
        db.commit()
        return _render(request, db, user, error="Pending upload not found. It may have been consumed or expired.")

    try:
        rows, _ds, _de = parse_xlsx_bytes(consumed.content, consumed.filename)
        result = ingest_rows(
            db, rows,
            source_filename=consumed.filename,
            date_start=consumed.date_start,
            date_end=consumed.date_end,
            ingested_by=user.username,
            on_conflict="replace",
        )
    except Exception as exc:
        db.commit()  # persist the pending row deletion
        return _render(request, db, user, error=f"Replace failed: {exc}")

    _audit(
        db, user, "ingest", "snapshot", str(result["snapshot_id"]),
        {"replaced": result.get("replaced_snapshot")},
        {"file": result["filename"], "rows": result["rows"], "via": "confirm-replace"},
    )
    db.commit()

    # B2.5: audit notif to #audit-log (fire-and-forget).
    try:
        import asyncio
        from .bot_client import notify_snapshot_replaced
        season = _active_season(db)
        asyncio.create_task(notify_snapshot_replaced({
            "season_name": season.name if season else "?",
            "date_start": consumed.date_start.isoformat(),
            "date_end": consumed.date_end.isoformat(),
            "replaced": result.get("replaced_snapshot") or {},
            "new": {
                "id": result["snapshot_id"],
                "source_filename": result["filename"],
                "ingested_by": user.username,
                "rows": result["rows"],
            },
        }))
    except Exception:
        pass  # never let notif failure break the response

    recompute_info = recompute_scores_for_active_season(db)
    return _render(
        request, db, user,
        success=f"Replaced existing snapshot with {result['rows']} rows from {result['filename']}.",
        recompute_info=recompute_info,
    )


# --- Season lifecycle: close active season --------------------------------
@router.post("/staff/seasons/{season_id}/close")
async def close_season(
    season_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Close the given season. Sets is_active=False, closed_at=now,
    end_date=today. Triggers one final rescore to freeze the scores.

    After this, the nightly Farlight cron sees no active season and
    skips all pulls until a new season is created.
    """
    season = db.get(Season, season_id)
    if season is None or not season.is_active:
        return RedirectResponse(url="/staff/seasons", status_code=303)

    try:
        recompute_scores_for_active_season(db)
    except Exception:
        pass

    now = datetime.utcnow()
    today = now.date()

    old_state = {
        "is_active": True,
        "end_date": season.end_date.isoformat() if season.end_date else None,
        "closed_at": season.closed_at.isoformat() if season.closed_at else None,
    }
    season.is_active = False
    season.closed_at = now
    if season.end_date is None:
        season.end_date = today
    new_state = {
        "is_active": False,
        "end_date": season.end_date.isoformat(),
        "closed_at": season.closed_at.isoformat(),
    }
    _audit(db, user, "close", "season", str(season.id), old_state, new_state)
    db.commit()

    return RedirectResponse(url="/staff/seasons", status_code=303)


# --- Season lifecycle: start new season -----------------------------------
@router.get("/staff/seasons/new", response_class=HTMLResponse)
async def new_season_form(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    import re
    active = _active_season(db)

    default_name = ""
    last = db.scalar(select(Season).order_by(Season.id.desc()))
    if last is not None:
        m = re.match(r"^(.*?)(\d+)$", last.name)
        if m:
            default_name = f"{m.group(1)}{int(m.group(2)) + 1}"
        else:
            default_name = last.name

    return templates.TemplateResponse(
        request=request,
        name="staff/season_new.html",
        context={
            "user": user,
            "kingdom": 193,
            "error": None,
            "active_season": active,
            "default_name": default_name,
            "default_start_date": date.today().isoformat(),
        },
    )


@router.post("/staff/seasons/new")
async def new_season_create(
    request: Request,
    name: str = Form(...),
    start_date: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    active = _active_season(db)
    if active is not None:
        return templates.TemplateResponse(
            request=request,
            name="staff/season_new.html",
            context={
                "user": user, "kingdom": 193,
                "error": f"Season '{active.name}' is still active. Close it first.",
                "active_season": active,
                "default_name": name,
                "default_start_date": start_date,
            },
        )

    name_clean = name.strip()
    if not name_clean or len(name_clean) > 64:
        return templates.TemplateResponse(
            request=request,
            name="staff/season_new.html",
            context={
                "user": user, "kingdom": 193,
                "error": "Invalid season name (1-64 chars).",
                "active_season": None,
                "default_name": name,
                "default_start_date": start_date,
            },
        )

    try:
        start_d = date.fromisoformat(start_date.strip())
    except ValueError:
        return templates.TemplateResponse(
            request=request,
            name="staff/season_new.html",
            context={
                "user": user, "kingdom": 193,
                "error": "Invalid start date (expected YYYY-MM-DD).",
                "active_season": None,
                "default_name": name,
                "default_start_date": start_date,
            },
        )

    now = datetime.utcnow()
    new_season = Season(
        name=name_clean,
        start_date=start_d,
        is_active=True,
        start_snapshot_id=None,
        created_at=now,
    )
    db.add(new_season)
    db.flush()

    _audit(db, user, "create", "season", str(new_season.id),
           None, {"name": name_clean, "start_date": start_d.isoformat(),
                  "start_snapshot_id": None})
    db.commit()

    return RedirectResponse(url="/staff/seasons", status_code=303)
