"""
Danger-zone endpoints for /staff/settings.

Contains the "Reset all alliance data" action: wipes every alliance-scoped
table, resets the alliances row and alliance/farlight settings to defaults,
purges non-owner users and stale sessions, empties on-disk artefacts
(screenshots, support attachments, CSP log), and optionally snapshots the
DB before doing all of the above.

Owner-only. Confirmation requires typing the current alliance name.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import require_owner
from app.db import get_db
from app.models import User

router = APIRouter(prefix="/staff/settings", tags=["staff-settings-danger"])


# Tables to wipe entirely (alliance-scoped data).
# Order matters: children before parents to respect FK constraints even
# when cascades are set, and to keep the intent obvious.
TABLES_TO_WIPE = [
    "stats",
    "scores",
    "burns",
    "player_notes",
    "event_participations",
    "event_rsvps",
    "member_events",
    "staff_events",
    "events",
    "applications",
    "pending_uploads",
    "snapshots",
    "season_farming_windows",
    "seasons",
    "members",
    "secrets",
    "password_reset_tokens",
]

# Default values for alliance.* and farlight.* settings.
# Must stay in sync with migration e5f6a7b8c9d0.
SETTINGS_DEFAULTS = {
    "alliance.name": "Your Alliance",
    "alliance.tag": "AL",
    "alliance.kingdom_id": 0,
    "alliance.server_id": 0,
    "alliance.tagline": "",
    "alliance.motto": "",
    "farlight.pull_enabled": False,
}

# On-disk directories/files to clear (contents only, not the folders themselves).
DATA_DIR = Path("/opt/dashboard/data")
DB_PATH = DATA_DIR / "eternal_vanguard.db"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
SUPPORT_ATTACHMENTS_DIR = DATA_DIR / "support-attachments"
CSP_LOG = DATA_DIR / "csp-reports.log"


def _backup_db() -> str:
    """Copy the live DB to a timestamped .before-reset-* sibling. Returns the backup path."""
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    dst = DATA_DIR / f"eternal_vanguard.db.before-reset-{ts}"
    shutil.copy2(DB_PATH, dst)
    return str(dst)


def _clear_directory(path: Path) -> int:
    """Remove all files/subdirs inside `path`, keep the folder itself. Returns count removed."""
    if not path.exists():
        return 0
    count = 0
    for entry in path.iterdir():
        try:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            count += 1
        except Exception:
            pass
    return count


def _truncate_file(path: Path) -> bool:
    """Truncate a log file if it exists. Returns True if truncated."""
    if not path.exists():
        return False
    try:
        path.write_text("")
        return True
    except Exception:
        return False


@router.post("/reset-alliance-data")
def reset_alliance_data(
    request: Request,
    confirm_name: str = Form(...),
    create_backup: str = Form(default=""),
    owner: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Wipe all alliance data. Owner-only. Requires typing current alliance name."""

    # 1. Verify typed name matches current alliance.name setting.
    row = db.execute(
        text("SELECT value FROM settings WHERE key = 'alliance.name'")
    ).first()
    if row is None:
        raise HTTPException(500, "Setting alliance.name not found. Migration missing?")
    current_name = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    if confirm_name.strip() != str(current_name).strip():
        raise HTTPException(
            400,
            f"Confirmation failed. You typed '{confirm_name}', expected '{current_name}'.",
        )

    # 2. Optional DB backup BEFORE any destructive action.
    backup_path = None
    if create_backup == "on":
        try:
            backup_path = _backup_db()
        except Exception as exc:
            raise HTTPException(500, f"Backup failed, aborting reset: {exc}")

    # 3. Wipe alliance-scoped tables inside a single transaction.
    try:
        for tbl in TABLES_TO_WIPE:
            db.execute(text(f"DELETE FROM {tbl}"))

        # Purge non-owner users.
        db.execute(text("DELETE FROM users WHERE role != 'owner'"))

        # Purge sessions except the caller's, so the owner stays logged in.
        db.execute(
            text("DELETE FROM user_sessions WHERE user_id != :uid"),
            {"uid": owner.id},
        )

        # Reset the alliances row #1 (kept as tenant root for future multi-tenant work).
        db.execute(
            text(
                "UPDATE alliances "
                "SET name = 'Your Alliance', tag = 'AL', kingdom_number = 0 "
                "WHERE id = 1"
            )
        )

        # Reset alliance.* and farlight.* settings to defaults.
        for key, value in SETTINGS_DEFAULTS.items():
            db.execute(
                text(
                    "UPDATE settings SET value = :val, updated_at = CURRENT_TIMESTAMP, "
                    "updated_by = :by WHERE key = :key"
                ),
                {"val": json.dumps(value), "by": owner.username, "key": key},
            )

        # Audit trail entry (kept intentionally so this event is traceable).
        db.execute(
            text(
                "INSERT INTO audit_log (actor, action, target, details, created_at) "
                "VALUES (:actor, 'reset_alliance_data', 'system', :details, CURRENT_TIMESTAMP)"
            ),
            {
                "actor": owner.username,
                "details": json.dumps(
                    {
                        "backup_created": backup_path,
                        "tables_wiped": TABLES_TO_WIPE,
                    }
                ),
            },
        )

        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, f"Reset failed mid-transaction, rolled back: {exc}")

    # 4. Clear on-disk artefacts (after DB commit — non-transactional).
    screenshots_removed = _clear_directory(SCREENSHOTS_DIR)
    attachments_removed = _clear_directory(SUPPORT_ATTACHMENTS_DIR)
    csp_truncated = _truncate_file(CSP_LOG)

    # 5. Redirect back to /staff/settings with a flash.
    return RedirectResponse(
        url="/staff/settings?reset=done",
        status_code=303,
    )
