"""Excel ingestion pipeline for Farlight K193 exports (17-column format).

Source: cod-game-tools.farlightgames.com/topn (official beta portal).

The portal lets users tick which columns to include in the export.
This parser is therefore tolerant: any subset of known columns is accepted,
provided the minimal identity columns are present (character_id, name, power,
merits_total).

Entrypoint: `_ingest_upload` — called by the staff seasons upload wizard
(seasons_routes.py). The former `/api/ingest` HTTP endpoint (openpyxl-based
pipeline B) was removed as unused.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Season, Setting, Snapshot

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


async def _ingest_upload(
    session,
    file,
    *,
    ingested_by: str,
) -> dict:
    """Reusable ingestion entrypoint used by the staff seasons upload wizard
    (seasons_routes.py). Returns a dict with snapshot_id, filename, rows.
    """
    from io import BytesIO
    import pandas as pd
    from sqlalchemy import select as _select
    from .models import Snapshot as _Snapshot, Stat as _Stat, Member as _Member

    raw = await file.read()
    if not file.filename:
        raise ValueError("Missing filename.")
    date_start, date_end = _extract_dates_from_filename(file.filename)

    season = _get_active_season(session)
    if season is None:
        raise ValueError("No active season.")

    existing = session.scalar(
        _select(_Snapshot)
        .where(_Snapshot.source_filename == file.filename)
        .where(_Snapshot.date_start == date_start)
        .where(_Snapshot.date_end == date_end)
    )
    if existing is not None:
        raise ValueError(f"Snapshot already ingested (id={existing.id}).")

    if date_start != date_end and _get_setting_bool(
        session, "ingest.reject_overlapping_periods", default=True
    ):
        if _detect_overlap(session, season.id, date_start, date_end):
            raise ValueError("Overlapping snapshot period detected.")

    df = pd.read_excel(BytesIO(raw))
    df.columns = [_normalize(c) for c in df.columns]

    snap = _Snapshot(
        season_id=season.id,
        date_start=date_start,
        date_end=date_end,
        source_filename=file.filename,
        row_count=len(df),
        ingested_at=datetime.utcnow(),
        ingested_by=ingested_by,
    )
    session.add(snap)
    session.flush()

    unmapped = sorted({c for c in df.columns if c and c not in HEADER_MAP})
    if unmapped:
        logger.warning(
            "Ingest(upload) %s: %d unmapped column(s) ignored: %s",
            file.filename, len(unmapped), unmapped,
        )

    rows_inserted = 0
    for _, row in df.iterrows():
        mapped = {}
        for col_value, field in HEADER_MAP.items():
            if col_value in row.index:
                mapped[field] = row[col_value]
        if not REQUIRED_FIELDS.issubset(mapped.keys()):
            continue

        try:
            cid = _parse_int(mapped["character_id"])
        except Exception:
            continue

        member = session.get(_Member, cid)
        if member is None:
            member = _Member(
                character_id=cid,
                current_name=str(mapped["current_name"])[:64],
                in_alliance=True,
            )
            session.add(member)
        else:
            member.current_name = str(mapped["current_name"])[:64]
            member.last_seen_at = datetime.utcnow()

        stat = _Stat(
            snapshot_id=snap.id,
            character_id=cid,
            rank=_parse_int(mapped.get("rank", 0)) if mapped.get("rank") else None,
            power=_parse_int(mapped.get("power", 0)),
            peak_power=_parse_int(mapped.get("peak_power", 0)) if mapped.get("peak_power") else None,
            deaths_t45=_parse_int(mapped.get("deaths_t45", 0)),
            destruction_time=_parse_int(mapped.get("destruction_time", 0)),
            merits_total=_parse_int(mapped.get("merits_total", 0)),
            merits_infantry=_parse_int(mapped.get("merits_infantry", 0)),
            merits_cavalry=_parse_int(mapped.get("merits_cavalry", 0)),
            merits_archers=_parse_int(mapped.get("merits_archers", 0)),
            merits_magic=_parse_int(mapped.get("merits_magic", 0)),
            merits_other=_parse_int(mapped.get("merits_other", 0)),
            healing_t45=_parse_int(mapped.get("healing_t45", 0)),
            harvest=_parse_int(mapped.get("harvest", 0)),
            build_time=_parse_int(mapped.get("build_time", 0)),
            alliance_donations=_parse_int(mapped.get("alliance_donations", 0)),
            behemoth_victories=_parse_int(mapped.get("behemoth_victories", 0)),
        )
        session.add(stat)
        rows_inserted += 1

    session.commit()
    return {
        "snapshot_id": snap.id,
        "filename": file.filename,
        "rows": rows_inserted,
    }
