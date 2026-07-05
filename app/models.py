"""SQLAlchemy 2.0 models for Eternal Vanguard dashboard.

Schema design follows brief section 5.3:
- Core tables: alliances, members, snapshots, stats, seasons
- Annotation tables: member_events, burns
- Extensibility tables: axes, categories, event_types, settings
- Audit table: audit_log
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ============================================================================
# CORE DOMAIN TABLES
# ============================================================================


class Alliance(Base):
    __tablename__ = "alliances"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kingdom_number: Mapped[int] = mapped_column(Integer, nullable=False)
    tag: Mapped[Optional[str]] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Season(Base):
    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    start_snapshot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("snapshots.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    snapshots: Mapped[list["Snapshot"]] = relationship(
        back_populates="season",
        foreign_keys="Snapshot.season_id",
    )
    burns: Mapped[list["Burn"]] = relationship(back_populates="season")


class Member(Base):
    __tablename__ = "members"

    character_id: Mapped[int] = mapped_column(primary_key=True)
    current_name: Mapped[str] = mapped_column(String(64), nullable=False)
    alliance_id: Mapped[Optional[int]] = mapped_column(ForeignKey("alliances.id"))
    in_alliance: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    troop_tier: Mapped[str] = mapped_column(
        String(8), default="unknown", nullable=False
    )  # 'T4' | 'T5' | 'unknown'
    discord_id: Mapped[Optional[str]] = mapped_column(String(32))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    stats: Mapped[list["Stat"]] = relationship(back_populates="member")
    events: Mapped[list["MemberEvent"]] = relationship(back_populates="member")
    burns: Mapped[list["Burn"]] = relationship(back_populates="member")


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    date_start: Mapped[date] = mapped_column(Date, nullable=False)
    date_end: Mapped[date] = mapped_column(Date, nullable=False)
    source_filename: Mapped[str] = mapped_column(String(128), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ingested_by: Mapped[Optional[str]] = mapped_column(String(64))

    season: Mapped["Season"] = relationship(
        back_populates="snapshots",
        foreign_keys=[season_id],
    )
    stats: Mapped[list["Stat"]] = relationship(back_populates="snapshot")

    __table_args__ = (
        UniqueConstraint(
            "source_filename", "date_start", "date_end", name="uq_snapshot_source"
        ),
        Index("ix_snapshot_season_dates", "season_id", "date_start", "date_end"),
    )


class Stat(Base):
    __tablename__ = "stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("members.character_id"), nullable=False
    )
    rank: Mapped[Optional[int]] = mapped_column(Integer)

    # Identity / power
    power: Mapped[int] = mapped_column(Integer, nullable=False)
    peak_power: Mapped[Optional[int]] = mapped_column(Integer)

    # PvP combat
    deaths_t45: Mapped[int] = mapped_column(Integer, default=0)
    destruction_time: Mapped[int] = mapped_column(Integer, default=0)

    # Merits (the central metric)
    merits_total: Mapped[int] = mapped_column(Integer, default=0)
    merits_infantry: Mapped[int] = mapped_column(Integer, default=0)
    merits_cavalry: Mapped[int] = mapped_column(Integer, default=0)
    merits_archers: Mapped[int] = mapped_column(Integer, default=0)
    merits_magic: Mapped[int] = mapped_column(Integer, default=0)
    merits_other: Mapped[int] = mapped_column(Integer, default=0)

    # Support
    healing_t45: Mapped[int] = mapped_column(Integer, default=0)

    # Economy
    harvest: Mapped[int] = mapped_column(Integer, default=0)
    build_time: Mapped[int] = mapped_column(Integer, default=0)

    # Alliance contribution
    alliance_donations: Mapped[int] = mapped_column(Integer, default=0)
    behemoth_victories: Mapped[int] = mapped_column(Integer, default=0)

    snapshot: Mapped["Snapshot"] = relationship(back_populates="stats")
    member: Mapped["Member"] = relationship(back_populates="stats")

    __table_args__ = (
        UniqueConstraint("snapshot_id", "character_id", name="uq_stat_snapshot_member"),
        Index("ix_stat_member_snapshot", "character_id", "snapshot_id"),
    )


# ============================================================================
# ANNOTATION TABLES
# ============================================================================


class MemberEvent(Base):
    """Generic event sourcing for staff annotations.

    Used for warnings, promotions, absences, recruitment, departure, etc.
    Burns have their own dedicated table because they have richer structure.
    """

    __tablename__ = "member_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("members.character_id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(
        ForeignKey("event_types.code"), nullable=False
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    detected_by: Mapped[Optional[str]] = mapped_column(String(64))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    event_metadata: Mapped[Optional[dict]] = mapped_column(JSON)
    validated: Mapped[bool] = mapped_column(Boolean, default=True)

    member: Mapped["Member"] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_event_member_date", "character_id", "event_date"),
    )


class Burn(Base):
    """Dedicated table for burn events (multiple per member per season possible).

    Burns are tracked separately from member_events because they carry
    structured power_before / power_after fields used for score compensation.
    """

    __tablename__ = "burns"

    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("members.character_id"), nullable=False
    )
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    burn_date: Mapped[date] = mapped_column(Date, nullable=False)
    power_before: Mapped[Optional[int]] = mapped_column(Integer)
    power_after: Mapped[Optional[int]] = mapped_column(Integer)
    target: Mapped[Optional[str]] = mapped_column(String(64))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    recorded_by: Mapped[Optional[str]] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    member: Mapped["Member"] = relationship(back_populates="burns")
    season: Mapped["Season"] = relationship(back_populates="burns")

    __table_args__ = (
        Index("ix_burn_member_season", "character_id", "season_id"),
    )


# ============================================================================
# EXTENSIBILITY TABLES (staff-editable without code changes)
# ============================================================================


class Axis(Base):
    """Scoring axis definition. Staff can enable/disable, reweight, rename."""

    __tablename__ = "axes"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    source_field: Mapped[str] = mapped_column(String(64), nullable=False)
    is_delta: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_core: Mapped[bool] = mapped_column(Boolean, default=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    color: Mapped[Optional[str]] = mapped_column(String(16))


class Category(Base):
    """S/A/B/C/D categorization. Thresholds editable by staff."""

    __tablename__ = "categories"

    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(32), nullable=False)
    min_score: Mapped[float] = mapped_column(Float, nullable=False)
    max_score: Mapped[float] = mapped_column(Float, nullable=False)
    color: Mapped[str] = mapped_column(String(16), nullable=False)
    icon: Mapped[Optional[str]] = mapped_column(String(32))
    display_order: Mapped[int] = mapped_column(Integer, default=0)


class EventType(Base):
    """Event type definitions for member_events. Staff can add new types."""

    __tablename__ = "event_types"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    icon: Mapped[Optional[str]] = mapped_column(String(32))
    color: Mapped[Optional[str]] = mapped_column(String(16))
    description: Mapped[Optional[str]] = mapped_column(Text)
    requires_date: Mapped[bool] = mapped_column(Boolean, default=True)
    affects_dormant: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    editable_by: Mapped[str] = mapped_column(String(16), default="staff")


class Setting(Base):
    """Generic key/value store for system-wide settings.

    Used for scoring weights, thresholds, time windows, exigence ratios, etc.
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    editable_by: Mapped[str] = mapped_column(String(16), default="staff")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    updated_by: Mapped[Optional[str]] = mapped_column(String(64))


