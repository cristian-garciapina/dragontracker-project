"""
Read-side queries for the dashboard pages.

All functions take a SQLAlchemy session and return plain Python data
(ints, dicts, lists of dicts) so the templates have no SQLAlchemy
objects to dig into.
"""
from __future__ import annotations

import statistics
from datetime import date
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from .models import Member, Score, Season, Snapshot, Stat


# --- Season / snapshot resolution ----------------------------------------
def get_active_season(db: Session) -> Optional[Season]:
    return db.scalar(select(Season).where(Season.is_active == True))


def get_scoring_snapshot(db: Session, season: Season) -> Optional[Snapshot]:
    """The latest cumulative snapshot used for the current scores."""
    return db.scalar(
        select(Snapshot)
        .where(Snapshot.season_id == season.id)
        .where(Snapshot.date_start == season.start_date)
        .where(Snapshot.date_end > season.start_date)
        .order_by(Snapshot.date_end.desc())
        .limit(1)
    )


def has_any_scores(db: Session, snapshot_id: int) -> bool:
    n = db.scalar(select(func.count(Score.id)).where(Score.snapshot_id == snapshot_id))
    return (n or 0) > 0


# --- Aggregate KPIs ------------------------------------------------------
def get_grade_distribution(
    db: Session, season_id: int, snapshot_id: int
) -> dict[str, int]:
    rows = db.execute(
        select(Score.grade, func.count(Score.id))
        .where(Score.season_id == season_id)
        .where(Score.snapshot_id == snapshot_id)
        .where(Score.is_farm_account == False)
        .group_by(Score.grade)
    ).all()
    return {grade: count for grade, count in rows if grade is not None}


def get_dashboard_stats(db: Session, season_id: int, snapshot_id: int) -> dict:
    base = (
        lambda f: select(f)
        .where(Score.season_id == season_id)
        .where(Score.snapshot_id == snapshot_id)
    )

    count_scored = (
        db.scalar(base(func.count(Score.id)).where(Score.is_farm_account == False))
        or 0
    )
    count_farm = (
        db.scalar(base(func.count(Score.id)).where(Score.is_farm_account == True))
        or 0
    )
    total_merits = (
        db.scalar(
            base(func.sum(Score.merits_cumulative)).where(
                Score.is_farm_account == False
            )
        )
        or 0
    )
    mp_avg = db.scalar(
        base(func.avg(Score.mp_ratio)).where(Score.is_farm_account == False)
    )

    powers = db.scalars(
        select(Score.end_power)
        .where(Score.season_id == season_id)
        .where(Score.snapshot_id == snapshot_id)
        .where(Score.is_farm_account == False)
    ).all()
    median_power = int(statistics.median(powers)) if powers else 0

    return {
        "count_scored": count_scored,
        "count_farm": count_farm,
        "total_merits": int(total_merits),
        "total_merits_short": format_number_short(int(total_merits)),
        "mp_avg": float(mp_avg) if mp_avg is not None else 0.0,
        "median_power": median_power,
        "median_power_short": format_number_short(median_power),
    }


def get_missing_count(
    db: Session, season: Season, current_snapshot_id: int
) -> int:
    if season.start_snapshot_id is None:
        return 0
    current_chars = select(Stat.character_id).where(
        Stat.snapshot_id == current_snapshot_id
    )
    return (
        db.scalar(
            select(func.count(Stat.id))
            .where(Stat.snapshot_id == season.start_snapshot_id)
            .where(Stat.character_id.not_in(current_chars))
        )
        or 0
    )


# --- Lists ---------------------------------------------------------------
def get_top_performers(
    db: Session, season_id: int, snapshot_id: int, limit: int = 15
) -> list[dict]:
    """Top N non-farm members by M/P%. Joined with Member + Stat for the
    snapshot so the template has every metric available (not just the
    three used in the M/P% formula).
    """
    rows = db.execute(
        select(Score, Member, Stat)
        .join(Member, Score.character_id == Member.character_id)
        .join(
            Stat,
            and_(
                Stat.character_id == Score.character_id,
                Stat.snapshot_id == Score.snapshot_id,
            ),
        )
        .where(Score.season_id == season_id)
        .where(Score.snapshot_id == snapshot_id)
        .where(Score.is_farm_account == False)
        .order_by(Score.mp_ratio.desc())
        .limit(limit)
    ).all()

    return [_row_to_dict(score, member, stat) for score, member, stat in rows]


