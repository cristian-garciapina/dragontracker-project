"""
Auth routes: /login (GET + POST), /logout (POST).
"""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .auth import (
    COOKIE_NAME,
    _clear_cookie,
    _set_cookie,
    create_session,
    delete_session,
    get_db,
    verify_password,
)
from .models import Member, User

router = APIRouter(tags=["auth"])

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/dashboard", reset: int = 0):
    flash = None
    if reset:
        flash = "Your password has been reset. You can log in with your new password."
    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={"next": next, "error": None, "flash": flash},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/dashboard"),
    db: Session = Depends(get_db),
):
    ident = (username or "").lower().strip()
    user = db.scalar(
        select(User).where(or_(User.username == ident, User.email == ident))
    )

    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="auth/login.html",
            context={
                "next": next,
                "error": "Invalid username or password.",
                "flash": None,
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Open-redirect guard
    if not next.startswith("/") or next.startswith("//"):
        next = "/dashboard"

    session = create_session(
        db,
        user,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    # If a discord OAuth was pending, link the discord_id to this user
    pending = request.cookies.get("discord_pending_id", "")
    linked_msg = ""
    if "|" in pending:
        discord_user_id, _ = pending.split("|", 1)
        # Refuse if the discord_id is already claimed by another user
        from sqlalchemy import select as _sel
        conflict = db.scalar(
            _sel(User).where(User.discord_id == discord_user_id)
        )
        if conflict is None or conflict.id == user.id:
            user.discord_id = discord_user_id
            db.commit()
            linked_msg = "?ok=discord_linked"
        else:
            linked_msg = "?err=discord_taken"

    redirect_url = "/profile" + linked_msg if linked_msg else next
    response = RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
    _set_cookie(response, session.session_id)
    response.delete_cookie("discord_pending_id", path="/")
    return response


@router.post("/logout")
async def logout(
    request: Request,
    db: Session = Depends(get_db),
):
    session_id = request.cookies.get(COOKIE_NAME)
    if session_id:
        delete_session(db, session_id)
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    _clear_cookie(response)
    return response
