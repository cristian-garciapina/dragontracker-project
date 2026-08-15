"""Client for communicating with the Discord bot's internal webhook server.

Fire-and-forget calls with short timeout: never raise, never block the caller.
The bot's webhook endpoint is a bonus path; if it fails, the grade_sync_task
running every 15 minutes will eventually assign @Verified anyway.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Optional

import httpx

log = logging.getLogger(__name__)

DEFAULT_BOT_URL = "http://127.0.0.1:8100"
TIMEOUT_SECONDS = 2.0


def _config() -> Optional[tuple[str, str]]:
    api_key = os.environ.get("EV_API_KEY")
    if not api_key:
        return None
    base_url = os.environ.get("BOT_WEBHOOK_URL", DEFAULT_BOT_URL).rstrip("/")
    return base_url, api_key


async def _post(path: str, discord_id: str) -> None:
    cfg = _config()
    if cfg is None:
        log.debug("bot_client: EV_API_KEY missing, skipping %s", path)
        return
    base_url, api_key = cfg
    url = f"{base_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"discord_id": str(discord_id)},
            )
            if resp.status_code >= 400:
                log.warning(
                    "bot_client %s -> %s: %s", path, resp.status_code, resp.text[:200]
                )
    except httpx.TimeoutException:
        log.warning("bot_client %s: timeout for discord_id=%s", path, discord_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("bot_client %s: %s", path, exc)


async def notify_verified_link(discord_id: Optional[str]) -> None:
    if not discord_id:
        return
    await _post("/internal/assign-verified", discord_id)


async def notify_verified_unlink(discord_id: Optional[str]) -> None:
    if not discord_id:
        return
    await _post("/internal/remove-verified", discord_id)


async def notify_new_application(payload: dict) -> None:
    """Notify the bot that a new /apply submission arrived."""
    cfg = _config()
    if cfg is None:
        return
    base_url, api_key = cfg
    url = f"{base_url}/internal/notify-application"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            if resp.status_code >= 400:
                log.warning("notify_new_application -> %s: %s", resp.status_code, resp.text[:200])
    except httpx.TimeoutException:
        log.warning("notify_new_application: timeout")
    except Exception as exc:  # noqa: BLE001
        log.warning("notify_new_application: %s", exc)


async def notify_new_signup(payload: dict) -> None:
    """Notify the bot that a new signup requires staff approval.
    Reads the screenshot file and includes it as base64 in the payload."""
    cfg = _config()
    if cfg is None:
        return
    base_url, api_key = cfg
    url = f"{base_url}/internal/notify-signup"

    # Read screenshot file and encode as base64 (small files, ~1-2 MB max)
    screenshot_path = payload.pop("screenshot_path", None)
    if screenshot_path:
        try:
            from pathlib import Path as _Path
            p = _Path(screenshot_path)
            if p.is_file():
                with p.open("rb") as f:
                    payload["screenshot_b64"] = base64.b64encode(f.read()).decode("ascii")
                payload["screenshot_filename"] = p.name
        except Exception as exc:  # noqa: BLE001
            log.warning("notify_new_signup: read screenshot failed: %s", exc)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            if resp.status_code >= 400:
                log.warning("notify_new_signup -> %s: %s", resp.status_code, resp.text[:200])
    except httpx.TimeoutException:
        log.warning("notify_new_signup: timeout")
    except Exception as exc:  # noqa: BLE001
        log.warning("notify_new_signup: %s", exc)


async def notify_new_event(payload: dict) -> None:
    """Notify the bot that a new event was created — bot posts embed in #events."""
    cfg = _config()
    if cfg is None:
        return
    base_url, api_key = cfg
    url = f"{base_url}/internal/notify-event"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            if resp.status_code >= 400:
                log.warning("notify_new_event -> %s: %s", resp.status_code, resp.text[:200])
    except httpx.TimeoutException:
        log.warning("notify_new_event: timeout")
    except Exception as exc:  # noqa: BLE001
        log.warning("notify_new_event: %s", exc)


async def notify_event_deleted(payload: dict) -> None:
    """Notify the bot that an event was deleted — bot removes the #events embed."""
    cfg = _config()
    if cfg is None:
        return
    base_url, api_key = cfg
    url = f"{base_url}/internal/delete-event"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            if resp.status_code >= 400:
                log.warning("notify_event_deleted -> %s: %s", resp.status_code, resp.text[:200])
    except httpx.TimeoutException:
        log.warning("notify_event_deleted: timeout")
    except Exception as exc:  # noqa: BLE001
        log.warning("notify_event_deleted: %s", exc)