def _row_to_dict(score: Score, member: Member, stat: Stat) -> dict:
    """Project every visible metric. `*_short` variants are the formatted
    "1.5M" strings; raw integers are also exposed for sorting or future use.
    """
    return {
        # Identity
        "name": member.current_name,
        "start_power": score.start_power,
        "start_power_short": format_number_short(score.start_power),
        "end_power": score.end_power,
        "end_power_short": format_number_short(score.end_power),
        "character_id": member.character_id,

        # Scoring outputs
        "grade": score.grade or "—",
        "status": score.status or "—",
        "mp_ratio": score.mp_ratio,

        # Power
        "power": stat.power,
        "power_short": format_number_short(stat.power),
        "peak_power": stat.peak_power or 0,
        "peak_power_short": format_number_short(stat.peak_power or 0),

        # Combat
        "deaths_t45": stat.deaths_t45,
        "deaths_t45_short": format_number_short(stat.deaths_t45),
        "destruction_time": stat.destruction_time,
        "destruction_time_short": format_number_short(stat.destruction_time),

        # Merits (total + breakdown)
        "merits_total": stat.merits_total,
        "merits_total_short": format_number_short(stat.merits_total),
        "merits_infantry": stat.merits_infantry,
        "merits_infantry_short": format_number_short(stat.merits_infantry),
        "merits_cavalry": stat.merits_cavalry,
        "merits_cavalry_short": format_number_short(stat.merits_cavalry),
        "merits_archers": stat.merits_archers,
        "merits_archers_short": format_number_short(stat.merits_archers),
        "merits_magic": stat.merits_magic,
        "merits_magic_short": format_number_short(stat.merits_magic),
        "merits_other": stat.merits_other,
        "merits_other_short": format_number_short(stat.merits_other),

        # Support
        "healing_t45": stat.healing_t45,
        "healing_t45_short": format_number_short(stat.healing_t45),

        # Economy
        "harvest": stat.harvest,
        "harvest_short": format_number_short(stat.harvest),
        "build_time": stat.build_time,
        "build_time_short": format_number_short(stat.build_time),

        # Alliance contribution
        "alliance_donations": stat.alliance_donations,
        "alliance_donations_short": format_number_short(stat.alliance_donations),
        "behemoth_victories": stat.behemoth_victories,
    }


# --- Helpers -------------------------------------------------------------
def compute_season_progress(season: Season) -> dict:
    today = date.today()
    start = season.start_date
    days_elapsed = max(1, (today - start).days + 1)

    result = {
        "day_current": days_elapsed,
        "start_date": start.strftime("%Y-%m-%d"),
        "day_total": None,
        "days_remaining": None,
        "end_date": None,
        "progress_pct": None,
    }

    if season.end_date:
        days_total = (season.end_date - start).days + 1
        days_remaining = max(0, (season.end_date - today).days)
        result.update(
            {
                "day_total": days_total,
                "days_remaining": days_remaining,
                "end_date": season.end_date.strftime("%Y-%m-%d"),
                "progress_pct": min(100, int(100 * days_elapsed / days_total)),
            }
        )

    return result


def format_number_short(n: Optional[int]) -> str:
    """1_523_400 -> '1.5M'. None or 0 -> '0' / '—'."""
    if n is None:
        return "—"
    if n == 0:
        return "0"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


