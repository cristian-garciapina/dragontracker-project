"""
CSP middleware — generates a per-request nonce and attaches Content-Security-Policy
(currently in Report-Only mode) with the nonce in script-src.

The nonce is exposed via `request.state.csp_nonce` so templates can render it
into every inline <script> tag: <script nonce="{{ request.state.csp_nonce }}">.

Once every inline script carries the nonce and the report log stays empty for
24-48h, switch CSP_HEADER_NAME to "Content-Security-Policy" (drop the -Report-Only
suffix) to enforce the policy.
"""
from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CSP_HEADER_NAME = "Content-Security-Policy-Report-Only"

_POLICY_TEMPLATE = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{nonce}' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'; "
    "report-uri /csp-report"
)


class CSPNonceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        response: Response = await call_next(request)
        response.headers[CSP_HEADER_NAME] = _POLICY_TEMPLATE.format(nonce=nonce)
        return response
