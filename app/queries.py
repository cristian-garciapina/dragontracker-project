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


def list_seasons_for_picker(db: Session) -> list[Season]:
    """Seasons that have at least one score row, for the UI picker.
    Sorted: active first, then most recent start_date first."""
    scored_ids = db.execute(
        select(Score.season_id).distinct()
    ).scalars().all()
    if not scored_ids:
        return []
    return db.execute(
        select(Season)
        .where(Season.id.in_(scored_ids))
        .order_by(Season.is_active.desc(), Season.start_date.desc())
    ).scalars().all()


def resolve_season_or_active(
    db: Session, season_id: Optional[int]
) -> Optional[Season]:
    """Return the requested season if it exists, else the active season,
    else None. Invalid IDs silently fall back to active."""
    if season_id:
        season = db.get(Season, season_id)
        if season is not None:
            return season
    return get_active_season(db)


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
            base(func.sum(Score.merits_cumulative))
            .where(Score.is_farm_account == False)
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
        "primary_role": score.primary_role,
        "mp_ratio": score.mp_ratio,
        "merits_farmed_deduction": score.merits_farmed_deduction or 0,
        "merits_farmed_deduction_short": format_number_short(score.merits_farmed_deduction or 0),
        "merits_effective": score.merits_effective if score.merits_effective is not None else score.merits_cumulative,
        "merits_effective_short": format_number_short(score.merits_effective if score.merits_effective is not None else score.merits_cumulative),

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
        "merits_net": score.merits_effective or 0,
        "merits_net_short": format_number_short(score.merits_effective or 0),
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
def list_daily_snapshots_for_season(db: Session, season_id: int) -> list[Snapshot]:
    """Daily snapshots (date_start == date_end) for a season, oldest first.
    Excludes the season's start_snapshot_id — that one is 'start', not 'daily'."""
    season = db.get(Season, season_id)
    start_snap_id = season.start_snapshot_id if season else None
    stmt = (
        select(Snapshot)
        .where(Snapshot.season_id == season_id)
        .where(Snapshot.date_start == Snapshot.date_end)
        .order_by(Snapshot.date_end.asc())
    )
    rows = db.execute(stmt).scalars().all()
    return [s for s in rows if s.id != start_snap_id]


def _short(v: int | float | None) -> str:
    return format_number_short(v or 0)


