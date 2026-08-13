"""
In-memory rate limiter with per-key deque of hit timestamps.

Fine for single-process deployments (this site). If we ever move to
multi-worker/uvicorn or add a second app instance, migrate to Redis.

Behind Caddy reverse_proxy, real client IP comes via X-Forwarded-For.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Optional

from fastapi import Request

_hits: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def hit(key: str, max_per_window: int, window_seconds: int) -> tuple[bool, int]:
    """
    Register a hit for `key`. Returns (allowed, retry_after_seconds).

    - allowed=True: under the limit, hit recorded.
    - allowed=False: rate-limited; retry_after tells the caller when the
      oldest hit will fall out of the window.
    """
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and q[0] < now - window_seconds:
            q.popleft()
        if len(q) >= max_per_window:
            retry = int(window_seconds - (now - q[0])) + 1
            return False, max(retry, 1)
        q.append(now)
        return True, 0


def client_ip(request: Request) -> str:
    """
    Real client IP. Caddy sets X-Forwarded-For; take the first (leftmost)
    entry, which is the original client. Fall back to request.client.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def format_retry(seconds: int) -> str:
    """Human string for the 429 message. '2 minutes' / '45 seconds'."""
    if seconds >= 60:
        m = (seconds + 59) // 60
        return f"{m} minute{'s' if m > 1 else ''}"
    return f"{seconds} second{'s' if seconds > 1 else ''}"
