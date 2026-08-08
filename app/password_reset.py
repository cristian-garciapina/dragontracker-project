"""Password reset flow.

Routes:
  GET  /forgot          Form: enter email
  POST /forgot          Generate token, send reset email (generic response)
  GET  /reset/{token}   Form: enter new password (if token valid)
  POST /reset/{token}   Update password, invalidate token, redirect to login
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from argon2 import PasswordHasher
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import get_db
from .mailer import send_password_reset
from .models import PasswordResetToken, User

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_hasher = PasswordHasher()

TOKEN_TTL = timedelta(hours=1)


def _generic_forgot_response(request: Request) -> HTMLResponse:
    """Same response whether the email exists or not, to avoid enumeration."""
    return templates.TemplateResponse(
        "auth/forgot_sent.html",
        {"request": request},
    )


def _base_url(request: Request) -> str:
    """Public base URL for building reset links. Respects X-Forwarded-* headers."""
    return str(request.base_url).rstrip("/")


@router.get("/forgot", response_class=HTMLResponse)
def forgot_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "auth/forgot.html",
        {"request": request, "error": None},
    )


@router.post("/forgot", response_class=HTMLResponse)
def forgot_submit(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    email_clean = (email or "").strip().lower()
    if not email_clean or "@" not in email_clean:
        return templates.TemplateResponse(
            "auth/forgot.html",
            {"request": request, "error": "Please enter a valid email address."},
        )

    user = db.execute(
        select(User).where(User.email == email_clean)
    ).scalar_one_or_none()

    if user is None or not user.is_active:
        return _generic_forgot_response(request)

    # Invalidate any previous unused token for this user
    prev_tokens = db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
    ).scalars().all()
    now = datetime.utcnow()
    for t in prev_tokens:
        t.used_at = now

    # Generate a new token
    token_str = secrets.token_urlsafe(32)
    reset_token = PasswordResetToken(
        user_id=user.id,
        token=token_str,
        created_at=now,
        expires_at=now + TOKEN_TTL,
    )
    db.add(reset_token)
    db.commit()

    reset_url = f"{_base_url(request)}/reset/{token_str}"
    ok = send_password_reset(
        to=user.email,
        username=user.username,
        reset_url=reset_url,
    )
    if not ok:
        print(f"[password_reset] Failed to send email to user_id={user.id}")

    return _generic_forgot_response(request)


def _load_valid_token(
    db: Session, token: str
) -> tuple[Optional[PasswordResetToken], Optional[User]]:
    reset_token = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token == token)
    ).scalar_one_or_none()
    if reset_token is None:
        return None, None
    if reset_token.used_at is not None:
        return None, None
    if reset_token.expires_at < datetime.utcnow():
        return None, None
    user = db.get(User, reset_token.user_id)
    if user is None or not user.is_active:
        return None, None
    return reset_token, user


@router.get("/reset/{token}", response_class=HTMLResponse)
def reset_form(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    reset_token, user = _load_valid_token(db, token)
    if reset_token is None or user is None:
        return templates.TemplateResponse(
            "auth/reset_invalid.html",
            {"request": request},
        )
    return templates.TemplateResponse(
        "auth/reset.html",
        {"request": request, "token": token, "username": user.username, "error": None},
    )


@router.post("/reset/{token}", response_class=HTMLResponse)
def reset_submit(
    request: Request,
    token: str,
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    reset_token, user = _load_valid_token(db, token)
    if reset_token is None or user is None:
        return templates.TemplateResponse(
            "auth/reset_invalid.html",
            {"request": request},
        )

    if len(password) < 8:
        return templates.TemplateResponse(
            "auth/reset.html",
            {
                "request": request,
                "token": token,
                "username": user.username,
                "error": "Password must be at least 8 characters.",
            },
        )
    if password != password_confirm:
        return templates.TemplateResponse(
            "auth/reset.html",
            {
                "request": request,
                "token": token,
                "username": user.username,
                "error": "Passwords do not match.",
            },
        )

    user.password_hash = _hasher.hash(password)
    reset_token.used_at = datetime.utcnow()
    db.commit()

    return RedirectResponse(url="/login?reset=1", status_code=303)