def get_roster_window(
    db: Session,
    season_id: int,
    from_date,
    to_date,
    *,
    search: Optional[str] = None,
    role: Optional[str] = None,
    include_ex_members: bool = False,
    sort: str = "merits_total",
    order: str = "desc",
) -> list[dict]:
    """Aggregate activity across daily snapshots in [from_date, to_date].
    Additive columns SUM'd; power/peak_power taken from the latest daily in the window.
    No grade/status/M-P%: these belong to season-cumulative context only.
    """
    # Daily snapshot ids inside the window
    daily_ids = db.execute(
        select(Snapshot.id).where(
            and_(
                Snapshot.season_id == season_id,
                Snapshot.date_start == Snapshot.date_end,
                Snapshot.date_start >= from_date,
                Snapshot.date_end <= to_date,
            )
        )
    ).scalars().all()
    if not daily_ids:
        return []

    # Latest snapshot id in the window (for non-additive columns)
    latest_snap = db.scalar(
        select(Snapshot).where(Snapshot.id.in_(daily_ids))
        .order_by(Snapshot.date_end.desc()).limit(1)
    )

    # Aggregated additive stats per character
    agg_stmt = (
        select(
            Stat.character_id,
            func.sum(Stat.merits_total).label("merits_total"),
            func.sum(Stat.merits_infantry).label("merits_infantry"),
            func.sum(Stat.merits_cavalry).label("merits_cavalry"),
            func.sum(Stat.merits_archers).label("merits_archers"),
            func.sum(Stat.merits_magic).label("merits_magic"),
            func.sum(Stat.merits_other).label("merits_other"),
            func.sum(Stat.deaths_t45).label("deaths_t45"),
            func.sum(Stat.healing_t45).label("healing_t45"),
            func.sum(Stat.harvest).label("harvest"),
            func.sum(Stat.build_time).label("build_time"),
            func.sum(Stat.destruction_time).label("destruction_time"),
            func.sum(Stat.alliance_donations).label("alliance_donations"),
            func.sum(Stat.behemoth_victories).label("behemoth_victories"),
        )
        .where(Stat.snapshot_id.in_(daily_ids))
        .group_by(Stat.character_id)
    )

    # Latest-in-window power/peak per character
    latest_stmt = (
        select(Stat.character_id, Stat.power, Stat.peak_power)
        .where(Stat.snapshot_id == latest_snap.id)
    )
    latest_by_cid = {
        cid: (p, pp) for cid, p, pp in db.execute(latest_stmt).all()
    }

    # Join with Member for name + ex-member filter
    aggregates = db.execute(agg_stmt).all()

    rows: list[dict] = []
    for r in aggregates:
        member = db.get(Member, r.character_id)
        if member is None:
            continue
        if not include_ex_members and not member.in_alliance:
            continue
        if search:
            like = search.strip().lower()
            if like not in (member.current_name or "").lower() and like not in str(member.character_id):
                continue
        # Role filter uses the season's primary_role from Score if it exists,
        # otherwise skipped (window view has no grade context)
        p, pp = latest_by_cid.get(r.character_id, (None, None))
        rows.append({
            "character_id": r.character_id,
            "name": member.current_name,
            "grade": "—",
            "status": "—",
            "primary_role": None,
            "mp_ratio": None,
            "merits_total": r.merits_total or 0,
            "merits_total_short": _short(r.merits_total),
            "merits_net": r.merits_total or 0,
            "merits_net_short": _short(r.merits_total),
            "merits_farmed_deduction": 0,
            "merits_farmed_deduction_short": _short(0),
            "merits_infantry": r.merits_infantry or 0,
            "merits_infantry_short": _short(r.merits_infantry),
            "merits_cavalry": r.merits_cavalry or 0,
            "merits_cavalry_short": _short(r.merits_cavalry),
            "merits_archers": r.merits_archers or 0,
            "merits_archers_short": _short(r.merits_archers),
            "merits_magic": r.merits_magic or 0,
            "merits_magic_short": _short(r.merits_magic),
            "merits_other": r.merits_other or 0,
            "merits_other_short": _short(r.merits_other),
            "deaths_t45": r.deaths_t45 or 0,
            "deaths_t45_short": _short(r.deaths_t45),
            "healing_t45": r.healing_t45 or 0,
            "healing_t45_short": _short(r.healing_t45),
            "harvest": r.harvest or 0,
            "harvest_short": _short(r.harvest),
            "build_time": r.build_time or 0,
            "build_time_short": _short(r.build_time),
            "destruction_time": r.destruction_time or 0,
            "destruction_time_short": _short(r.destruction_time),
            "alliance_donations": r.alliance_donations or 0,
            "alliance_donations_short": _short(r.alliance_donations),
            "behemoth_victories": r.behemoth_victories or 0,
            "power": p or 0,
            "power_short": _short(p),
            "peak_power": pp or 0,
            "peak_power_short": _short(pp),
            "start_power": 0,
            "start_power_short": "—",
            "end_power": p or 0,
            "end_power_short": _short(p),
        })

    # Sort
    sort_map = {
        "name": lambda r: (r["name"] or "").lower(),
        "merits_total": lambda r: r["merits_total"],
        "merits_net": lambda r: r["merits_net"],
        "deaths": lambda r: r["deaths_t45"],
        "healing": lambda r: r["healing_t45"],
        "power": lambda r: r["power"],
        "peak_power": lambda r: r["peak_power"],
        "behemoth": lambda r: r["behemoth_victories"],
        "harvest": lambda r: r["harvest"],
        "donations": lambda r: r["alliance_donations"],
        "build_time": lambda r: r["build_time"],
        "destruction": lambda r: r["destruction_time"],
        "merits_infantry": lambda r: r["merits_infantry"],
        "merits_cavalry": lambda r: r["merits_cavalry"],
        "merits_archers": lambda r: r["merits_archers"],
        "merits_magic": lambda r: r["merits_magic"],
        "merits_other": lambda r: r["merits_other"],
    }
    key_fn = sort_map.get(sort, sort_map["merits_total"])
    rows.sort(key=key_fn, reverse=(order.lower() != "asc"))
    return rows


