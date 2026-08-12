"""Audit log helper for staff actions on applications and registrations.

Writes to the staff_events table. Kept as thin helpers with no session
commit — the caller controls the transaction. If the caller commits, the
event is durable; if the caller rolls back, the event is discarded, which
is the correct behavior.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from .models import StaffEvent


def record_staff_event(
    session: Session,
    *,
    entity_type: str,
    entity_id: int,
    entity_ref: Optional[str],
    action: str,
    actor: str,
    detail: Optional[str] = None,
) -> StaffEvent:
    """Append a staff event. Does NOT commit — caller owns the transaction.

    entity_type: 'application' or 'registration'
    action: application -> accepted, migrated, rejected, deleted, status_change, notes_updated
            registration -> approved, rejected, deleted
    detail: role granted for 'approved', new status for 'status_change', else None
    actor:  'web:<username>' or 'discord:<display_name>'
    """
    if entity_type not in ("application", "registration"):
        raise ValueError(f"invalid entity_type: {entity_type!r}")
    evt = StaffEvent(
        entity_type=entity_type,
        entity_id=entity_id,
        entity_ref=(entity_ref or None),
        action=action,
        detail=(detail or None),
        actor=actor[:64],
        at=datetime.utcnow(),
    )
    session.add(evt)
    session.flush()
    return evt


def latest_event(
    session: Session,
    *,
    entity_type: str,
    entity_id: int,
) -> Optional[StaffEvent]:
    """Return the most recent event for (entity_type, entity_id) or None."""
    from sqlalchemy import select
    stmt = (
        select(StaffEvent)
        .where(StaffEvent.entity_type == entity_type)
        .where(StaffEvent.entity_id == entity_id)
        .order_by(StaffEvent.at.desc())
        .limit(1)
    )
    return session.scalar(stmt)
