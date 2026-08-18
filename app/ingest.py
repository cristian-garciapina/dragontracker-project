"""Excel ingestion pipeline for Farlight K193 exports (17-column format).

Source: cod-game-tools.farlightgames.com/topn (official beta portal).

Two entrypoints share the same core:
- `_ingest_upload` — xlsx uploaded via the staff seasons wizard.
- `ingest_rows` — pure function called by the xlsx path AND by the
  Farlight API auto-pull (see app/farlight_pull.py).

Both funnel through `ingest_rows`, which handles snapshot dedup,
member upsert, stat insert, and in_alliance sync.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, datetime
from typing import Iterable, Literal, Mapping

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Member, Score, Season, Setting, Snapshot, Stat

logger = logging.getLogger(__name__)


HEADER_MAP = {
    "rang": "rank",
    "identifiant du personnage": "character_id",
    "nom du personnage": "current_name",
    "puissance actuelle": "power",
    "plus haute puissance historique": "peak_power",
    "morts (t4/t5)": "deaths_t45",
    "merites totaux": "merits_total",
    "infanterie uniquement": "merits_infantry",
    "cavalerie uniquement": "merits_cavalry",
    "tireurs d'elite uniquement": "merits_archers",
    "unites magiques uniquement": "merits_magic",
    "autres merites": "merits_other",
    "guerison (t4/t5)": "healing_t45",
    "dons de l'alliance": "alliance_donations",
    "temps de construction": "build_time",
    "temps de destruction": "destruction_time",
    "victoires lors de raids de behemoth": "behemoth_victories",
    "recolte": "harvest",
}

REQUIRED_FIELDS = {"character_id", "current_name", "power", "merits_total"}

_FILENAME_DATES_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[_-](\d{4}-\d{2}-\d{2})")


def _normalize(s) -> str:
    if s is None:
        return ""
    s = str(s).strip().lower()
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s)
    return s


def _parse_int(v) -> int:
    if v is None or v == "":
        return 0
    try:
        return int(float(str(v).replace(" ", "").replace(",", "")))
    except (ValueError, TypeError):
        return 0


def _extract_dates_from_filename(filename: str) -> tuple[date, date]:
    m = _FILENAME_DATES_RE.search(filename)
    if not m:
        today = date.today()
        return today, today
    return date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))


def _get_setting_bool(session: Session, key: str, default: bool) -> bool:
    setting = session.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if setting is None:
        return default
    return bool(setting.value.get("v", default))


def _get_active_season(session: Session) -> Season:
    season = session.execute(
        select(Season).where(Season.is_active.is_(True))
    ).scalar_one_or_none()
    if season is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No active season. Seed one via init_db.",
        )
    return season


def _detect_overlap(session: Session, season_id: int, date_start: date, date_end: date):
    candidates = session.execute(
        select(Snapshot).where(Snapshot.season_id == season_id)
    ).scalars().all()
    for c in candidates:
        if max(c.date_start, date_start) <= min(c.date_end, date_end):
            return c
    return None


# ============================================================================
# CORE: pure ingestion function
# ============================================================================


def ingest_rows(
    session: Session,
    rows: Iterable[Mapping[str, object]],
    *,
    source_filename: str,
    date_start: date,
    date_end: date,
    ingested_by: str,
    on_conflict: Literal["fail", "replace"] = "fail",
    replace: bool | None = None,
) -> dict:
    """Ingest a batch of already-parsed member rows into a new snapshot.

    Called by both the xlsx upload path and the Farlight API auto-pull.
    All heavy source-format parsing (Excel / JSON / whatever) happens
    upstream; this function only cares about a normalized list of dicts.

    Args:
        session: SQLAlchemy session. This function commits on success and
                 leaves rollback to the caller on failure.
        rows: iterable of dicts with keys matching HEADER_MAP values
              (character_id, current_name, power, merits_total, ...).
              Values may be int, str, None — coerced via _parse_int.
        source_filename: unique-ish tag for this batch. For xlsx uploads,
              the original filename. For API pulls, a synthetic name like
              "farlight_api_YYYY-MM-DD_YYYY-MM-DD.json".
        date_start / date_end: temporal window this batch covers.
              date_start == date_end → daily snapshot.
              date_start < date_end → cumulative snapshot (season-to-date).
        ingested_by: audit trail (username or "farlight-cron").
        replace: if True and a snapshot with the same
                 (source_filename, date_start, date_end) already exists,
                 wipe it (with its stats + scores) and re-insert. Used by
                 the nightly cron so re-runs are idempotent.
                 Never allowed for a season's start_snapshot_id — raises.

    Returns:
        dict with snapshot_id, filename, rows, dropped, replaced_previous.

    Raises:
        ValueError: dup snapshot (when replace=False), overlap detected,
                    or attempted replace of a season start snapshot.
        HTTPException: no active season.
    """
    rows = list(rows)  # materialize (we iterate twice)
    season = _get_active_season(session)

    # Backward compat: `replace=True` shim -> `on_conflict="replace"`.
    # New code should use on_conflict directly. Kept for callers not yet
    # migrated (e.g. older CLI scripts) to avoid a hard break.
    if replace is not None:
        import warnings
        warnings.warn(
            "ingest_rows: `replace=` is deprecated, use `on_conflict=` instead.",
            DeprecationWarning, stacklevel=2,
        )
        on_conflict = "replace" if replace else "fail"

    replaced_previous = False
    replaced_snapshot_meta: dict | None = None

    # Semantic conflict lookup: any snapshot for the SAME
    # (season, date_start, date_end) — regardless of filename — is the
    # same logical dataset. This is what lets a Farlight-API auto pull
    # supersede a stale manual xlsx (and vice versa via /confirm-replace).
    existing = session.scalar(
        select(Snapshot)
        .where(Snapshot.season_id == season.id)
        .where(Snapshot.date_start == date_start)
        .where(Snapshot.date_end == date_end)
    )
    if existing is not None:
        if on_conflict == "fail":
            raise ValueError(
                f"Snapshot already ingested (id={existing.id}, "
                f"source={existing.source_filename!r}, "
                f"ingested_by={existing.ingested_by!r})."
            )
        # on_conflict == "replace" — guard against wiping season anchors.
        anchoring = session.scalar(
            select(Season.id).where(Season.start_snapshot_id == existing.id)
        )
        if anchoring:
            raise ValueError(
                f"Cannot replace snapshot {existing.id}: "
                f"it anchors season {anchoring} as start_snapshot_id."
            )
        # Preserve metadata of the wiped snapshot for the return payload,
        # so callers can log/audit what was overwritten.
        replaced_snapshot_meta = {
            "id": existing.id,
            "source_filename": existing.source_filename,
            "ingested_by": existing.ingested_by,
            "ingested_at": existing.ingested_at.isoformat() if existing.ingested_at else None,
        }
        session.execute(delete(Score).where(Score.snapshot_id == existing.id))
        session.execute(delete(Stat).where(Stat.snapshot_id == existing.id))
        session.delete(existing)
        session.flush()
        replaced_previous = True
        logger.info(
            "Ingest %s: replaced snapshot id=%d (was %s by %s) for (%s..%s)",
            source_filename, existing.id, existing.source_filename,
            existing.ingested_by, date_start, date_end,
        )

    if date_start != date_end and _get_setting_bool(
        session, "ingest.reject_overlapping_periods", default=True
    ):
        overlap = _detect_overlap(session, season.id, date_start, date_end)
        if overlap:
            raise ValueError("Overlapping snapshot period detected.")

    snap = Snapshot(
        season_id=season.id,
        date_start=date_start,
        date_end=date_end,
        source_filename=source_filename,
        row_count=len(rows),
        ingested_at=datetime.utcnow(),
        ingested_by=ingested_by,
    )
    session.add(snap)
    session.flush()

    rows_inserted = 0
    skipped = 0
    present_ids: set[int] = set()
    now = datetime.utcnow()

    for row in rows:
        if not REQUIRED_FIELDS.issubset(row.keys()):
            skipped += 1
            continue
        try:
            cid = _parse_int(row["character_id"])
        except Exception:
            skipped += 1
            continue
        if not cid:
            skipped += 1
            continue

        name = str(row["current_name"])[:64]
        member = session.get(Member, cid)
        if member is None:
            member = Member(
                character_id=cid,
                current_name=name,
                in_alliance=True,
                last_seen_at=now,
            )
            session.add(member)
        else:
            member.current_name = name
            # Throttle last_seen_at writes to at most once per snapshot day.
            if member.last_seen_at is None or member.last_seen_at.date() < date_end:
                member.last_seen_at = now

        stat = Stat(
            snapshot_id=snap.id,
            character_id=cid,
            rank=_parse_int(row["rank"]) if row.get("rank") else None,
            power=_parse_int(row.get("power", 0)),
            peak_power=_parse_int(row["peak_power"]) if row.get("peak_power") else None,
            deaths_t45=_parse_int(row.get("deaths_t45", 0)),
            destruction_time=_parse_int(row.get("destruction_time", 0)),
            merits_total=_parse_int(row.get("merits_total", 0)),
            merits_infantry=_parse_int(row.get("merits_infantry", 0)),
            merits_cavalry=_parse_int(row.get("merits_cavalry", 0)),
            merits_archers=_parse_int(row.get("merits_archers", 0)),
            merits_magic=_parse_int(row.get("merits_magic", 0)),
            merits_other=_parse_int(row.get("merits_other", 0)),
            healing_t45=_parse_int(row.get("healing_t45", 0)),
            harvest=_parse_int(row.get("harvest", 0)),
            build_time=_parse_int(row.get("build_time", 0)),
            alliance_donations=_parse_int(row.get("alliance_donations", 0)),
            behemoth_victories=_parse_int(row.get("behemoth_victories", 0)),
        )
        session.add(stat)
        present_ids.add(cid)
        rows_inserted += 1

    # --- in_alliance sync (unchanged rule) -------------------------------
    # A cumulative export of the ACTIVE season lists every current member.
    # Members currently in_alliance=True but absent from this export have
    # left the alliance → flip to False. Guarded on non-empty rows.
    dropped: list[tuple[int, str]] = []
    is_cumulative = date_start != date_end
    is_active_season = season.is_active
    if present_ids and is_cumulative and is_active_season:
        stale = session.execute(
            select(Member).where(
                Member.in_alliance == True,  # noqa: E712
                Member.character_id.not_in(present_ids),
            )
        ).scalars().all()
        for m in stale:
            m.in_alliance = False
            dropped.append((m.character_id, m.current_name))
        if dropped:
            logger.info(
                "Ingest %s: %d member(s) absent from cumulative → in_alliance=False: %s",
                source_filename, len(dropped), dropped,
            )
    elif present_ids and not (is_cumulative and is_active_season):
        logger.info(
            "Ingest %s: skipping in_alliance sync (cumulative=%s, active_season=%s)",
            source_filename, is_cumulative, is_active_season,
        )

    session.commit()
    return {
        "snapshot_id": snap.id,
        "filename": source_filename,
        "rows": rows_inserted,
        "skipped": skipped,
        "dropped": len(dropped),
        "replaced_previous": replaced_previous,
        "replaced_snapshot": replaced_snapshot_meta,
        "is_cumulative": is_cumulative,
    }


# ============================================================================
# HELPERS: pure xlsx parser + conflict lookup (used by upload conflict flow)
# ============================================================================


def parse_xlsx_bytes(
    raw: bytes, filename: str
) -> tuple[list[dict], date, date]:
    """Pure xlsx -> (rows, date_start, date_end). No session, no I/O.

    Shared by the direct upload path and by the pending-upload consume
    path (B2.2 confirm-replace flow), so both parse the blob identically.
    """
    from io import BytesIO
    import pandas as pd

    if not filename:
        raise ValueError("Missing filename.")
    date_start, date_end = _extract_dates_from_filename(filename)

    df = pd.read_excel(BytesIO(raw))
    df.columns = [_normalize(c) for c in df.columns]

    unmapped = sorted({c for c in df.columns if c and c not in HEADER_MAP})
    if unmapped:
        logger.warning(
            "parse_xlsx_bytes %s: %d unmapped column(s) ignored: %s",
            filename, len(unmapped), unmapped,
        )

    rows: list[dict] = []
    for _, row in df.iterrows():
        d: dict = {}
        for col_value, field in HEADER_MAP.items():
            if col_value in row.index:
                d[field] = row[col_value]
        rows.append(d)

    return rows, date_start, date_end


def find_conflicting_snapshot(
    session: Session, date_start: date, date_end: date
) -> Snapshot | None:
    """Return the snapshot for (active_season, date_start, date_end) if any.

    This is the semantic dedup key: same season + same window = same
    logical dataset, regardless of source (xlsx vs Farlight API) or
    filename. Caller uses this to decide whether to stash the upload
    into pending_uploads instead of calling ingest_rows(on_conflict="fail").
    """
    season = _get_active_season(session)
    return session.scalar(
        select(Snapshot)
        .where(Snapshot.season_id == season.id)
        .where(Snapshot.date_start == date_start)
        .where(Snapshot.date_end == date_end)
    )


# ============================================================================
# XLSX UPLOAD WRAPPER (used by staff seasons upload wizard)
# ============================================================================


async def _ingest_upload(
    session,
    file,
    *,
    ingested_by: str,
    on_conflict: Literal["fail", "replace"] = "fail",
) -> dict:
    """Xlsx upload path. Reads the file bytes, delegates to parse_xlsx_bytes
    then ingest_rows. `on_conflict` is forwarded so the B2 confirm-replace
    flow can call this with "replace" once the staff confirms."""
    raw = await file.read()
    rows, date_start, date_end = parse_xlsx_bytes(raw, file.filename or "")
    return ingest_rows(
        session,
        rows,
        source_filename=file.filename,
        date_start=date_start,
        date_end=date_end,
        ingested_by=ingested_by,
        on_conflict=on_conflict,
    )