def compute_season_progress(season: Season) -> dict:
    today = date.today()
    start = season.start_date
    days_elapsed = max(1, (today - start).days + 1)
    # Cap day_current to end_date for closed/archived seasons.
    if season.end_date and today > season.end_date:
        days_elapsed = (season.end_date - start).days + 1

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
    "primary_role": Score.primary_role,
    "mp": Score.mp_ratio,
    "deduction": Score.merits_farmed_deduction,
    "merits_total": Stat.merits_total,
    "merits_net": Score.merits_effective,
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
    role: Optional[str] = None,
    status: Optional[str] = None,
    include_farms: bool = False,
    include_ex_members: bool = False,
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


    if not include_ex_members:
        stmt = stmt.where(Member.in_alliance == True)

    if search:
        like = f"%{search.strip()}%"
        # search matches name OR character_id
        if search.strip().isdigit():
            stmt = stmt.where(
                (Member.current_name.ilike(like))
                | (Member.character_id == int(search.strip()))
            )
        else:
            stmt = stmt.where(Member.current_name.ilike(like))

    if grade and grade.upper() in ("S", "A", "B", "C", "D"):
        stmt = stmt.where(Score.grade == grade.upper())

    if role and role.lower() in ("infantry", "cavalry", "archers", "magic", "other"):
        stmt = stmt.where(Score.primary_role == role.lower())

    if status and status.upper() in ("KEEP", "WATCH", "EXPEL"):
        stmt = stmt.where(Score.status == status.upper())

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


# ============================================================================
# ALLIANCE EVENTS
# ============================================================================

from sqlalchemy import delete as sa_delete
from .models import Event, EventParticipation

EVENT_TYPES = [
    ("mobilization", "Mobilisation"),
    ("war_threshold", "Seuil de la Guerre"),
    ("other", "Autre"),
]
EVENT_TYPE_LABELS = dict(EVENT_TYPES)


def list_alliance_members(db: Session) -> list[dict]:
    """All in-alliance members, id + name, ordered by name."""
    stmt = (
        select(Member.character_id, Member.current_name)
        .where(Member.in_alliance == True)  # noqa: E712
        .order_by(Member.current_name.asc())
    )
    return [{"character_id": cid, "name": name} for cid, name in db.execute(stmt).all()]


def list_events_for_season(db: Session, season_id: int) -> list[dict]:
    stmt = (
        select(
            Event,
            func.count(EventParticipation.id).label("participant_count"),
            func.coalesce(func.sum(EventParticipation.points), 0).label("total_points"),
        )
        .outerjoin(EventParticipation, EventParticipation.event_id == Event.id)
        .where(Event.season_id == season_id)
        .group_by(Event.id)
        .order_by(Event.date_start.desc(), Event.id.desc())
    )
    return [
        {"event": e, "participant_count": pc, "total_points": tp}
        for e, pc, tp in db.execute(stmt).all()
    ]


def get_event(db: Session, event_id: int) -> Optional[Event]:
    return db.get(Event, event_id)


def get_event_participations_ranked(db: Session, event_id: int) -> list[dict]:
    rank_expr = func.rank().over(
        partition_by=EventParticipation.event_id,
        order_by=EventParticipation.points.desc(),
    ).label("rank")
    stmt = (
        select(
            EventParticipation.character_id,
            Member.current_name,
            EventParticipation.points,
            EventParticipation.notes,
            rank_expr,
        )
        .join(Member, Member.character_id == EventParticipation.character_id)
        .where(EventParticipation.event_id == event_id, EventParticipation.is_eligible == True)  # noqa: E712
        .order_by(EventParticipation.points.desc(), Member.current_name.asc())
    )
    return [
        {"character_id": cid, "name": name, "points": pts, "notes": n, "rank": r}
        for cid, name, pts, n, r in db.execute(stmt).all()
    ]


