"""
Self-service profile editing for any authenticated user.

GET  /profile            view profile
POST /profile            update profile fields
POST /profile/password   change own password

Username, character_id, role, is_active are NOT editable from here —
those are admin-only concerns.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .auth import get_db, hash_password, require_user, verify_password
from .models import Member, User

router = APIRouter(tags=["profile"])

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

ALLOWED_RANKS = {"R1", "R2", "R3", "R4", "R5"}
MIN_PASSWORD_LEN = 10


def _render(request: Request, user: User, db: Session,
            error: str | None = None, success: str | None = None):
    member = db.get(Member, user.character_id) if user.character_id else None
    return templates.TemplateResponse(
        request=request,
        name="profile/profile.html",
        context={
            "user": user,
            "member_current_name": member.current_name if member else None,
            "linked_discord_id": user.discord_id,
            "user_email": user.email,
            "need_email": bool(request.query_params.get("need_email")),
            "email_ok": request.query_params.get("ok") == "email_updated",
            "email_err": request.query_params.get("err") == "email_taken",
            "error": error,
            "success": success,
        },
    )


@router.get("/profile", response_class=HTMLResponse)
async def profile_view(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return _render(request, user, db)


@router.post("/profile")
async def profile_update(
    request: Request,
    in_game_name: str = Form(...),
    rank: str = Form(...),
    server: str = Form(...),
    alliance_tag: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    in_game_name_clean = in_game_name.strip()
    if not in_game_name_clean or len(in_game_name_clean) > 64:
        return _render(request, user, db, error="Invalid in-game name.")

    if rank.upper() not in ALLOWED_RANKS:
        return _render(request, user, db, error="Invalid rank.")

    try:
        server_int = int(server.strip())
    except ValueError:
        return _render(request, user, db, error="Server must be a number.")

    alliance_tag_clean = alliance_tag.strip()
    if not alliance_tag_clean or len(alliance_tag_clean) > 16:
        return _render(request, user, db, error="Invalid alliance tag.")

    user.submitted_in_game_name = in_game_name_clean
    user.submitted_rank = rank.upper()
    user.submitted_server = server_int
    user.submitted_alliance_tag = alliance_tag_clean
    db.commit()
    db.refresh(user)
    return _render(request, user, db, success="Profile updated.")


@router.post("/profile/password")
async def profile_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not verify_password(current_password, user.password_hash):
        return _render(request, user, db, error="Current password is incorrect.")

    if new_password != new_password_confirm:
        return _render(request, user, db, error="New passwords do not match.")

    if len(new_password) < MIN_PASSWORD_LEN:
        return _render(
            request, user, db,
            error=f"New password must be at least {MIN_PASSWORD_LEN} characters."
        )

    user.password_hash = hash_password(new_password)
    db.commit()
    return _render(request, user, db, success="Password changed.")


@router.post("/profile/email")
async def profile_email_update(
    request: Request,
    email: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    from sqlalchemy import select as _sel
    email_clean = (email or "").lower().strip()
    if not email_clean or "@" not in email_clean or "." not in email_clean.split("@")[-1] or len(email_clean) > 120:
        return _render(request, user, db, error="Please enter a valid email address.")
    conflict = db.scalar(_sel(User).where(User.email == email_clean, User.id != user.id))
    if conflict is not None:
        return RedirectResponse(url="/profile?err=email_taken", status_code=303)
    user.email = email_clean
    db.commit()
    return RedirectResponse(url="/profile?ok=email_updated", status_code=303)

