"""Farlight CoD Game Tools API client.

Talks to https://plat-cod-gametools-global-api.farlightgames.com/api/topn
on behalf of a user-provided JWT (RS256 Bearer token issued by Lilith's
`pup` auth server, TTL ~30d).

Two responsibilities:
1. Fetch the topN JSON for a given date window + server_id.
2. Decode the JWT locally (no signature verification — we don't have the
   public key) to extract `exp`, `iss`, `aud`, `account`, so callers can
   validate freshness and origin before storing it or before pulling.

The JSON→rows mapping (Farlight field names → HEADER_MAP field names used
by app/ingest.py) is centralised here so the rest of the codebase never
touches Farlight-specific vocabulary.
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://plat-cod-gametools-global-api.farlightgames.com"
TOPN_ENDPOINT = f"{API_BASE}/api/topn"
EXPECTED_ISS = "pup"
EXPECTED_AUD = "user"
EXPECTED_CLIENT_ID = "samo_game_tools_lglo"

# Farlight JSON field  →  ingest.HEADER_MAP field
FIELD_MAP: dict[str, str] = {
    "rank": "rank",
    "role_id": "character_id",
    "role_name": "current_name",
    "power": "power",
    "power_highest": "peak_power",
    "dead_num": "deaths_t45",
    "pvpmoney_num": "merits_total",
    "infantry_pvpmoney": "merits_infantry",
    "rider_pvpmoney": "merits_cavalry",
    "archer_pvpmoney": "merits_archers",
    "caster_pvpmoney": "merits_magic",
    "other_pvpmoney": "merits_other",
    "heal_num": "healing_t45",
    "donate_num": "alliance_donations",
    "build_time": "build_time",
    "destroy_time": "destruction_time",
    "bossbattle_cnt": "behemoth_victories",
    "gather_num": "harvest",
}


class FarlightError(Exception):
    """Base exception for Farlight client errors."""


class FarlightAuthError(FarlightError):
    """JWT rejected by the API (401/403) or malformed locally."""


class FarlightAPIError(FarlightError):
    """API returned non-zero business code, or 5xx, or network failure."""


# ============================================================================
# JWT decoding (local, no signature check — we don't have the public key)
# ============================================================================


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode_jwt_payload(jwt: str) -> dict[str, Any]:
    """Return the JWT payload as a dict. Raises FarlightAuthError if malformed.

    We do NOT verify the signature: Lilith's public key isn't documented.
    All we need locally is `exp` (to warn before rotation) and `iss/aud`
    (to reject obviously-wrong tokens pasted into the rotation endpoint).
    The API itself is the real validator.
    """
    parts = jwt.split(".")
    if len(parts) != 3:
        raise FarlightAuthError("Malformed JWT (expected 3 segments).")
    try:
        payload = json.loads(_b64url_decode(parts[1]))
    except (ValueError, json.JSONDecodeError) as e:
        raise FarlightAuthError(f"JWT payload not decodable: {e}") from e
    if not isinstance(payload, dict):
        raise FarlightAuthError("JWT payload is not a JSON object.")
    return payload


def validate_jwt_shape(payload: dict[str, Any]) -> None:
    """Sanity-check a decoded JWT payload before storing. Raises on failure."""
    if payload.get("iss") != EXPECTED_ISS:
        raise FarlightAuthError(
            f"JWT iss={payload.get('iss')!r} != expected {EXPECTED_ISS!r}."
        )
    if payload.get("aud") != EXPECTED_AUD:
        raise FarlightAuthError(
            f"JWT aud={payload.get('aud')!r} != expected {EXPECTED_AUD!r}."
        )
    if payload.get("client_id") != EXPECTED_CLIENT_ID:
        raise FarlightAuthError(
            f"JWT client_id={payload.get('client_id')!r} != expected "
            f"{EXPECTED_CLIENT_ID!r}."
        )
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        raise FarlightAuthError("JWT missing numeric 'exp'.")
    exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    if exp_dt <= now:
        raise FarlightAuthError(f"JWT already expired at {exp_dt.isoformat()}.")


def jwt_expiry(payload: dict[str, Any]) -> datetime:
    """Return the JWT expiration as a naive UTC datetime (matches DB style)."""
    exp = payload["exp"]
    return datetime.fromtimestamp(exp, tz=timezone.utc).replace(tzinfo=None)


# ============================================================================
# JSON → ingest rows
# ============================================================================


def map_api_rows(api_data: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Transform the raw API list into rows compatible with ingest.ingest_rows.

    Silently drops entries missing role_id / role_name (the two identity
    keys). Everything else is passed through as-is; ingest._parse_int
    handles missing / weird values downstream.
    """
    out: list[dict[str, Any]] = []
    for entry in api_data:
        if not isinstance(entry, dict):
            continue
        if entry.get("role_id") is None or entry.get("role_name") is None:
            continue
        row: dict[str, Any] = {}
        for api_field, ingest_field in FIELD_MAP.items():
            if api_field in entry:
                row[ingest_field] = entry[api_field]
        out.append(row)
    return out


# ============================================================================
# HTTP call
# ============================================================================


def fetch_topn(
    jwt: str,
    *,
    start_date: str,
    end_date: str,
    server_id: int,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """GET /api/topn and return the raw list of member dicts.

    Args:
        jwt: bearer token (no "Bearer " prefix).
        start_date / end_date: ISO date strings (YYYY-MM-DD).
        server_id: CoD kingdom number (e.g. 193).

    Raises:
        FarlightAuthError on 401/403 (token dead — trigger rotation).
        FarlightAPIError on 5xx, network failure, non-zero business code,
            or unexpected payload shape.
    """
    headers = {
        "Authorization": f"Bearer {jwt}",
        "Accept": "application/json",
        "Origin": "https://cod-game-tools.farlightgames.com",
        "Referer": "https://cod-game-tools.farlightgames.com/",
    }
    params = {
        "start_date": start_date,
        "end_date": end_date,
        "server_id": str(server_id),
    }

    logger.info(
        "farlight.fetch_topn: GET topn start=%s end=%s server=%d",
        start_date, end_date, server_id,
    )
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(TOPN_ENDPOINT, headers=headers, params=params)
    except httpx.RequestError as e:
        raise FarlightAPIError(f"network error: {e}") from e

    if resp.status_code in (401, 403):
        raise FarlightAuthError(
            f"API rejected JWT: HTTP {resp.status_code}. Rotate the token."
        )
    if resp.status_code >= 500:
        raise FarlightAPIError(
            f"API 5xx (HTTP {resp.status_code}): body={resp.text[:200]!r}"
        )
    if resp.status_code != 200:
        raise FarlightAPIError(
            f"unexpected HTTP {resp.status_code}: body={resp.text[:200]!r}"
        )

    try:
        body = resp.json()
    except ValueError as e:
        raise FarlightAPIError(f"non-JSON response: {e}") from e

    if not isinstance(body, dict):
        raise FarlightAPIError(f"unexpected body shape: {type(body).__name__}")
    code = body.get("code")
    if code != 0:
        raise FarlightAPIError(
            f"API business error: code={code!r} message={body.get('message')!r}"
        )
    data = body.get("data")
    if not isinstance(data, list):
        raise FarlightAPIError(
            f"expected 'data' as list, got {type(data).__name__}"
        )

    logger.info(
        "farlight.fetch_topn: got %d rows for start=%s end=%s",
        len(data), start_date, end_date,
    )
    return data
