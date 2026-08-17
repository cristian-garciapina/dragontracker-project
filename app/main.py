"""
Eternal Vanguard — main FastAPI application.

Routes:
- `/`              public landing
- `/login`         public
- `/logout`        public (POST)
- `/dashboard`     authenticated overview
- `/roster`        authenticated full member grid
- `/healthz`       public
"""
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import queries
from .auth import (
    ExternalGateException,
    MissingEmailException,
    RequiresLoginException,
    get_current_user,
    get_db,
    require_user,
)
from .auth_routes import router as auth_router
from .signup_routes import router as signup_router
from .staff_routes import router as staff_router
from .profile_routes import router as profile_router
from .settings_routes import router as settings_router
from .seasons_routes import router as seasons_router
from .recruitment_routes import router as recruitment_router
from .events_routes import router as events_router
from .farming_windows_routes import router as farming_windows_router
from .player_routes import router as player_router
from .discord_oauth import router as discord_oauth_router
from .api_routes import router as api_router
from .audit_routes import router as audit_router
from .farlight_routes import router as farlight_router
from .password_reset import router as password_reset_router
from .csp_report import router as csp_report_router
from .models import User

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="Eternal Vanguard",
    description="Alliance management website for Call of Dragons — Kingdom 193.",
    version="0.4.0",
    docs_url="/_docs",
    redoc_url=None,
)

app.include_router(auth_router)
app.include_router(signup_router)
app.include_router(staff_router)
app.include_router(profile_router)
app.include_router(settings_router)
app.include_router(recruitment_router)
app.include_router(events_router)
app.include_router(farming_windows_router)
app.include_router(player_router)
app.include_router(discord_oauth_router)
app.include_router(password_reset_router)
app.include_router(seasons_router)
app.include_router(api_router)
app.include_router(farlight_router)
app.include_router(audit_router)
app.include_router(csp_report_router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)


@app.exception_handler(RequiresLoginException)
async def requires_login_handler(request: Request, exc: RequiresLoginException):
    return RedirectResponse(url=f"/login?next={exc.next_url}", status_code=303)


@app.exception_handler(ExternalGateException)
async def external_gate_handler(request: Request, exc: ExternalGateException):
    return RedirectResponse(url="/apply", status_code=303)


@app.exception_handler(MissingEmailException)
async def missing_email_handler(request: Request, exc: MissingEmailException):
    return RedirectResponse(url="/profile?need_email=1", status_code=303)


# --- Public --------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def landing(
    request: Request,
    user: Optional[User] = Depends(get_current_user),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={
            "alliance_name": "Eternal Vanguard",
            "kingdom": 193,
            "user": user,
        },
    )





