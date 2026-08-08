"""
Discord OAuth2 login/link.

Flow:
  GET  /auth/discord/start     -> generate state, redirect to Discord OAuth
  GET  /auth/discord/callback  -> validate state, exchange code, act on user

Behavior on callback:
  - Already logged in: link discord_id to the current user's linked Member.
  - Anonymous + discord_id matches a Member: log in the User attached.
  - Anonymous + discord_id unknown: create a fresh external User + ghost
    Member, log them in, redirect to /apply.

Config: DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI
via environment.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import (
    COOKIE_NAME,
    SECURE_COOKIES,
    SESSION_LIFETIME,
    create_session,
    get_current_user,
    get_db,
)
from .models import Member, User

router = APIRouter(tags=["oauth-discord"])

CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get(
    "DISCORD_REDIRECT_URI",
    "https://eternal-vanguard.com/auth/discord/callback",
)
SCOPE = "identify email"

STATE_COOKIE = "discord_oauth_state"
NEXT_COOKIE = "discord_oauth_next"
PENDING_COOKIE = "discord_pending_id"


def _oauth_configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


def _build_auth_url(state: str) -> str:
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        "prompt": "consent",
    }
    return f"https://discord.com/api/oauth2/authorize?{urlencode(params)}"


def _set_session_cookie(resp: RedirectResponse, session_id: str) -> None:
    resp.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=SECURE_COOKIES,
        samesite="lax",
        path="/",
    )


@router.get("/auth/discord/start")
async def discord_start(request: Request, next: str = "/dashboard"):
    if not _oauth_configured():
        return RedirectResponse(
            url="/login?error=discord_not_configured",
            status_code=303,
        )
    state = secrets.token_urlsafe(24)
    resp = RedirectResponse(url=_build_auth_url(state), status_code=303)
    resp.set_cookie(
        STATE_COOKIE, state, max_age=600, httponly=True,
        secure=SECURE_COOKIES, samesite="lax", path="/",
    )
    resp.set_cookie(
        NEXT_COOKIE, next, max_age=600, httponly=True,
        secure=SECURE_COOKIES, samesite="lax", path="/",
    )
    return resp


@router.get("/auth/discord/callback")
async def discord_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: Session = Depends(get_db),
    current: Optional[User] = Depends(get_current_user),
):
    if error:
        return RedirectResponse(url="/login?error=discord_denied", status_code=303)
    if not _oauth_configured():
        return RedirectResponse(url="/login?error=discord_not_configured", status_code=303)

    cookie_state = request.cookies.get(STATE_COOKIE, "")
    next_url = request.cookies.get(NEXT_COOKIE, "/dashboard")
    if not code or not state or state != cookie_state:
        return RedirectResponse(url="/login?error=discord_state", status_code=303)

    # 1. Exchange code -> access token
    async with httpx.AsyncClient(timeout=10.0) as client:
        token_resp = await client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_resp.status_code != 200:
            return RedirectResponse(url="/login?error=discord_token", status_code=303)
        access_token = token_resp.json().get("access_token", "")
        if not access_token:
            return RedirectResponse(url="/login?error=discord_token", status_code=303)

        # 2. Get Discord user id
        me = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if me.status_code != 200:
            return RedirectResponse(url="/login?error=discord_me", status_code=303)
        me_data = me.json()
        discord_user_id = str(me_data.get("id", ""))
        discord_username = me_data.get("username", "")
        discord_email = (me_data.get("email") or "").lower().strip()
        if not discord_user_id:
            return RedirectResponse(url="/login?error=discord_me", status_code=303)

    # 3. Branch on session state
    # Case A: already logged in -> link discord_id to the current user
    if current is not None:
        # Refuse if another user already claims this discord_id
        conflict = db.scalar(
            select(User).where(User.discord_id == discord_user_id)
        )
        if conflict is not None and conflict.id != current.id:
            resp = RedirectResponse(url="/profile?err=discord_taken", status_code=303)
        else:
            current.discord_id = discord_user_id
            if not current.email and discord_email:
                email_conflict = db.scalar(
                    select(User).where(User.email == discord_email)
                )
                if email_conflict is None:
                    current.email = discord_email
            db.commit()
            resp = RedirectResponse(url="/profile?ok=discord_linked", status_code=303)
        resp.delete_cookie(STATE_COOKIE, path="/")
        resp.delete_cookie(NEXT_COOKIE, path="/")
        return resp

    # Case B: anonymous + discord_id matches an existing User -> log them in
    user = db.scalar(select(User).where(User.discord_id == discord_user_id))
    if user is not None and user.is_active and not user.pending_approval:
        if not user.email and discord_email:
            email_conflict = db.scalar(
                select(User).where(User.email == discord_email)
            )
            if email_conflict is None:
                user.email = discord_email
                db.commit()
        if True:
            session = create_session(
                db, user,
                ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )
            resp = RedirectResponse(url=next_url or "/dashboard", status_code=303)
            _set_session_cookie(resp, session.session_id)
            resp.delete_cookie(STATE_COOKIE, path="/")
            resp.delete_cookie(NEXT_COOKIE, path="/")
            return resp

    # Case C: anonymous + unknown discord_id -> ask user what to do
    # Stash discord_user_id + username in a short-lived cookie and redirect
    # to /auth/discord/choose. NO account is created here.
    resp = RedirectResponse(url="/auth/discord/choose", status_code=303)
    payload = f"{discord_user_id}|{discord_username}|{discord_email}"
    resp.set_cookie(
        PENDING_COOKIE, payload, max_age=600, httponly=True,
        secure=SECURE_COOKIES, samesite="lax", path="/",
    )
    resp.delete_cookie(STATE_COOKIE, path="/")
    resp.delete_cookie(NEXT_COOKIE, path="/")
    return resp



@router.get("/auth/discord/choose")
async def discord_choose(request: Request):
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
    pending = request.cookies.get(PENDING_COOKIE, "")
    if "|" not in pending:
        return RedirectResponse(url="/login?error=discord_pending_missing", status_code=303)
    _, discord_username = pending.split("|", 1)
    return templates.TemplateResponse(
        request=request,
        name="auth/discord_choose.html",
        context={"discord_username": discord_username},
    )


@router.post("/auth/discord/create-external")
async def discord_create_external(
    request: Request,
    db: Session = Depends(get_db),
):
    pending = request.cookies.get(PENDING_COOKIE, "")
    if "|" not in pending:
        return RedirectResponse(url="/login?error=discord_pending_missing", status_code=303)
    parts = pending.split("|")
    discord_user_id = parts[0] if len(parts) > 0 else ""
    discord_username = parts[1] if len(parts) > 1 else ""
    discord_email = parts[2].lower().strip() if len(parts) > 2 else ""

    # Generate unique username derived from discord username
    base = ("dc_" + discord_username).lower().strip()[:24] or "dc_user"
    base = "".join(c for c in base if c.isalnum() or c in "_-") or "dc_user"
    username = base
    n = 0
    while db.scalar(select(User).where(User.username == username)) is not None:
        n += 1
        username = f"{base}_{n}"[:32]

    now = datetime.utcnow()
    user = User(
        username=username,
        password_hash="!disabled!" + secrets.token_urlsafe(16),
        role="external",
        character_id=None,
        is_active=True,
        created_at=now,
        pending_approval=False,
        submitted_at=now,
        submitted_in_game_name=discord_username or username,
        discord_id=discord_user_id,
        email=(discord_email if discord_email and not db.scalar(select(User).where(User.email == discord_email)) else None),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    session = create_session(
        db, user,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    resp = RedirectResponse(url="/apply", status_code=303)
    _set_session_cookie(resp, session.session_id)
    resp.delete_cookie(PENDING_COOKIE, path="/")
    return resp


@router.post("/profile/discord/unlink")
async def discord_unlink(
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(lambda: None),  # replaced by inject below
):
    # imported lazily to avoid circular
    from .auth import require_user
    # Re-run require_user manually
    current = await _require_or_none(request, db)
    if current is None:
        return RedirectResponse(url="/profile", status_code=303)
    current.discord_id = None
    db.commit()
    return RedirectResponse(url="/profile?ok=discord_unlinked", status_code=303)


async def _require_or_none(request: Request, db: Session) -> Optional[User]:
    from .auth import get_current_user
    return await get_current_user(request=request, db=db)