def upsert_event_participations(
    db: Session, event_id: int, rows: list[tuple[int, int, Optional[str]]]
) -> tuple[int, int, int]:
    """rows: (character_id, points, notes). Returns (inserted, updated, skipped)."""
    known = set(db.scalars(select(Member.character_id)).all())
    existing = {
        p.character_id: p
        for p in db.scalars(
            select(EventParticipation).where(EventParticipation.event_id == event_id)
        )
    }
    inserted = updated = skipped = 0
    for character_id, points, notes in rows:
        if character_id not in known:
            skipped += 1
            continue
        if character_id in existing:
            p = existing[character_id]
            changed = False
            if p.points != points:
                p.points = points; changed = True
            if notes and p.notes != notes:
                p.notes = notes; changed = True
            if not p.is_eligible:
                p.is_eligible = True; changed = True
            if changed:
                updated += 1
        else:
            db.add(EventParticipation(
                event_id=event_id, character_id=character_id,
                points=points, notes=notes, is_eligible=True,
            ))
            inserted += 1
    db.commit()
    return inserted, updated, skipped


def delete_event(db: Session, event_id: int) -> bool:
    ev = db.get(Event, event_id)
    if not ev:
        return False
    db.delete(ev)
    db.commit()
    return True


def get_player_event_history(db: Session, character_id: int) -> list[dict]:
    rank_expr = func.rank().over(
        partition_by=EventParticipation.event_id,
        order_by=EventParticipation.points.desc(),
    ).label("rank")
    inner = (
        select(
            EventParticipation.event_id,
            EventParticipation.character_id,
            EventParticipation.points,
            rank_expr,
        )
        .where(EventParticipation.is_eligible == True)  # noqa: E712
        .subquery()
    )
    stmt = (
        select(
            Event.id, Event.name, Event.date_start, Event.date_end, Event.season_id,
            inner.c.points, inner.c.rank,
        )
        .join(inner, inner.c.event_id == Event.id)
        .where(inner.c.character_id == character_id)
        .order_by(Event.date_start.desc())
    )
    return [
        {"event_id": eid, "name": n, "date_start": ds, "date_end": de,
         "season_id": sid, "points": pt, "rank": r}
        for eid, n, ds, de, sid, pt, r in db.execute(stmt).all()
    ]


def list_scored_non_farms(db: Session, season_id: int) -> list[dict]:
    """All in-alliance members flagged non-farm in latest scoring for the season."""
    latest_snap = db.scalar(
        select(Score.snapshot_id)
        .where(Score.season_id == season_id)
        .order_by(Score.snapshot_id.desc())
        .limit(1)
    )
    if latest_snap is None:
        # Fallback : pas de scoring, on prend tous les in_alliance
        stmt = (
            select(Member.character_id, Member.current_name)
            .where(Member.in_alliance == True)  # noqa: E712
            .order_by(Member.current_name.asc())
        )
        return [{"character_id": cid, "name": n} for cid, n in db.execute(stmt).all()]
    stmt = (
        select(Member.character_id, Member.current_name)
        .join(Score, Score.character_id == Member.character_id)
        .where(
            Score.snapshot_id == latest_snap,
            Score.is_farm_account == False,  # noqa: E712
            Member.in_alliance == True,  # noqa: E712
        )
        .order_by(Member.current_name.asc())
    )
    return [{"character_id": cid, "name": n} for cid, n in db.execute(stmt).all()]


def prefill_event_eligibility(db: Session, event_id: int, season_id: int) -> int:
    """Called at event creation: insert one participation per non-farm, is_eligible=False, points=0.
    Staff will then check the actual participants. Returns count inserted."""
    existing = set(db.scalars(
        select(EventParticipation.character_id).where(EventParticipation.event_id == event_id)
    ).all())
    candidates = list_scored_non_farms(db, season_id)
    inserted = 0
    for m in candidates:
        if m["character_id"] in existing:
            continue
        db.add(EventParticipation(
            event_id=event_id,
            character_id=m["character_id"],
            points=0,
            is_eligible=False,
        ))
        inserted += 1
    db.commit()
    return inserted


