"""Performance scoring for Dragontracker.

Algorithm (simplified vs brief, aligned with operational practice 2026-06-25):

For each member in the active season:
    M/P ratio = (cumulative merits over season) / (start-of-season power) x 100

Grade by absolute thresholds (configurable in settings):
    S  if M/P >= scoring.threshold.s   (default 10%)
    A  if M/P >= scoring.threshold.a   (default 7%)
    B  if M/P >= scoring.threshold.b   (default 4%)
    C  if M/P >= scoring.threshold.c   (default 1%)
    D  otherwise

Status:
    KEEP   for S, A, B
    WATCH  for C  (potentially expellable)
    EXPEL  for D
    FARM   for accounts with start_power <= scoring.farm_account_power_threshold
           (default 15M). FARM accounts are excluded from the grade entirely.

The "start of season" snapshot is identified by Season.start_snapshot_id.
The "cumulative" snapshot is the most recent snapshot of the season whose
date_start equals Season.start_date.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from .models import Burn, SeasonFarmingWindow, Member, Score, Season, Setting, Snapshot, Stat


# ----------------------------------------------------------------------------
# Settings helpers
# ----------------------------------------------------------------------------
def _read_setting(session: Session, key: str, default):
    """Read a setting value (unwrap the {'v': ...} envelope)."""
    s = session.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if s is None:
        return default
    return s.value.get("v", default)


def _load_thresholds(session: Session) -> dict:
    return {
        "S": float(_read_setting(session, "scoring.threshold.s", 10.0)),
        "A": float(_read_setting(session, "scoring.threshold.a", 7.0)),
        "B": float(_read_setting(session, "scoring.threshold.b", 4.0)),
        "C": float(_read_setting(session, "scoring.threshold.c", 1.0)),
        "farm_power": int(
            _read_setting(session, "scoring.farm_account_power_threshold", 15_000_000)
        ),
    }


# ----------------------------------------------------------------------------
# Grade / status
# ----------------------------------------------------------------------------
def _grade_from_ratio(ratio: float, thr: dict) -> str:
    if ratio >= thr["S"]:
        return "S"
    if ratio >= thr["A"]:
        return "A"
    if ratio >= thr["B"]:
        return "B"
    if ratio >= thr["C"]:
        return "C"
    return "D"


def _status_from_grade(grade: str) -> str:
    if grade in ("S", "A", "B"):
        return "KEEP"
    if grade == "C":
        return "WATCH"
    return "EXPEL"


# ----------------------------------------------------------------------------
# Snapshot lookup
# ----------------------------------------------------------------------------
def _find_cumulative_snapshot(session: Session, season: Season) -> Optional[Snapshot]:
    """Most recent snapshot whose date_start equals the season start date.

    Such a snapshot represents the full-season cumulative export.
    """
    return session.execute(
        select(Snapshot)
        .where(
            and_(
                Snapshot.season_id == season.id,
                Snapshot.date_start == season.start_date,
                Snapshot.date_end != season.start_date,  # exclude the start itself
            )
        )
        .order_by(Snapshot.date_end.desc())
        .limit(1)
    ).scalar_one_or_none()


# ----------------------------------------------------------------------------
# Main entrypoint
# ----------------------------------------------------------------------------
def _primary_role_from_stat(stat) -> Optional[str]:
    """Return the unit type with the most merits, or None if all zero."""
    buckets = {
        "infantry": stat.merits_infantry or 0,
        "cavalry": stat.merits_cavalry or 0,
        "archers": stat.merits_archers or 0,
        "magic": stat.merits_magic or 0,
        "other": stat.merits_other or 0,
    }
    top = max(buckets.values())
    if top <= 0:
        return None
    return max(buckets, key=buckets.get)



def _load_farming_deductions(session, season_id: int, start_snap_id: int) -> dict[int, int]:
    """
    For each character, sum of merit deltas across all farming windows of the season.
    A window's delta = merits at upper-bound snapshot - merits at lower-bound snapshot.
    Windows with no available upper-bound snapshot are silently skipped.
    Returns {character_id: total_deduction}.
    """
    windows = session.execute(
        select(SeasonFarmingWindow)
        .where(SeasonFarmingWindow.season_id == season_id)
        .order_by(SeasonFarmingWindow.date_start)
    ).scalars().all()

    if not windows:
        return {}

    all_snaps = session.execute(
        select(Snapshot)
        .where(Snapshot.season_id == season_id)
        .where(Snapshot.date_start != Snapshot.date_end)
        .order_by(Snapshot.date_end)
    ).scalars().all()

    deductions: dict[int, int] = {}

    for w in windows:
        # Upper bound: first snapshot with date_end >= w.date_end
        upper = next((s for s in all_snaps if s.date_end >= w.date_end), None)
        if upper is None:
            continue  # can't compute this window yet

        # Lower bound: last snapshot with date_end < w.date_start
        lower_candidates = [s for s in all_snaps if s.date_end < w.date_start]
        lower = lower_candidates[-1] if lower_candidates else session.get(Snapshot, start_snap_id)
        if lower is None:
            continue

        lower_stats = {
            s.character_id: s.merits_total
            for s in session.execute(
                select(Stat).where(Stat.snapshot_id == lower.id)
            ).scalars()
        }
        upper_stats = {
            s.character_id: s.merits_total
            for s in session.execute(
                select(Stat).where(Stat.snapshot_id == upper.id)
            ).scalars()
        }

        for cid, upper_m in upper_stats.items():
            delta = upper_m - lower_stats.get(cid, 0)
            if delta > 0:
                deductions[cid] = deductions.get(cid, 0) + delta

    return deductions


def _load_burn_deductions(session, season_id: int) -> dict[int, int]:
    """Sum of merits_gained across all burns of the season, per character.

    Burns are treated as farming-like: their merits count as non-war effort
    and are deducted from merits_effective (used to compute M/P%).
    Returns {character_id: total_burn_merits}.
    """
    from sqlalchemy import func as _f
    rows = session.execute(
        select(Burn.character_id, _f.sum(Burn.merits_gained))
        .where(Burn.season_id == season_id)
        .where(Burn.merits_gained.isnot(None))
        .group_by(Burn.character_id)
    ).all()
    return {int(cid): int(total or 0) for cid, total in rows}


def recompute_scores_for_active_season(session: Session) -> dict:
    """Recompute scores for every member of the active season's roster.

    Returns a summary dict for caller logging/inspection.
    """
    # 1) Active season + its start snapshot
    season = session.execute(
        select(Season).where(Season.is_active.is_(True))
    ).scalar_one_or_none()
    if season is None:
        raise RuntimeError("No active season.")
    if season.start_snapshot_id is None:
        raise RuntimeError(
            f"Season {season.id} has no start_snapshot_id set. "
            "Set it via UPDATE seasons before computing scores."
        )

    start_snap_id = season.start_snapshot_id

    # 2) Cumulative snapshot for this season
    cum = _find_cumulative_snapshot(session, season)
    if cum is None:
        # Season just opened, no cumulative yet — this is a valid state,
        # not an error. Wipe any stale scores from a previous season and
        # return a no-op result.
        session.execute(
            __import__("sqlalchemy").text(
                "DELETE FROM scores WHERE season_id = :sid"
            ),
            {"sid": season.id},
        )
        session.commit()
        return {
            "status": "no_cumulative",
            "season_id": season.id,
            "season_name": season.name,
            "scored": 0,
            "farm": 0,
            "missing": 0,
        }

    # 3) Load thresholds from settings
    thr = _load_thresholds(session)

    # 3b) Load farming deductions for this season
    deductions = _load_farming_deductions(session, season.id, start_snap_id)
    burn_deductions = _load_burn_deductions(session, season.id)

    # 4) Load all start stats and cumulative stats, indexed by character_id
    start_stats = {
        s.character_id: s
        for s in session.execute(
            select(Stat).where(Stat.snapshot_id == start_snap_id)
        ).scalars()
    }
    cum_stats = {
        s.character_id: s
        for s in session.execute(
            select(Stat).where(Stat.snapshot_id == cum.id)
        ).scalars()
    }

    # 5) Wipe existing score rows for this (season, cumulative_snapshot) pair
    #    so a recompute does not pile up duplicates.
    session.execute(
        delete(Score).where(Score.season_id == season.id)
    )

    # 6) Compute one Score row per character that has BOTH start and cum stats
    now = datetime.utcnow()
    counts = {"S": 0, "A": 0, "B": 0, "C": 0, "D": 0, "FARM": 0, "MISSING": 0}
    scores: list[Score] = []

    for cid, start_stat in start_stats.items():
        cum_stat = cum_stats.get(cid)
        if cum_stat is None:
            # Player was in roster at start but left the top 800 by season end.
            counts["MISSING"] += 1
            continue

        is_farm = start_stat.power <= thr["farm_power"]
        merits = cum_stat.merits_total
        sp = start_stat.power
        if is_farm:
            grade = None
            status = "FARM"
            mp = 0.0
            counts["FARM"] += 1
        else:
            deduction = deductions.get(cid, 0)
            burn_ded = burn_deductions.get(cid, 0)
            merits_eff = max(0, merits - deduction - burn_ded)
            mp = (merits_eff / sp) * 100.0 if sp > 0 else 0.0
            grade = _grade_from_ratio(mp, thr)
            status = _status_from_grade(grade)
            counts[grade] += 1

        scores.append(
            Score(
                season_id=season.id,
                snapshot_id=cum.id,
                start_snapshot_id=start_snap_id,
                character_id=cid,
                start_power=sp,
                end_power=cum_stat.power,
                merits_cumulative=merits,
                merits_farmed_deduction=(deductions.get(cid, 0) if not is_farm else 0),
                merits_burn_deduction=(burn_deductions.get(cid, 0) if not is_farm else 0),
                merits_effective=(max(0, merits - deductions.get(cid, 0) - burn_deductions.get(cid, 0)) if not is_farm else merits),
                deaths_t45=cum_stat.deaths_t45,
                healing_t45=cum_stat.healing_t45,
                mp_ratio=mp,
                grade=grade,
                status=status,
                primary_role=_primary_role_from_stat(cum_stat),
                is_farm_account=is_farm,
                computed_at=now,
            )
        )

    session.add_all(scores)
    session.commit()

    return {
        "season_id": season.id,
        "start_snapshot_id": start_snap_id,
        "cumulative_snapshot_id": cum.id,
        "thresholds": thr,
        "scores_written": len(scores),
        "distribution": counts,
    }