# ============================================================================
# AUDIT
# ============================================================================


class AuditLog(Base):
    """Trace of every staff-side modification on settings/axes/categories/etc."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[Optional[dict]] = mapped_column(JSON)
    new_value: Mapped[Optional[dict]] = mapped_column(JSON)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_audit_target", "target_type", "target_id"),
        Index("ix_audit_timestamp", "timestamp"),
    )


# ============================================================================
# SCORES (computed by scoring.py, persisted for history)
# ============================================================================


class Score(Base):
    """Computed score for a member at a given snapshot.

    One row per (snapshot_id, character_id). Recomputed when settings change
    or a new snapshot is ingested. History is preserved across recomputes
    by inserting new rows with a fresh computed_at.
    """

    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id"), nullable=False
    )
    start_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("members.character_id"), nullable=False
    )

    # Raw inputs (denormalized for traceability)
    start_power: Mapped[int] = mapped_column(Integer, nullable=False)
    end_power: Mapped[int] = mapped_column(Integer, nullable=False)
    merits_cumulative: Mapped[int] = mapped_column(Integer, default=0)
    deaths_t45: Mapped[int] = mapped_column(Integer, default=0)
    healing_t45: Mapped[int] = mapped_column(Integer, default=0)

    # Computed
    mp_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    grade: Mapped[Optional[str]] = mapped_column(String(2))  # 'S'|'A'|'B'|'C'|'D' or NULL for farm
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    # 'KEEP' | 'WATCH' | 'EXPEL' | 'FARM'

    is_farm_account: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_score_season_snapshot", "season_id", "snapshot_id"),
        Index("ix_score_member_snapshot", "character_id", "snapshot_id"),
    )


# ============================================================================
# AUTH (users + server-side sessions)
# ============================================================================


class User(Base):
    """Authenticated account. Linked optionally to a Member via character_id."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)
    # 'staff' = R4/R5 with full UI access; 'member' = own-stats view.

    character_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("members.character_id")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Self-service registration metadata
    pending_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    submitted_in_game_name: Mapped[Optional[str]] = mapped_column(String(64))
    submitted_rank: Mapped[Optional[str]] = mapped_column(String(8))
    submitted_server: Mapped[Optional[int]] = mapped_column(Integer)
    submitted_alliance_tag: Mapped[Optional[str]] = mapped_column(String(16))

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSession(Base):
    """Server-side session record. The session_id is the value stored in the
    httpOnly cookie sent to the browser.
    """

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ip: Mapped[Optional[str]] = mapped_column(String(45))
    user_agent: Mapped[Optional[str]] = mapped_column(String(256))

    user: Mapped["User"] = relationship(back_populates="sessions")

    __table_args__ = (
        Index("ix_session_expires", "expires_at"),
    )



class Application(Base):
    """Recruitment application submitted via the public /apply form.

    Reviewed by staff via /staff/applications. Status starts at 'new'
    and is updated by staff (new | reviewing | accepted | rejected).
    """

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    in_game_name: Mapped[str] = mapped_column(String(64), nullable=False)
    player_id: Mapped[int] = mapped_column(Integer, nullable=False)
    current_alliance: Mapped[Optional[str]] = mapped_column(String(64))
    server: Mapped[int] = mapped_column(Integer, nullable=False)
    motivation: Mapped[str] = mapped_column(Text, nullable=False)
    discord_handle: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(16), default="new", nullable=False)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(64))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    reference: Mapped[Optional[str]] = mapped_column(String(20), unique=True)
    public_reason: Mapped[Optional[str]] = mapped_column(Text)
    status_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_app_status", "status"),
        Index("ix_app_created", "created_at"),
    )



class PlayerNote(Base):
    """Staff-only traceability note attached to a member.

    Multiple notes per member. Never shown to non-staff users.
    Table already exists in DB (created via SQL); this model matches
    that schema exactly.
    """

    __tablename__ = "player_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("members.character_id"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_player_notes_char", "character_id"),
        Index("ix_player_notes_created", "created_at"),
    )
