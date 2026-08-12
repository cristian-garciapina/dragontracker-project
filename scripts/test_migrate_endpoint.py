#!/usr/bin/env python3
"""Test /api/applications/{id}/migrate endpoint.

In-memory SQLite. Uses FastAPI TestClient (no HTTP mocking needed).
Run: /opt/dashboard/venv/bin/python scripts/test_migrate_endpoint.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["EV_API_KEY"] = "test-api-key"

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api_routes import router as api_router
from app import db as db_module
from app.models import Application, Base, Member, User

HEADERS = {"Authorization": "Bearer test-api-key"}


def make_app():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[db_module.get_session] = override_get_session
    return app, SessionLocal


def add_application(SessionLocal, status="new", player_id=12345):
    s = SessionLocal()
    now = datetime.utcnow()
    a = Application(
        in_game_name="Applicant", player_id=player_id, current_alliance=None,
        server=193, motivation="x" * 25, discord_handle=None,
        created_at=now, status=status, reference="EV-2026-TEST",
        status_updated_at=now,
    )
    s.add(a); s.commit(); s.refresh(a)
    aid = a.id
    s.close()
    return aid


def add_member(SessionLocal, character_id):
    s = SessionLocal()
    now = datetime.utcnow()
    m = Member(character_id=character_id, current_name="X",
               first_seen_at=now, last_seen_at=now)
    s.add(m); s.commit(); s.close()


def add_external_user(SessionLocal, character_id, username="ext"):
    s = SessionLocal()
    now = datetime.utcnow()
    u = User(username=username, password_hash="!x!", role="external",
             character_id=character_id, is_active=True, created_at=now,
             pending_approval=False)
    s.add(u); s.commit(); s.refresh(u)
    uid = u.id
    s.close()
    return uid


def get_app_status(SessionLocal, aid):
    s = SessionLocal()
    a = s.get(Application, aid)
    st = a.status
    s.close()
    return st


def get_user_role(SessionLocal, uid):
    s = SessionLocal()
    u = s.get(User, uid)
    r = u.role
    s.close()
    return r


def t_migrate_new():
    app, SL = make_app()
    aid = add_application(SL, status="new")
    r = TestClient(app).post(f"/api/applications/{aid}/migrate",
                             json={"acted_by": "discord:tester"},
                             headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "migrated"
    assert get_app_status(SL, aid) == "migrated"
    print("PASS  migrate from new")


def t_migrate_reviewing():
    app, SL = make_app()
    aid = add_application(SL, status="reviewing")
    r = TestClient(app).post(f"/api/applications/{aid}/migrate",
                             json={}, headers=HEADERS)
    assert r.status_code == 200
    assert get_app_status(SL, aid) == "migrated"
    print("PASS  migrate from reviewing")


def t_migrate_accepted():
    app, SL = make_app()
    aid = add_application(SL, status="accepted")
    r = TestClient(app).post(f"/api/applications/{aid}/migrate",
                             json={}, headers=HEADERS)
    assert r.status_code == 200
    assert get_app_status(SL, aid) == "migrated"
    print("PASS  migrate from accepted")


def t_noop_migrated():
    app, SL = make_app()
    aid = add_application(SL, status="migrated")
    r = TestClient(app).post(f"/api/applications/{aid}/migrate",
                             json={}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["status"] == "noop"
    print("PASS  noop when already migrated")


def t_refuse_rejected():
    app, SL = make_app()
    aid = add_application(SL, status="rejected")
    r = TestClient(app).post(f"/api/applications/{aid}/migrate",
                             json={}, headers=HEADERS)
    assert r.status_code == 409, r.text
    print("PASS  refuse when rejected")


def t_auto_promote_external():
    app, SL = make_app()
    add_member(SL, 12345)
    uid = add_external_user(SL, character_id=12345)
    aid = add_application(SL, status="new", player_id=12345)
    r = TestClient(app).post(f"/api/applications/{aid}/migrate",
                             json={}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["promoted_user_id"] == uid
    assert get_user_role(SL, uid) == "member"
    print("PASS  auto-promote external -> member")


def t_no_promote_when_no_user():
    app, SL = make_app()
    aid = add_application(SL, status="new", player_id=99999)
    r = TestClient(app).post(f"/api/applications/{aid}/migrate",
                             json={}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["promoted_user_id"] is None
    print("PASS  no promotion when no linked user")


def t_no_promote_when_already_member():
    app, SL = make_app()
    add_member(SL, 12345)
    uid = add_external_user(SL, character_id=12345, username="already")
    s = SL()
    u = s.get(User, uid); u.role = "member"; s.commit(); s.close()
    aid = add_application(SL, status="new", player_id=12345)
    r = TestClient(app).post(f"/api/applications/{aid}/migrate",
                             json={}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["promoted_user_id"] is None
    assert get_user_role(SL, uid) == "member"
    print("PASS  no promotion when linked user is already member")


def t_404_missing():
    app, SL = make_app()
    r = TestClient(app).post("/api/applications/99999/migrate",
                             json={}, headers=HEADERS)
    assert r.status_code == 404
    print("PASS  404 when application missing")


def t_auth_required():
    app, SL = make_app()
    aid = add_application(SL, status="new")
    r = TestClient(app).post(f"/api/applications/{aid}/migrate", json={})
    assert r.status_code == 401
    r = TestClient(app).post(f"/api/applications/{aid}/migrate",
                             json={}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403
    print("PASS  auth required")


if __name__ == "__main__":
    t_migrate_new()
    t_migrate_reviewing()
    t_migrate_accepted()
    t_noop_migrated()
    t_refuse_rejected()
    t_auto_promote_external()
    t_no_promote_when_no_user()
    t_no_promote_when_already_member()
    t_404_missing()
    t_auth_required()
    print("\nAll tests passed.")
