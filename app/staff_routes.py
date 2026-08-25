"""
Staff administration: full user management.

Authorization model:
- Both 'staff' and 'owner' can access /staff/users and the registrations page.
- A 'staff' user can act on members and other staff, but NEVER on owners.
- An 'owner' can act on anyone (members, staff, other owners, themselves).
- The owner role can only be granted by another owner.

Self-actions are allowed without UI guardrails. The owner is expected
to use SSH + direct SQL access for emergency recovery if they paint
themselves into a corner.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .auth import get_db, require_staff
from .models import Member, User, UserSession

router = APIRouter(tags=["staff"])

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# --- Helpers --------------------------------------------------------------
def _invalidate_sessions(db: Session, user_id: int) -> int:
    sessions = db.scalars(
        select(UserSession).where(UserSession.user_id == user_id)
    ).all()
    for s in sessions:
        db.delete(s)
    return len(sessions)


def _can_act_on(actor: User, target: User) -> bool:
    """Hierarchy rule:
    - owner can act on anyone (including themselves and other owners)
    - staff can act on members and other staff, but NEVER on owners
    """
    if actor.role == "owner":
        return True
    if actor.role == "staff":
        return target.role in ("external", "member", "staff")
    return False


# --- List ----------------------------------------------------------------
@router.get("/staff/users", response_class=HTMLResponse)
async def list_users(
    request: Request,
    q: str = Query(""),
    state: str = Query(""),
    role: str = Query(""),
    actor: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    stmt = select(User)

    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                User.username.ilike(like),
                User.submitted_in_game_name.ilike(like),
            )
        )

    if state == "active":
        stmt = stmt.where(User.is_active == True, User.pending_approval == False)
    elif state == "disabled":
        stmt = stmt.where(User.is_active == False, User.pending_approval == False)
    elif state == "pending":
        stmt = stmt.where(User.pending_approval == True)

    if role in ("external", "member", "staff", "owner"):
        stmt = stmt.where(User.role == role)

    stmt = stmt.order_by(User.created_at.desc())
    users = db.scalars(stmt).all()

    rows = []
    for u in users:
        member = db.get(Member, u.character_id) if u.character_id else None
        rows.append({
            "u": u,
            "member_current_name": member.current_name if member else None,
            "is_self": (u.id == actor.id),
            "can_act": _can_act_on(actor, u),
        })

    return templates.TemplateResponse(
        request=request,
        name="staff/users.html",
        context={
            "user": actor,
            "rows": rows,
            "filters": {"q": q, "state": state, "role": role},
            "total": len(users),
        },
    )


# --- Actions --------------------------------------------------------------
def _redirect_back():
    return RedirectResponse(url="/staff/users", status_code=303)


@router.post("/staff/users/{user_id}/toggle-active")
async def toggle_active(
    user_id: int,
    actor: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    u = db.get(User, user_id)
    if u is None or u.pending_approval:
        return _redirect_back()
    if not _can_act_on(actor, u):
        return _redirect_back()

    u.is_active = not u.is_active
    if not u.is_active:
        _invalidate_sessions(db, u.id)
    db.commit()
    return _redirect_back()


@router.post("/staff/users/{user_id}/set-role")
async def set_role(
    user_id: int,
    new_role: str = Form(...),
    actor: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    u = db.get(User, user_id)
    if u is None or u.pending_approval:
        return _redirect_back()
    if not _can_act_on(actor, u):
        return _redirect_back()
    if new_role not in ("external", "member", "staff", "owner"):
        return _redirect_back()
    # Only an owner can grant the owner role
    if new_role == "owner" and actor.role != "owner":
        return _redirect_back()

    u.role = new_role
    db.commit()
    return _redirect_back()


@router.post("/staff/users/{user_id}/delete")
async def delete_user(
    user_id: int,
    actor: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    u = db.get(User, user_id)
    if u is None:
        return _redirect_back()
    if not _can_act_on(actor, u):
        return _redirect_back()

    db.delete(u)  # UserSession.user_id has ondelete='CASCADE'
    db.commit()
    return _redirect_back()


# --- Staff edit user fields ---------------------------------------------
ALLOWED_RANKS = {"R1", "R2", "R3", "R4", "R5"}


@router.get("/staff/users/{user_id}/edit", response_class=HTMLResponse)
async def staff_edit_form(
    user_id: int,
    request: Request,
    actor: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    u = db.get(User, user_id)
    if u is None or not _can_act_on(actor, u):
        return RedirectResponse(url="/staff/users", status_code=303)

    member = db.get(Member, u.character_id) if u.character_id else None
    return templates.TemplateResponse(
        request=request,
        name="staff/user_edit.html",
        context={
            "user": actor,
            "target": u,
            "member_current_name": member.current_name if member else None,
            "error": None,
            "success": None,
        },
    )


@router.post("/staff/users/{user_id}/edit")
async def staff_edit_user(
    user_id: int,
    request: Request,
    in_game_name: str = Form(...),
    rank: str = Form(...),
    server: str = Form(...),
    alliance_tag: str = Form(...),
    actor: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    u = db.get(User, user_id)
    if u is None or not _can_act_on(actor, u):
        return RedirectResponse(url="/staff/users", status_code=303)

    def render(error=None, success=None):
        member = db.get(Member, u.character_id) if u.character_id else None
        return templates.TemplateResponse(
            request=request,
            name="staff/user_edit.html",
            context={
                "user": actor,
                "target": u,
                "member_current_name": member.current_name if member else None,
                "error": error,
                "success": success,
            },
        )

    in_game_name_clean = in_game_name.strip()
    if not in_game_name_clean or len(in_game_name_clean) > 64:
        return render(error="Invalid in-game name.")
    if rank.upper() not in ALLOWED_RANKS:
        return render(error="Invalid rank.")
    try:
        server_int = int(server.strip())
    except ValueError:
        return render(error="Server must be a number.")
    alliance_tag_clean = alliance_tag.strip()
    if not alliance_tag_clean or len(alliance_tag_clean) > 16:
        return render(error="Invalid alliance tag.")

    u.submitted_in_game_name = in_game_name_clean
    u.submitted_rank = rank.upper()
    u.submitted_server = server_int
    u.submitted_alliance_tag = alliance_tag_clean
    db.commit()
    db.refresh(u)
    return render(success="User profile updated.")
