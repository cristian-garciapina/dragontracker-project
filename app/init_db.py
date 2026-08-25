"""Initialize the SQLite database: create tables + seed default values.

Idempotent: safe to re-run. Existing data is preserved unless --reset is passed.

Usage (from /opt/dashboard/app):
    /opt/dashboard/venv/bin/python -m app.init_db        # create + seed if empty
    /opt/dashboard/venv/bin/python -m app.init_db --reset  # DROP ALL and recreate
"""
import sys
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import engine
from app.models import (
    Alliance,
    Axis,
    Base,
    Category,
    EventType,
    Season,
    Setting,
)


def seed_alliance(session: Session) -> None:
    """Insert a placeholder alliance row if the table is empty.

    Owner reconfigures name / kingdom_number / tag afterwards through
    /staff/settings. Kept as row #1 to serve as the tenant root."""
    existing = session.execute(select(Alliance)).scalars().first()
    if existing:
        return
    session.add(Alliance(name="My Alliance", kingdom_number=0, tag="ALLY"))


def seed_season(session: Session) -> None:
    """Create initial active season if no active season exists."""
    active = session.execute(
        select(Season).where(Season.is_active.is_(True))
    ).scalar_one_or_none()
    if active:
        return
    session.add(
        Season(
            name="Season 2026-S2",
            start_date=date(2026, 6, 1),
            is_active=True,
        )
    )


def seed_axes(session: Session) -> None:
    """Seed the 5 scoring axes with default weights from brief 5.4."""
    if session.execute(select(Axis)).first():
        return
    defaults = [
        ("merits_power_ratio", "Mérites / Puissance", "merits_total", False, 0.50, 1, "#FFD700"),
        ("pvp_engagement",     "Engagement PvP",      "deaths_t45",   False, 0.20, 2, "#DC143C"),
        ("support",            "Support",             "healing_t45",  False, 0.15, 3, "#4169E1"),
        ("construction",       "Construction",        "build_time",   False, 0.10, 4, "#8B4513"),
        ("pve_coop",           "PvE coopératif",      "behemoth_victories", False, 0.05, 5, "#9370DB"),
    ]
    for code, name, source, is_delta, weight, order, color in defaults:
        session.add(
            Axis(
                code=code,
                display_name=name,
                source_field=source,
                is_delta=is_delta,
                is_active=True,
                is_core=True,
                weight=weight,
                display_order=order,
                color=color,
            )
        )


def seed_categories(session: Session) -> None:
    """Seed S/A/B/C/D categories with colors from brief section 4."""
    if session.execute(select(Category)).first():
        return
    defaults = [
        ("S", "Platine", 80.0, 100.01, "#E5E4E2", "crown",  1),  # brilliant platinum with glow
        ("A", "Or",      65.0,  80.0,  "#FFD700", "medal",  2),
        ("B", "Argent",  50.0,  65.0,  "#C0C0C0", "medal",  3),
        ("C", "Bronze",  35.0,  50.0,  "#CD7F32", "medal",  4),
        ("D", "Noir",     0.0,  35.0,  "#1a1a1a", "circle", 5),
    ]
    for code, name, mn, mx, color, icon, order in defaults:
        session.add(
            Category(
                code=code,
                display_name=name,
                min_score=mn,
                max_score=mx,
                color=color,
                icon=icon,
                display_order=order,
            )
        )


def seed_event_types(session: Session) -> None:
    """Seed annotation event types."""
    if session.execute(select(EventType)).first():
        return
    defaults = [
        ("warning",     "Avertissement",    "warning",  "#FFA500", True,  False),
        ("absence",     "Absence déclarée", "shield",   "#4169E1", True,  True),
        ("promotion",   "Promotion",        "arrow-up", "#32CD32", True,  False),
        ("demotion",    "Rétrogradation",   "arrow-down","#DC143C", True,  False),
        ("recruitment", "Recrutement",      "user-plus","#32CD32", True,  False),
        ("departure",   "Départ",           "user-minus","#808080", True,  False),
        ("mvp",         "MVP événement",    "star",     "#FFD700", True,  False),
    ]
    for code, name, icon, color, req_date, aff_dormant in defaults:
        session.add(
            EventType(
                code=code,
                display_name=name,
                icon=icon,
                color=color,
                requires_date=req_date,
                affects_dormant=aff_dormant,
                is_active=True,
                editable_by="r4_r5",
            )
        )


def seed_settings(session: Session) -> None:
    """Seed system-wide settings."""
    if session.execute(select(Setting)).first():
        return
    defaults = [
        # Scoring
        ("scoring.exigence_merits_ratio_percent", 10.0, "float", "scoring",
         "Exigence Mérites/Power en % cumulée sur la saison"),
        ("scoring.forced_dormant_consecutive", 4, "int", "scoring",
         "Nombre de snapshots consécutifs à zéro pour Forced D"),
        ("scoring.burn_compensation_points", 0, "int", "scoring",
         "Bonus de score par burn validé (0 = pas de bonus)"),

        # Display
        ("display.public_mode", True, "bool", "display",
         "True = dashboard accessible aux visiteurs anonymes"),
        ("display.show_numeric_score_to_members", False, "bool", "display",
         "True = membres voient leur score 0-100, False = note S/A/B/C/D seulement"),

        # Ingestion
        ("ingest.reject_overlapping_periods", True, "bool", "ingestion",
         "True = rejet des plages qui chevauchent un snapshot existant"),
    ]
    for key, value, vtype, cat, desc in defaults:
        session.add(
            Setting(
                key=key,
                value={"v": value},  # wrap in dict because column is JSON
                value_type=vtype,
                category=cat,
                description=desc,
                editable_by="r5",
            )
        )


def main(reset: bool = False) -> None:
    if reset:
        print("[init_db] DROP ALL TABLES")
        Base.metadata.drop_all(engine)

    print("[init_db] CREATE ALL TABLES")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        seed_alliance(session)
        seed_season(session)
        seed_axes(session)
        seed_categories(session)
        seed_event_types(session)
        seed_settings(session)
        session.commit()
        print("[init_db] Seeds applied.")

    print("[init_db] Done.")


if __name__ == "__main__":
    main(reset="--reset" in sys.argv)
