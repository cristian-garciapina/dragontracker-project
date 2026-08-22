"""Farlight API auto-pull orchestrator.

Runs nightly via systemd timer (see systemd/farlight-pull.{service,timer}).
Also callable programmatically after a token rotation to smoke-test the
freshly stored JWT before returning success to the caller.

Per-run flow:
  1. Load JWT from encrypted secrets store (unless caller supplies one).
  2. Validate JWT locally (shape + not-yet-expired).
  3. Compute date window:
       - daily:      start = end = yesterday UTC
       - cumulative: start = active_season.start_date, end = yesterday UTC
     Cron runs at 03:15 UTC, after Farlight has frozen the previous day
     at 00:00 UTC, so "yesterday" is always a complete window.
  4. Fetch both windows from /api/topn.
  5. Ingest via ingest_rows(replace=True) — re-runs the same day are
     idempotent (previous snapshot with the same filename gets wiped
     and re-inserted). Never replaces a season's anchor snapshot
     (ingest_rows guards against that).
  6. Recompute scores for the active season.
  7. Return a summary dict for the CLI stdout / Discord bot embed.

The function never raises for expected failure modes — it encodes them
in `status` and `error` so bot/CLI can format uniformly. Unexpected
exceptions inside ingest/scoring bubble up as status="ingest_error".

CLI exit codes:
  0 ok                  — everything committed, scores recomputed
  1 jwt_missing/invalid — need rotation, DM Cristian
  2 api_auth_rejected   — token dead on the server side, DM Cristian
  3 api_error           — transient (5xx / network / business code)
  4 ingest_error        — data-shape problem, needs investigation
  5 no_active_season / season_not_started — config problem
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select

from .db import SessionLocal
from .farlight_client import (
    FarlightAPIError,
    FarlightAuthError,
    decode_jwt_payload,
    fetch_topn,
    jwt_expiry,
    map_api_rows,
    validate_jwt_shape,
)
from .ingest import find_conflicting_snapshot, ingest_rows
from .models import Alliance, Season
from .scoring import recompute_scores_for_active_season
from .secrets_store import get_secret

logger = logging.getLogger(__name__)

SECRET_KEY_JWT = "farlight_jwt"
DEFAULT_SERVER_ID = 193
WARN_EXPIRY_DAYS = 7
INGESTED_BY = "farlight-cron"


def _yesterday_utc() -> date:
    return (datetime.now(tz=timezone.utc) - timedelta(days=1)).date()


def _get_server_id(session) -> int:
    """Pull kingdom_number from the first Alliance row, fallback to 193."""
    alliance = session.execute(select(Alliance)).scalars().first()
    if alliance:
        return alliance.kingdom_number
    return DEFAULT_SERVER_ID


def _get_active_season(session) -> Season:
    season = session.execute(
        select(Season).where(Season.is_active.is_(True))
    ).scalar_one_or_none()
    if season is None:
        raise RuntimeError("No active season.")
    return season


def _maybe_attach_start_snapshot(session, season: Season) -> Optional[int]:
    """If the active season has no start_snapshot yet, try to attach the
    single-day snapshot that matches season.start_date. Returns the
    attached snapshot id if attachment happened, None otherwise.

    Called after each daily pull. Safe to call every night: no-op once
    the anchor is set.
    """
    if season.start_snapshot_id is not None:
        return None
    snap = session.execute(
        select(Snapshot).where(
            Snapshot.date_start == season.start_date,
            Snapshot.date_end == season.start_date,
        ).order_by(Snapshot.id.asc())
    ).scalars().first()
    if snap is None:
        return None
    season.start_snapshot_id = snap.id
    if snap.season_id != season.id:
        snap.season_id = season.id
    session.flush()
    logger.info(
        "farlight_pull: auto-attached snapshot #%d as start of season '%s' (%s)",
        snap.id, season.name, season.start_date.isoformat(),
    )
    return snap.id


def run_pull(session, *, jwt: Optional[str] = None, force: bool = False) -> dict[str, Any]:
    """Execute a full nightly pull. See module docstring for semantics."""
    summary: dict[str, Any] = {
        "started_at": datetime.utcnow().isoformat(),
        "status": "unknown",
    }

    # ---- Load + validate JWT --------------------------------------------
    if jwt is None:
        jwt = get_secret(session, SECRET_KEY_JWT)
    if not jwt:
        summary["status"] = "jwt_missing"
        summary["error"] = f"No secret {SECRET_KEY_JWT!r} in store."
        return summary
    try:
        payload = decode_jwt_payload(jwt)
        validate_jwt_shape(payload)
    except FarlightAuthError as e:
        summary["status"] = "jwt_invalid"
        summary["error"] = str(e)
        return summary

    exp = jwt_expiry(payload)
    days_left = (exp - datetime.utcnow()).days
    summary["jwt_account"] = payload.get("account")
    summary["jwt_expires_at"] = exp.isoformat()
    summary["jwt_expires_in_days"] = days_left
    summary["jwt_expiring_soon"] = days_left < WARN_EXPIRY_DAYS

    # ---- Determine dates ------------------------------------------------
    try:
        season = _get_active_season(session)
    except RuntimeError as e:
        summary["status"] = "no_active_season"
        summary["error"] = str(e)
        return summary
    server_id = _get_server_id(session)
    end = _yesterday_utc()
    cum_start = season.start_date
    if cum_start > end:
        summary["status"] = "season_not_started"
        summary["error"] = f"Season start {cum_start} > yesterday {end}."
        return summary
    summary.update({
        "server_id": server_id,
        "season_id": season.id,
        "season_name": season.name,
        "daily_date": end.isoformat(),
        "cum_start": cum_start.isoformat(),
        "cum_end": end.isoformat(),
    })

    # ---- Fetch daily + cumulative ---------------------------------------
    try:
        daily_data = fetch_topn(
            jwt,
            start_date=end.isoformat(),
            end_date=end.isoformat(),
            server_id=server_id,
        )
        cum_data = fetch_topn(
            jwt,
            start_date=cum_start.isoformat(),
            end_date=end.isoformat(),
            server_id=server_id,
        )
    except FarlightAuthError as e:
        summary["status"] = "api_auth_rejected"
        summary["error"] = str(e)
        return summary
    except FarlightAPIError as e:
        summary["status"] = "api_error"
        summary["error"] = str(e)
        return summary

    daily_rows = map_api_rows(daily_data)
    cum_rows = map_api_rows(cum_data)
    summary["daily_rows_fetched"] = len(daily_rows)
    summary["cum_rows_fetched"] = len(cum_rows)

    # ---- Ingest both windows with per-window manual-snapshot skip -------
    # B2.3: if a snapshot for the same (season, date_start, date_end)
    # already exists AND it was ingested by staff (not farlight-cron),
    # skip that window unless force=True. Prevents auto-pull from
    # silently wiping a manual upload.
    try:
        daily_report, daily_skip = _handle_window(
            session, daily_rows,
            source_filename=f"farlight_api_{server_id}_{end}_{end}.json",
            date_start=end, date_end=end,
            force=force,
        )
        cum_report, cum_skip = _handle_window(
            session, cum_rows,
            source_filename=f"farlight_api_{server_id}_{cum_start}_{end}.json",
            date_start=cum_start, date_end=end,
            force=force,
        )
        # Auto-attach start snapshot if season is still waiting for its anchor
        attached_id = _maybe_attach_start_snapshot(session, season)
        if attached_id is not None:
            summary["start_snapshot_auto_attached"] = attached_id

        score_report = None
        # Only rescore if we have an anchor (needed for M/P% computation)
        if (daily_report is not None or cum_report is not None) and season.start_snapshot_id is not None:
            score_report = recompute_scores_for_active_season(session)
    except Exception as e:
        session.rollback()
        summary["status"] = "ingest_error"
        summary["error"] = f"{type(e).__name__}: {e}"
        logger.exception("farlight_pull: ingest/scoring failed")
        return summary

    summary["daily"] = daily_report
    summary["daily_skipped_manual"] = daily_skip
    summary["cumulative"] = cum_report
    summary["cum_skipped_manual"] = cum_skip
    summary["scoring"] = score_report
    summary["force"] = force

    if daily_skip and cum_skip:
        summary["status"] = "skipped_manual"
    else:
        summary["status"] = "ok"
    summary["completed_at"] = datetime.utcnow().isoformat()
    return summary


def _handle_window(
    session,
    rows,
    *,
    source_filename: str,
    date_start: date,
    date_end: date,
    force: bool,
) -> tuple[Optional[dict], Optional[dict]]:
    """Ingest one window unless a manual (staff-ingested) snapshot exists
    for the same (season, date_start, date_end).

    Returns (ingest_report | None, skip_info | None). Exactly one of the
    two is non-None.
    """
    conflict = find_conflicting_snapshot(session, date_start, date_end)
    if conflict is not None and conflict.ingested_by != INGESTED_BY and not force:
        skip_info = {
            "conflict_snapshot_id": conflict.id,
            "conflict_source_filename": conflict.source_filename,
            "conflict_ingested_by": conflict.ingested_by,
            "conflict_ingested_at": conflict.ingested_at.isoformat() if conflict.ingested_at else None,
            "date_start": date_start.isoformat(),
            "date_end": date_end.isoformat(),
            "reason": "Manual staff snapshot present; auto-pull refuses to overwrite. Retry with force=true if intentional.",
        }
        logger.info(
            "farlight_pull: skipping window %s..%s (manual snapshot #%d by %s)",
            date_start, date_end, conflict.id, conflict.ingested_by,
        )
        return None, skip_info

    report = ingest_rows(
        session, rows,
        source_filename=source_filename,
        date_start=date_start, date_end=date_end,
        ingested_by=INGESTED_BY, on_conflict="replace",
    )
    return report, None




# ============================================================================
# Bot notification (fire-and-forget POST to the bot's internal HTTP server)
# ============================================================================

BOT_WEBHOOK_URL = "http://127.0.0.1:8100/internal/notify-farlight-pull"


def notify_bot(summary: dict) -> None:
    """POST the summary to the bot's internal webhook.

    Fire-and-forget: any error here MUST NOT change the pull outcome.
    The pull already committed to the DB; failing to notify Discord is
    a soft failure (logged, not raised).
    """
    import os
    api_key = os.environ.get("EV_API_KEY")
    if not api_key:
        logger.warning("notify_bot: EV_API_KEY missing, skipping notification")
        return
    try:
        import httpx
        with httpx.Client(timeout=3.0) as client:
            resp = client.post(
                BOT_WEBHOOK_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=summary,
            )
        if resp.status_code >= 400:
            logger.warning(
                "notify_bot: bot returned HTTP %d: %s",
                resp.status_code, resp.text[:200],
            )
        else:
            logger.info("notify_bot: posted (status=%s)", summary.get("status"))
    except Exception as e:  # noqa: BLE001
        logger.warning("notify_bot: POST failed: %s", e)


def main() -> int:
    """CLI entrypoint. Prints JSON summary to stdout, returns exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    with SessionLocal() as session:
        result = run_pull(session)
    notify_bot(result)
    print(json.dumps(result, default=str, indent=2))

    status = result.get("status")
    return {
        "ok": 0,
        "jwt_missing": 1,
        "jwt_invalid": 1,
        "api_auth_rejected": 2,
        "api_error": 3,
        "ingest_error": 4,
        "no_active_season": 5,
        "season_not_started": 5,
    }.get(status, 4)


if __name__ == "__main__":
    sys.exit(main())