# --- Farm accounts (authenticated, all members) -------------------------
@app.get("/farms", response_class=HTMLResponse)
async def farms(
    request: Request,
    q: str = "",
    sort: str = "start_power",
    order: str = "desc",
    season: int = Query(0, description="Season id (0 = active)"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Read-only listing of accounts excluded from scoring (start power ≤ 15M)."""
    context: dict = {
        "user": user,
        "kingdom": 193,
        "season": None,
        "snapshot": None,
        "farms": [],
        "total": 0,
        "filters": {"q": "", "sort": "start_power", "order": "desc", "season": season or 0},
        "seasons_list": queries.list_seasons_for_picker(db),
        "ref_season": None,
    }

    selected = queries.resolve_season_or_active(db, season or None)
    if selected is None:
        return templates.TemplateResponse(
            request=request, name="dashboard/farms.html", context=context
        )
    context["season"] = selected
    season = selected  # rest of function uses `season`

    snapshot = queries.get_scoring_snapshot(db, season)
    if snapshot is None or not queries.has_any_scores(db, snapshot.id):
        return templates.TemplateResponse(
            request=request, name="dashboard/farms.html", context=context
        )
    context["snapshot"] = snapshot
    context["total"] = queries.count_farms(db, season.id, snapshot.id)
    context["farms"] = queries.get_farm_accounts(
        db, season.id, snapshot.id,
        search=q or None,
        sort=sort,
        order=order,
    )
    context["filters"] = {"q": q, "sort": sort, "order": order, "season": selected.id}

    return templates.TemplateResponse(
        request=request, name="dashboard/farms.html", context=context
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# --- Authenticated dashboard ---------------------------------------------
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_overview(
    request: Request,
    season: int = Query(0, description="Season id (0 = active)"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    context: dict = {
        "user": user,
        "kingdom": 193,
        "season": None,
        "season_progress": None,
        "snapshot": None,
        "stats": None,
        "distribution": {},
        "top_performers": [],
        "has_scores": False,
        "seasons_list": queries.list_seasons_for_picker(db),
        "selected_season_id": season or 0,
    }

    selected = queries.resolve_season_or_active(db, season or None)
    if selected is None:
        return templates.TemplateResponse(
            request=request, name="dashboard/overview.html", context=context
        )
    season = selected  # rest of function uses `season`

    context["season"] = season
    context["selected_season_id"] = season.id
    context["season_progress"] = queries.compute_season_progress(season)

    snapshot = queries.get_scoring_snapshot(db, season)
    if snapshot is None or not queries.has_any_scores(db, snapshot.id):
        return templates.TemplateResponse(
            request=request, name="dashboard/overview.html", context=context
        )

    stats = queries.get_dashboard_stats(db, season.id, snapshot.id)
    stats["count_missing"] = queries.get_missing_count(db, season, snapshot.id)

    is_staff_dash = user.role in ("staff", "owner")
    context.update(
        {
            "snapshot": snapshot,
            "stats": stats,
            "distribution": queries.get_grade_distribution(db, season.id, snapshot.id),
            "top_performers": queries.get_top_grade_s(db, season.id, snapshot.id),
            "season_history": queries.get_season_history(db),
            "has_scores": True,
            "ids_with_notes": queries.get_character_ids_with_notes(db) if is_staff_dash else set(),
        }
    )

    return templates.TemplateResponse(
        request=request, name="dashboard/overview.html", context=context
    )


# --- Authenticated full roster (Excel-like) ------------------------------
@app.get("/roster", response_class=HTMLResponse)
async def roster(
    request: Request,
    q: str = Query("", description="Substring search on member name or ID"),
    grade: str = Query("", description="Filter by grade letter"),
    role: str = Query("", description="Filter by primary role"),
    status: str = Query("", description="Filter by status KEEP/WATCH/EXPEL"),
    sort: str = Query("mp", description="Sort column key"),
    order: str = Query("desc", description="asc | desc"),
    farms: str = Query("0", description="Include farms (1) or not (0)"),
    mfarmers: str = Query("0", description="Include merit farmers"),
    exmembers: str = Query("0", description="Include ex-members"),
    discord: str = Query("0", description="Only Discord-linked users"),
    season: int = Query(0, description="Season id (0 = active)"),
    from_: str = Query("", alias="from", description="Window start YYYY-MM-DD"),
    to: str = Query("", description="Window end YYYY-MM-DD"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    include_farms = farms in ("1", "true", "yes")
    include_ex_members = exmembers in ("1", "true", "yes")
    discord_only = discord in ("1", "true", "yes")

    context: dict = {
        "user": user,
        "kingdom": 193,
        "season": None,
        "snapshot": None,
        "rows": [],
        "total_count": 0,
        "ids_with_notes": set(),
        "burns_count_by_char": {},
        "filtered_count": 0,
        "filters": {
            "q": q,
            "grade": grade.upper() if grade else "",
            "role": role.lower() if role else "",
            "status": status.upper() if status else "",
            "sort": sort,
            "order": order,
            "include_farms": include_farms,
            "include_ex_members": include_ex_members,
            "discord_only": discord_only,
            "season": season or 0,
        },
        "sortable_columns": list(queries.ROSTER_SORTABLE_COLUMNS.keys()),
        "seasons_list": queries.list_seasons_for_picker(db),
    }

    selected = queries.resolve_season_or_active(db, season or None)
    if selected is None:
        return templates.TemplateResponse(
            request=request, name="dashboard/roster.html", context=context
        )
    season = selected  # rest of function uses `season`
    context["season"] = season
    context["filters"]["season"] = season.id

    snapshot = queries.get_scoring_snapshot(db, season)
    if snapshot is None or not queries.has_any_scores(db, snapshot.id):
        return templates.TemplateResponse(
            request=request, name="dashboard/roster.html", context=context
        )
    context["snapshot"] = snapshot

    # Window mode branch: if from/to are set, aggregate daily snapshots instead.
    from datetime import date as _date
    window_from = None
    window_to = None
    if from_ and to:
        try:
            window_from = _date.fromisoformat(from_)
            window_to = _date.fromisoformat(to)
        except ValueError:
            window_from = None
            window_to = None
    context["daily_snapshots"] = queries.list_daily_snapshots_for_season(db, season.id)
    context["window_from"] = window_from.isoformat() if window_from else ""
    context["window_to"] = window_to.isoformat() if window_to else ""
    context["window_mode"] = bool(window_from and window_to)
    context["filters"]["from"] = context["window_from"]
    context["filters"]["to"] = context["window_to"]

    if window_from and window_to:
        rows = queries.get_roster_window(
            db, season.id, window_from, window_to,
            search=q or None,
            role=(role.lower() or None) if role else None,
            include_ex_members=include_ex_members,
            sort=sort,
            order=order,
        )
        context["rows"] = rows
        context["total_count"] = len(rows)
        context["filtered_count"] = len(rows)
        return templates.TemplateResponse(
            request=request, name="dashboard/roster.html", context=context
        )

    context["total_count"] = queries.count_total_roster(db, season.id, snapshot.id)
    ref_season, ref_ratios = queries.get_reference_season_and_ratios(db, season.id)
    context["ref_season"] = ref_season
    context["rows"] = queries.get_full_roster(
        db,
        season.id,
        snapshot.id,
        search=q or None,
        grade=grade or None,
        role=role or None,
        status=status or None,
        include_farms=include_farms,
        include_ex_members=include_ex_members,
        sort=sort,
        order=order,
        ref_ratios=ref_ratios,
    )
    context["filtered_count"] = len(context["rows"])
    is_staff_roster = user.role in ("staff", "owner")
    context["ids_with_notes"] = queries.get_character_ids_with_notes(db) if is_staff_roster else set()
    context["ids_with_discord"] = queries.get_character_ids_with_discord(db)
    context["burns_count_by_char"] = queries.get_character_ids_burned_this_season(db, season.id)

    return templates.TemplateResponse(
        request=request, name="dashboard/roster.html", context=context
    )
