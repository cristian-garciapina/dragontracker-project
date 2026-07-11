"""
Self-service signup + staff approval workflow.

Anyone can request an account via /signup. If the submitted rank is
R1/R2/R3, the account is activated immediately. If R4/R5, the account
sits in pending_approval=True until a staff member approves it via
/staff/registrations.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import (
    _set_cookie,
    create_session,
    get_db,
    hash_password,
    require_staff,
)
from .models import Member, User

router = APIRouter(tags=["signup"])

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

ALLOWED_RANKS = {"R1", "R2", "R3", "R4", "R5"}
PRIVILEGED_RANKS = {"R4", "R5"}
MIN_PASSWORD_LEN = 10


# --- Helpers --------------------------------------------------------------
def _empty_form() -> dict:
    return {
        "username": "",
        "in_game_name": "",
        "character_id": "",
        "rank": "",
        "server": "",
        "alliance_tag": "",
    }


def _render_form(request: Request, form: dict, error: Optional[str] = None,
                 status_code: int = 200):
    return templates.TemplateResponse(
        request=request,
        name="auth/signup.html",
        context={"form": form, "error": error},
        status_code=status_code,
    )


# --- Public signup --------------------------------------------------------
@router.get("/signup", response_class=HTMLResponse)
async def signup_form(request: Request):
    return _render_form(request, _empty_form())


@router.post("/signup")
async def signup_submit(
    request: Request,
    username: str = Form(...),
    in_game_name: str = Form(...),
    character_id: str = Form(...),
    rank: str = Form(...),
    server: str = Form(...),
    alliance_tag: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    form = {
        "username": username,
        "in_game_name": in_game_name,
        "character_id": character_id,
        "rank": rank,
        "server": server,
        "alliance_tag": alliance_tag,
    }

    # --- Field-level validation
    username_clean = username.lower().strip()
    if not username_clean or len(username_clean) > 32:
        return _render_form(request, form, "Invalid username.", 400)

    in_game_name_clean = in_game_name.strip()
    if not in_game_name_clean or len(in_game_name_clean) > 64:
        return _render_form(request, form, "Invalid in-game name.", 400)

    if rank.upper() not in ALLOWED_RANKS:
        return _render_form(request, form, "Invalid rank.", 400)
    rank_clean = rank.upper()

    try:
        character_id_int = int(character_id.strip())
    except ValueError:
        return _render_form(request, form, "Player ID must be a number.", 400)

    try:
        server_int = int(server.strip())
    except ValueError:
        return _render_form(request, form, "Server must be a number.", 400)

    alliance_tag_clean = alliance_tag.strip()
    if not alliance_tag_clean or len(alliance_tag_clean) > 16:
        return _render_form(request, form, "Invalid alliance tag.", 400)

    if password != password_confirm:
        return _render_form(request, form, "Passwords do not match.", 400)
    if len(password) < MIN_PASSWORD_LEN:
        return _render_form(
            request, form, f"Password must be at least {MIN_PASSWORD_LEN} characters.", 400
        )

    # --- Uniqueness / existence checks
    if db.scalar(select(User).where(User.username == username_clean)):
        return _render_form(request, form, "This username is already taken.", 400)

    if db.scalar(select(User).where(User.character_id == character_id_int)):
        return _render_form(
            request, form, "An account is already linked to this Player ID.", 400
        )

    # No hard check on Member existence: a fresh migrant may not have been
    # ingested yet. Create a ghost Member (in_alliance=False) so foreign keys
    # hold. Staff sees the pending account and can approve after cross-check.
    if not db.get(Member, character_id_int):
        db.add(Member(
            character_id=character_id_int,
            current_name=in_game_name_clean,
            in_alliance=False,
        ))
        db.flush()

    # --- Create account
    # ALL registrations go through pending. Staff validates manually so
    # an outsider claiming to belong to the alliance cannot self-activate
    # to spy on the roster.
    now = datetime.utcnow()

    user = User(
        username=username_clean,
        password_hash=hash_password(password),
        role="member",  # role is decided by staff at approval time
        character_id=character_id_int,
        is_active=False,
        created_at=now,
        pending_approval=True,
        submitted_at=now,
        submitted_in_game_name=in_game_name_clean,
        submitted_rank=rank_clean,
        submitted_server=server_int,
        submitted_alliance_tag=alliance_tag_clean,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return templates.TemplateResponse(
        request=request,
        name="auth/signup_pending.html",
        context={"rank": rank_clean},
    )


# --- Staff: pending registrations review ---------------------------------
@router.get("/staff/registrations", response_class=HTMLResponse)
async def list_registrations(
    request: Request,
    staff: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    pending = db.scalars(
        select(User).where(User.pending_approval == True).order_by(User.submitted_at.asc())
    ).all()

    # Join the Member.current_name as in-game reference for cross-check
    rows = []
    for u in pending:
        member = db.get(Member, u.character_id) if u.character_id else None
        rows.append({
            "user": u,
            "member_current_name": member.current_name if member else None,
        })

    return templates.TemplateResponse(
        request=request,
        name="staff/registrations.html",
        context={"user": staff, "rows": rows, "kingdom": 193},
    )


@router.post("/staff/registrations/{user_id}/approve")
async def approve_registration(
    user_id: int,
    grant_role: str = Form("member"),
    staff: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    u = db.get(User, user_id)
    if u is None or not u.pending_approval:
        return RedirectResponse(url="/staff/registrations", status_code=303)

    role = grant_role if grant_role in ("member", "staff", "owner") else "member"
    # Only an owner can grant the owner role at approval time
    if role == "owner" and staff.role != "owner":
        role = "staff"

    u.pending_approval = False
    u.is_active = True
    u.role = role
    db.commit()
    return RedirectResponse(url="/staff/registrations", status_code=303)


@router.post("/staff/registrations/{user_id}/reject")
async def reject_registration(
    user_id: int,
    staff: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    u = db.get(User, user_id)
    if u is not None and u.pending_approval:
        db.delete(u)
        db.commit()
    return RedirectResponse(url="/staff/registrations", status_code=303)