# --- Full roster (Excel-like view) ----------------------------------------
ROSTER_SORTABLE_COLUMNS: dict[str, object] = {
    # Map URL param -> SQL column. Whitelist for safety.
    "name": Member.current_name,
    "grade": Score.grade,
    "mp": Score.mp_ratio,
    "merits_total": Stat.merits_total,
    "power": Stat.power,
    "peak_power": Stat.peak_power,
    "deaths": Stat.deaths_t45,
    "healing": Stat.healing_t45,
    "destruction": Stat.destruction_time,
    "build_time": Stat.build_time,
    "behemoth": Stat.behemoth_victories,
    "harvest": Stat.harvest,
    "donations": Stat.alliance_donations,
    "merits_infantry": Stat.merits_infantry,
    "merits_cavalry": Stat.merits_cavalry,
    "merits_archers": Stat.merits_archers,
    "merits_magic": Stat.merits_magic,
    "merits_other": Stat.merits_other,
}


def get_full_roster(
    db: Session,
    season_id: int,
    snapshot_id: int,
    *,
    search: Optional[str] = None,
    grade: Optional[str] = None,
    include_farms: bool = False,
    sort: str = "mp",
    order: str = "desc",
) -> list[dict]:
    """Full roster query with search/filter/sort. Returns every member with
    a score for the given snapshot.

    - `search`: case-insensitive substring match on Member.current_name
    - `grade`: filter to a specific grade ('S'/'A'/'B'/'C'/'D'); None = all
    - `include_farms`: include farm accounts (default: exclude)
    - `sort` / `order`: column key from ROSTER_SORTABLE_COLUMNS + asc/desc
    """
    stmt = (
        select(Score, Member, Stat)
        .join(Member, Score.character_id == Member.character_id)
        .join(
            Stat,
            and_(
                Stat.character_id == Score.character_id,
                Stat.snapshot_id == Score.snapshot_id,
            ),
        )
        .where(Score.season_id == season_id)
        .where(Score.snapshot_id == snapshot_id)
    )

    if not include_farms:
        stmt = stmt.where(Score.is_farm_account == False)

    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(Member.current_name.ilike(like))

    if grade and grade.upper() in ("S", "A", "B", "C", "D"):
        stmt = stmt.where(Score.grade == grade.upper())

    sort_col = ROSTER_SORTABLE_COLUMNS.get(sort, Score.mp_ratio)
    if order.lower() == "asc":
        stmt = stmt.order_by(sort_col.asc().nulls_last())
    else:
        stmt = stmt.order_by(sort_col.desc().nulls_last())

    rows = db.execute(stmt).all()
    return [_row_to_dict(score, member, stat) for score, member, stat in rows]


def count_total_roster(db: Session, season_id: int, snapshot_id: int) -> int:
    """Total scored members (non-farm) for the snapshot."""
    return (
        db.scalar(
            select(func.count(Score.id))
            .where(Score.season_id == season_id)
            .where(Score.snapshot_id == snapshot_id)
            .where(Score.is_farm_account == False)
        )
        or 0
    )


# --- Farm accounts -------------------------------------------------------
def count_farms(db: Session, season_id: int, snapshot_id: int) -> int:
    return (
        db.scalar(
            select(func.count(Score.id))
            .where(Score.season_id == season_id)
            .where(Score.snapshot_id == snapshot_id)
            .where(Score.is_farm_account == True)
        )
        or 0
    )


FARMS_SORTABLE_COLUMNS: dict[str, object] = {
    "name": Member.current_name,
    "start_power": Score.start_power,
    "power": Stat.power,
    "behemoth": Stat.behemoth_victories,
    "donations": Stat.alliance_donations,
}


def get_farm_accounts(
    db: Session,
    season_id: int,
    snapshot_id: int,
    *,
    search: Optional[str] = None,
    sort: str = "start_power",
    order: str = "desc",
) -> list[dict]:
    """All farm accounts for the snapshot, with name search and sortable columns."""
    stmt = (
        select(Score, Member, Stat)
        .join(Member, Score.character_id == Member.character_id)
        .join(
            Stat,
            and_(
                Stat.character_id == Score.character_id,
                Stat.snapshot_id == Score.snapshot_id,
            ),
        )
        .where(Score.season_id == season_id)
        .where(Score.snapshot_id == snapshot_id)
        .where(Score.is_farm_account == True)
    )

    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(Member.current_name.ilike(like))

    sort_col = FARMS_SORTABLE_COLUMNS.get(sort, Score.start_power)
    if order.lower() == "asc":
        stmt = stmt.order_by(sort_col.asc().nulls_last())
    else:
        stmt = stmt.order_by(sort_col.desc().nulls_last())

    rows = db.execute(stmt).all()
    return [_row_to_dict(score, member, stat) for score, member, stat in rows]


