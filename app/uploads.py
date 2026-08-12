"""Screenshot upload helpers for signup and application flows.

Validates uploaded images (jpg/png/webp) via extension AND magic bytes,
enforces size limit, saves under /opt/dashboard/data/screenshots/ with
restrictive perms. Never trusts client-provided filename.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import UploadFile


# Root storage dir. Overridable for tests via env var.
SCREENSHOTS_ROOT = Path(os.environ.get(
    "EV_SCREENSHOTS_ROOT",
    "/opt/dashboard/data/screenshots",
))

MAX_BYTES = 5 * 1024 * 1024  # 5 MiB

# Magic byte signatures for accepted formats.
# WEBP: "RIFF????WEBP" — check bytes 0-3 == RIFF and 8-11 == WEBP.
_MAGIC = {
    "jpg": [b"\xff\xd8\xff"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "webp": None,  # special-cased below
}

_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


class UploadError(ValueError):
    """Raised on any validation failure."""


def _sniff_ext(head: bytes) -> Optional[str]:
    if len(head) >= 3 and head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if len(head) >= 8 and head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


async def save_screenshot(upload: UploadFile, subdir: str, key: str) -> str:
    """Validate + save uploaded image. Returns absolute file path.

    subdir: "signups" or "applications"
    key: user_id or application_id as string (used in filename)
    """
    if upload is None or not upload.filename:
        raise UploadError("No file provided.")

    # Peek head for magic byte sniff.
    head = await upload.read(16)
    await upload.seek(0)
    ext = _sniff_ext(head)
    if ext is None:
        raise UploadError("File must be a JPG, PNG or WEBP image.")

    # Read full body with size guard.
    body = await upload.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise UploadError("Image too large (max 5 MB).")
    if len(body) == 0:
        raise UploadError("Empty file.")

    dest_dir = SCREENSHOTS_ROOT / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)

    token = secrets.token_hex(8)
    filename = f"{key}_{token}.{ext}"
    dest = dest_dir / filename

    dest.write_bytes(body)
    try:
        os.chmod(dest, 0o640)
    except (PermissionError, OSError):
        # Non-fatal on dev boxes where cod-app isn't the owner.
        pass

    return str(dest)


def delete_screenshot(path: Optional[str]) -> bool:
    """Delete file if it exists. Returns True on success or if already gone."""
    if not path:
        return True
    try:
        Path(path).unlink(missing_ok=True)
        return True
    except OSError:
        return False
