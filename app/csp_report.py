"""
CSP (Content Security Policy) violation reporting endpoint.

The browser POSTs a JSON report to /csp-report whenever a directive is
violated (either enforced or Report-Only mode). We log one JSON line per
violation to /opt/dashboard/data/csp-reports.log for later review.

Rate limited to 60/min per IP so a misbehaving page cannot flood the log.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response

from .ratelimit import client_ip, hit as rl_hit

log = logging.getLogger(__name__)

router = APIRouter(tags=["security"])

# Prod path; overridable for tests via env if needed later.
CSP_LOG_PATH = Path("/opt/dashboard/data/csp-reports.log")


def _write_report(payload: dict[str, Any], ip: str) -> None:
    """Append one JSON line. Failures logged but never raised."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ip": ip,
        "report": payload,
    }
    try:
        CSP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CSP_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("csp-report: failed to write log: %s", exc)


@router.post("/csp-report", include_in_schema=False)
async def csp_report(request: Request) -> Response:
    ip = client_ip(request)

    allowed, _ = rl_hit(f"csp-report:{ip}", max_per_window=60, window_seconds=60)
    if not allowed:
        # Silently drop, no 429 leak to the browser.
        return Response(status_code=204)

    try:
        payload = await request.json()
    except Exception:
        # Some browsers send application/csp-report with quirky shape;
        # fall back to raw text.
        try:
            raw = (await request.body()).decode("utf-8", errors="replace")
            payload = {"_raw": raw[:4096]}
        except Exception:
            payload = {"_error": "unreadable body"}

    _write_report(payload, ip)
    return Response(status_code=204)