def list_event_eligibility(db: Session, event_id: int) -> list[dict]:
    """All participations for the event with member name — used by the Manage modal.
    Includes non-eligible (unchecked) so the staff can flip them on."""
    stmt = (
        select(
            EventParticipation.character_id,
            Member.current_name,
            EventParticipation.is_eligible,
            EventParticipation.points,
        )
        .join(Member, Member.character_id == EventParticipation.character_id)
        .where(EventParticipation.event_id == event_id)
        .order_by(Member.current_name.asc())
    )
    return [
        {"character_id": cid, "name": n, "is_eligible": bool(e), "points": pts}
        for cid, n, e, pts in db.execute(stmt).all()
    ]


def set_event_eligibility(db: Session, event_id: int, eligible_ids: set[int]) -> tuple[int, int]:
    """Update eligibility for all participations of the event.
    eligible_ids = set of character_id to mark eligible=True. Others -> False.
    Returns (marked_eligible, marked_ineligible)."""
    parts = db.scalars(
        select(EventParticipation).where(EventParticipation.event_id == event_id)
    ).all()
    on = off = 0
    for p in parts:
        should_be = p.character_id in eligible_ids
        if p.is_eligible != should_be:
            p.is_eligible = should_be
            if should_be:
                on += 1
            else:
                off += 1
    db.commit()
    return on, off


def ensure_member_exists(db, character_id: int, name: str) -> bool:
    """Create a ghost Member (in_alliance=False) if the ID is unknown.
    Returns True if a new member was created.

    Uses the ORM (not a raw SQL INSERT) so the Python-side column
    defaults are applied — troop_tier, first_seen_at, last_seen_at
    are NOT NULL with no SQL default, so a raw INSERT
    that omits them fails with an IntegrityError.
    """
    if db.get(Member, character_id) is not None:
        return False
    db.add(Member(
        character_id=character_id,
        current_name=name.strip(),
        in_alliance=False,
    ))
    db.commit()
    return True


def add_manual_participation(db, event_id: int, character_id: int,
                             points: int, notes) -> str:
    """Upsert a participation with is_eligible=True. Returns 'created' or 'updated'."""
    from sqlalchemy import text
    existing = db.execute(
        text("SELECT id FROM event_participations "
             "WHERE event_id = :eid AND character_id = :cid"),
        {"eid": event_id, "cid": character_id},
    ).first()
    if existing:
        db.execute(
            text("UPDATE event_participations "
                 "SET points = :pts, notes = :notes, is_eligible = 1 "
                 "WHERE event_id = :eid AND character_id = :cid"),
            {"pts": points, "notes": notes, "eid": event_id, "cid": character_id},
        )
        db.commit()
        return "updated"
    db.execute(
        text("INSERT INTO event_participations "
             "(event_id, character_id, points, notes, is_eligible, recorded_at) "
             "VALUES (:eid, :cid, :pts, :notes, 1, CURRENT_TIMESTAMP)"),
        {"eid": event_id, "cid": character_id, "pts": points, "notes": notes},
    )
    db.commit()
    return "created"



def get_player_daily_merits(session, character_id: int, season_id: int) -> list[dict]:
    """Daily merit gains for a player, tagged with farming window membership."""
    from .models import SeasonFarmingWindow, Snapshot, Stat
    windows = session.execute(
        select(SeasonFarmingWindow)
        .where(SeasonFarmingWindow.season_id == season_id)
    ).scalars().all()
    def in_window(d):
        return any(w.date_start <= d <= w.date_end for w in windows)
    rows = session.execute(
        select(Snapshot.date_end, Stat.merits_total)
        .join(Stat, Stat.snapshot_id == Snapshot.id)
        .where(Stat.character_id == character_id)
        .where(Snapshot.season_id == season_id)
        .where(Snapshot.date_start == Snapshot.date_end)
        .order_by(Snapshot.date_end)
    ).all()
    return [
        {
            "date": r.date_end.isoformat(),
            "merits": int(r.merits_total or 0),
            "in_farming_window": in_window(r.date_end),
        }
        for r in rows
    ]
