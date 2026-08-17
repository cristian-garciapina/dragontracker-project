"""Fernet-encrypted key/value secrets store.

The master key `EV_SECRETS_KEY` must be set in the environment.
Callers get plaintext back from `get_secret`; they must not persist it
outside this module or log it.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from .models import Secret

logger = logging.getLogger(__name__)

_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = os.environ.get("EV_SECRETS_KEY")
        if not key:
            raise RuntimeError(
                "EV_SECRETS_KEY not set in environment. "
                "Generate with: python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'"
            )
        try:
            _fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except Exception as e:
            raise RuntimeError(f"Invalid EV_SECRETS_KEY: {e}") from e
    return _fernet


def get_secret(session: Session, key: str) -> Optional[str]:
    """Return the decrypted secret value, or None if key not found."""
    secret = session.get(Secret, key)
    if secret is None:
        return None
    try:
        return _get_fernet().decrypt(secret.value_encrypted).decode("utf-8")
    except InvalidToken:
        logger.error(
            "secrets_store: InvalidToken decrypting key=%r "
            "(wrong EV_SECRETS_KEY?)",
            key,
        )
        raise


def get_secret_meta(session: Session, key: str) -> Optional[dict]:
    """Return plaintext metadata (exp date, etc.) without decrypting the value."""
    secret = session.get(Secret, key)
    if secret is None:
        return None
    return {
        "key": secret.key,
        "expires_at": secret.expires_at,
        "metadata": secret.secret_metadata or {},
        "updated_at": secret.updated_at,
        "updated_by": secret.updated_by,
    }


def set_secret(
    session: Session,
    key: str,
    value: str,
    *,
    expires_at: Optional[datetime] = None,
    metadata: Optional[dict] = None,
    updated_by: Optional[str] = None,
) -> None:
    """Upsert a secret. `value` is encrypted before storage. Commits."""
    encrypted = _get_fernet().encrypt(value.encode("utf-8"))
    secret = session.get(Secret, key)
    if secret is None:
        secret = Secret(
            key=key,
            value_encrypted=encrypted,
            expires_at=expires_at,
            secret_metadata=metadata,
            updated_by=updated_by,
        )
        session.add(secret)
    else:
        secret.value_encrypted = encrypted
        secret.expires_at = expires_at
        secret.secret_metadata = metadata
        secret.updated_by = updated_by
    session.commit()


def delete_secret(session: Session, key: str) -> bool:
    """Delete a secret. Returns True if it existed."""
    secret = session.get(Secret, key)
    if secret is None:
        return False
    session.delete(secret)
    session.commit()
    return True