# --- Top of the Vanguard (all S graders) ---------------------------------
def get_top_grade_s(
    db: Session, season_id: int, snapshot_id: int
) -> list[dict]:
    """All S-grade non-farm members for the snapshot, ordered by M/P% desc.

    Replaces the fixed-N top performers list on the dashboard: 'Top of the
    Vanguard' now means the elite tier (S grade), whatever its size is.
    """
    rows = db.execute(
        select(Score, Member, Stat)
        .join(Member, Score.character_id == Member.character_id)
        .join(
            Stat,
            and_(
                Stat.character_id == Score.character_id,
                Stat.snapshot_id == Score.snapshot_id,
            ),
        )
        .where(Score.season_id == season_id)
        .where(Score.snapshot_id == snapshot_id)
        .where(Score.is_farm_account == False)
        .where(Score.grade == "S")
        .order_by(Score.mp_ratio.desc())
    ).all()

    return [_row_to_dict(score, member, stat) for score, member, stat in rows]


# --- Settings management -------------------------------------------------
EDITABLE_SETTINGS = [
    {
        "key": "scoring.threshold.s",
        "label": "Threshold S",
        "kind": "float",
        "suffix": "%",
        "description": "Minimum M/P% for grade S (the elite).",
    },
    {
        "key": "scoring.threshold.a",
        "label": "Threshold A",
        "kind": "float",
        "suffix": "%",
        "description": "Minimum M/P% for grade A.",
    },
    {
        "key": "scoring.threshold.b",
        "label": "Threshold B",
        "kind": "float",
        "suffix": "%",
        "description": "Minimum M/P% for grade B.",
    },
    {
        "key": "scoring.threshold.c",
        "label": "Threshold C",
        "kind": "float",
        "suffix": "%",
        "description": "Minimum M/P% for grade C. Below this is D.",
    },
    {
        "key": "scoring.farm_account_power_threshold",
        "label": "Farm cutoff (start power)",
        "kind": "int",
        "suffix": "",
        "description": "Accounts with start power ≤ this value are flagged as farms and excluded from scoring.",
    },
]


def load_editable_settings(db: Session) -> list[dict]:
    """Read the editable settings + their current value from the DB."""
    from .models import Setting
    out = []
    for spec in EDITABLE_SETTINGS:
        row = db.get(Setting, spec["key"])
        value = None
        if row is not None and isinstance(row.value, dict):
            value = row.value.get("v")
        out.append({**spec, "current": value})
    return out



# --- Player notes -------------------------------------------------------
def get_character_ids_with_notes(db: Session) -> set[int]:
    """Return the set of character_ids that have at least one staff note.
    Used to render the 📝 badge on Roster and Dashboard for staff users."""
    from .models import PlayerNote
    rows = db.scalars(select(PlayerNote.character_id).distinct()).all()
    return set(rows)



# --- Burns --------------------------------------------------------------
def get_character_ids_burned_this_season(db: Session, season_id: int) -> dict[int, int]:
    """Return {character_id: burn_count} for the given season.
    Used to render the 🔥 badge on Roster."""
    from .models import Burn
    from sqlalchemy import func as _f
    rows = db.execute(
        select(Burn.character_id, _f.count(Burn.id))
        .where(Burn.season_id == season_id)
        .group_by(Burn.character_id)
    ).all()
    return {char_id: count for char_id, count in rows}


def get_burns_for_character(db: Session, character_id: int, season_id: int | None = None) -> list:
    """Return burns for a character, optionally filtered by season."""
    from .models import Burn
    stmt = select(Burn).where(Burn.character_id == character_id)
    if season_id is not None:
        stmt = stmt.where(Burn.season_id == season_id)
    return list(db.scalars(stmt.order_by(Burn.burn_date.desc(), Burn.recorded_at.desc())).all())
