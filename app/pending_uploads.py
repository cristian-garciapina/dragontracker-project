"""Helpers for the pending_uploads table (B2 conflict resolution flow).

Flow:
1. Staff uploads xlsx. Ingest detects conflict on (season_id, date_start, date_end).
2. create_pending_upload() stashes the blob and returns a signed token.
3. UI redirects staff to /staff/seasons/upload/confirm-replace?token=...
4. Staff confirms -> consume_pending_upload() returns the blob + calls ingest with on_conflict="replace".
5. Staff cancels or times out -> row is deleted lazily on next create/cleanup pass.

Design choices:
- Token stored HASHED (sha256). Raw signed token lives only in URL/cookie.
  Even if DB leaks, tokens can't be replayed against the site.
- itsdangerous.URLSafeTimedSerializer with a dedicated secret
  (EV_UPLOAD_TOKEN_SECRET) enforces integrity + TTL server-side.
  DB expiry check is a belt-and-suspenders extra guard.
- Blob in DB (LargeBinary) not /tmp: atomic, transactional, backed up
  nightly, no orphaned files.
- Cleanup: lazy on every create (cheap DELETE) + optional periodic call
  from an admin endpoint or cron later.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import PendingUpload

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

TOKEN_TTL_SECONDS = 30 * 60  # 30 minutes
SALT = "pending-upload-v1"
_SECRET_ENV = "EV_UPLOAD_TOKEN_SECRET"


class PendingUploadError(Exception):
    """Base error for pending-upload flow."""


class TokenInvalid(PendingUploadError):
    """Token was tampered with or the secret changed."""


class TokenExpired(PendingUploadError):
    """Token past its TTL. Staff must re-upload."""


class TokenNotFound(PendingUploadError):
    """Token signature is valid but the DB row was already consumed or pruned."""


@dataclass(frozen=True)
class ConsumedUpload:
    """What consume_pending_upload() returns after successful validation."""

    filename: str
    content: bytes
    season_id: int
    date_start: date
    date_end: date
    conflict_snapshot_id: Optional[int]
    uploaded_by: str


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _serializer() -> URLSafeTimedSerializer:
    secret = os.environ.get(_SECRET_ENV)
    if not secret:
        raise RuntimeError(
            f"{_SECRET_ENV} not set in environment. "
            "See /etc/eternal-vanguard.env on Aegis."
        )
    return URLSafeTimedSerializer(secret, salt=SALT)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _new_raw_token() -> str:
    # 32 bytes -> ~43 chars b64. Serialized+signed by itsdangerous on top.
    return secrets.token_urlsafe(32)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def create_pending_upload(
    session: Session,
    *,
    filename: str,
    content: bytes,
    season_id: int,
    date_start: date,
    date_end: date,
    conflict_snapshot_id: Optional[int],
    uploaded_by: str,
) -> str:
    """Stash the blob and return a signed token to hand back to the UI.

    Also lazily prunes any expired rows.
    """
    cleanup_expired(session)

    raw = _new_raw_token()
    token_hash = _hash_token(raw)

    row = PendingUpload(
        token_hash=token_hash,
        filename=filename,
        content=content,
        content_size=len(content),
        season_id=season_id,
        date_start=date_start,
        date_end=date_end,
        conflict_snapshot_id=conflict_snapshot_id,
        uploaded_by=uploaded_by,
        created_at=datetime.utcnow(),
    )
    session.add(row)
    session.flush()

    return _serializer().dumps(raw)


def consume_pending_upload(session: Session, signed_token: str) -> ConsumedUpload:
    """Validate token + return the blob. Deletes the row on success.

    Raises TokenInvalid, TokenExpired, or TokenNotFound. On any exception,
    caller should surface a friendly error to staff and ask them to re-upload.
    """
    ser = _serializer()
    try:
        raw = ser.loads(signed_token, max_age=TOKEN_TTL_SECONDS)
    except SignatureExpired as exc:
        raise TokenExpired("Upload confirmation token has expired.") from exc
    except BadSignature as exc:
        raise TokenInvalid("Upload confirmation token is invalid.") from exc

    token_hash = _hash_token(raw)
    row = session.execute(
        select(PendingUpload).where(PendingUpload.token_hash == token_hash)
    ).scalar_one_or_none()

    if row is None:
        raise TokenNotFound(
            "Pending upload not found. It may have been consumed or expired."
        )

    # Belt-and-suspenders: even if itsdangerous accepted it, refuse rows
    # older than TTL (clock skew, secret rotation edge cases).
    age = datetime.utcnow() - row.created_at
    if age > timedelta(seconds=TOKEN_TTL_SECONDS):
        session.delete(row)
        session.flush()
        raise TokenExpired("Pending upload has expired.")

    result = ConsumedUpload(
        filename=row.filename,
        content=bytes(row.content),
        season_id=row.season_id,
        date_start=row.date_start,
        date_end=row.date_end,
        conflict_snapshot_id=row.conflict_snapshot_id,
        uploaded_by=row.uploaded_by,
    )
    session.delete(row)
    session.flush()
    return result


def cancel_pending_upload(session: Session, signed_token: str) -> bool:
    """Explicit cancel. Returns True if a row was deleted. Never raises on
    invalid/expired tokens (cancel should always be idempotent)."""
    try:
        raw = _serializer().loads(signed_token, max_age=TOKEN_TTL_SECONDS)
    except (BadSignature, SignatureExpired):
        return False

    token_hash = _hash_token(raw)
    row = session.execute(
        select(PendingUpload).where(PendingUpload.token_hash == token_hash)
    ).scalar_one_or_none()
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def cleanup_expired(session: Session) -> int:
    """Delete rows past TTL. Returns number of deleted rows."""
    cutoff = datetime.utcnow() - timedelta(seconds=TOKEN_TTL_SECONDS)
    result = session.execute(
        delete(PendingUpload).where(PendingUpload.created_at < cutoff)
    )
    session.flush()
    return int(result.rowcount or 0)
