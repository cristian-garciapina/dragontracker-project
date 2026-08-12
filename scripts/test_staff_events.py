#!/usr/bin/env python3
"""Test staff_events audit + /status lookup endpoints.

In-memory SQLite via StaticPool. Uses FastAPI TestClient.
Run: /opt/dashboard/venv/bin/python scripts/test_staff_events.py
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
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api_routes import router as api_router
from app import db as db_module
from app.models import Application, Base, Member, StaffEvent, User

HEADERS = {"Authorization": "Bearer test-api-key"}


def make_app():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SL = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override():
        s = SL()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[db_module.get_session] = override
    return app, SL


def add_app_row(SL, status="new", player_id=12345, ref="EV-2026-TEST"):
    s = SL(); now = datetime.utcnow()
    a = Application(
        in_game_name="X", player_id=player_id, current_alliance=None,
        server=193, motivation="x" * 25, discord_handle=None,
        created_at=now, status=status, reference=ref, status_updated_at=now,
    )
    s.add(a); s.commit(); s.refresh(a)
    aid = a.id; s.close(); return aid


def add_pending_user(SL, username="alice", char_id=None):
    s = SL(); now = datetime.utcnow()
    if char_id is not None and s.get(Member, char_id) is None:
        s.add(Member(character_id=char_id, current_name="X",
                     first_seen_at=now, last_seen_at=now))
        s.commit()
    u = User(username=username, password_hash="!x!", role="member",
            character_id=char_id, is_active=False, created_at=now,
            pending_approval=True)
    s.add(u); s.commit(); s.refresh(u)
    uid = u.id; s.close(); return uid


def last_event(SL, entity_type, entity_id):
    s = SL()
    e = s.scalar(select(StaffEvent).where(
        StaffEvent.entity_type == entity_type,
        StaffEvent.entity_id == entity_id,
    ).order_by(StaffEvent.at.desc()))
    if e:
        out = (e.action, e.detail, e.actor, e.entity_ref)
    else:
        out = None
    s.close(); return out


# ---- Application audit tests -----------------------------------------------

def t_accept_writes_event():
    app, SL = make_app()
    aid = add_app_row(SL)
    r = TestClient(app).post(f"/api/applications/{aid}/accept",
                             json={"acted_by": "discord:alice"}, headers=HEADERS)
    assert r.status_code == 200
    assert last_event(SL, "application", aid) == ("accepted", None, "discord:alice", "EV-2026-TEST")
    print("PASS  API accept writes audit event")


def t_reject_writes_event():
    app, SL = make_app()
    aid = add_app_row(SL)
    r = TestClient(app).post(f"/api/applications/{aid}/reject",
                             json={"public_reason": "no", "acted_by": "discord:bob"},
                             headers=HEADERS)
    assert r.status_code == 200
    assert last_event(SL, "application", aid) == ("rejected", None, "discord:bob", "EV-2026-TEST")
    print("PASS  API reject writes audit event")


def t_migrate_writes_event():
    app, SL = make_app()
    aid = add_app_row(SL, status="accepted")
    r = TestClient(app).post(f"/api/applications/{aid}/migrate",
                             json={"acted_by": "discord:cari"}, headers=HEADERS)
    assert r.status_code == 200
    assert last_event(SL, "application", aid) == ("migrated", None, "discord:cari", "EV-2026-TEST")
    print("PASS  API migrate writes audit event")


# ---- Registration audit tests ----------------------------------------------

def t_approve_writes_event():
    app, SL = make_app()
    uid = add_pending_user(SL)
    r = TestClient(app).post(f"/api/registrations/{uid}/approve",
                             json={"grant_role": "member", "acted_by": "discord:dan"},
                             headers=HEADERS)
    assert r.status_code == 200
    assert last_event(SL, "registration", uid) == ("approved", "member", "discord:dan", "alice")
    print("PASS  API approve writes audit event (with role detail)")


def t_reject_registration_writes_event():
    app, SL = make_app()
    uid = add_pending_user(SL, username="spy")
    r = TestClient(app).post(f"/api/registrations/{uid}/reject",
                             json={"acted_by": "discord:eve"}, headers=HEADERS)
    assert r.status_code == 200
    assert last_event(SL, "registration", uid) == ("rejected", None, "discord:eve", "spy")
    print("PASS  API reject registration writes audit event (before delete)")


# ---- /status endpoint tests ------------------------------------------------

def t_status_app_pending():
    app, SL = make_app()
    aid = add_app_row(SL, status="new")
    r = TestClient(app).get(f"/api/applications/{aid}/status", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "new"
    assert body["entity_ref"] == "EV-2026-TEST"
    assert body["last_action"] is None
    print("PASS  /applications/{id}/status returns 'new' when no audit yet")


def t_status_app_after_accept():
    app, SL = make_app()
    aid = add_app_row(SL)
    TestClient(app).post(f"/api/applications/{aid}/accept",
                         json={"acted_by": "discord:alice"}, headers=HEADERS)
    r = TestClient(app).get(f"/api/applications/{aid}/status", headers=HEADERS)
    body = r.json()
    assert body["state"] == "accepted"
    assert body["last_action"]["action"] == "accepted"
    assert body["last_action"]["actor"] == "discord:alice"
    print("PASS  /applications/{id}/status shows last accept event")


def t_status_app_after_reject():
    app, SL = make_app()
    aid = add_app_row(SL)
    TestClient(app).post(f"/api/applications/{aid}/reject",
                         json={"public_reason": "no", "acted_by": "discord:bob"},
                         headers=HEADERS)
    r = TestClient(app).get(f"/api/applications/{aid}/status", headers=HEADERS)
    body = r.json()
    assert body["state"] == "rejected"
    assert body["last_action"]["action"] == "rejected"
    print("PASS  /applications/{id}/status shows rejected state")


def t_status_app_404_orphan():
    app, SL = make_app()
    r = TestClient(app).get("/api/applications/99999/status", headers=HEADERS)
    assert r.status_code == 404
    print("PASS  /applications/{id}/status 404 on true orphan")


def t_status_registration_pending():
    app, SL = make_app()
    uid = add_pending_user(SL)
    r = TestClient(app).get(f"/api/registrations/{uid}/status", headers=HEADERS)
    body = r.json()
    assert body["state"] == "pending"
    assert body["entity_ref"] == "alice"
    print("PASS  /registrations/{id}/status returns 'pending'")


def t_status_registration_approved():
    app, SL = make_app()
    uid = add_pending_user(SL)
    TestClient(app).post(f"/api/registrations/{uid}/approve",
                         json={"grant_role": "staff", "acted_by": "discord:dan"},
                         headers=HEADERS)
    r = TestClient(app).get(f"/api/registrations/{uid}/status", headers=HEADERS)
    body = r.json()
    assert body["state"] == "approved:staff", body
    assert body["last_action"]["detail"] == "staff"
    print("PASS  /registrations/{id}/status shows approved:role")


def t_status_registration_rejected_after_delete():
    """After reject: row is gone, but audit event exists -> state='rejected'."""
    app, SL = make_app()
    uid = add_pending_user(SL, username="spy")
    TestClient(app).post(f"/api/registrations/{uid}/reject",
                         json={"acted_by": "discord:eve"}, headers=HEADERS)
    r = TestClient(app).get(f"/api/registrations/{uid}/status", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "rejected"
    assert body["entity_ref"] == "spy"
    assert body["last_action"]["actor"] == "discord:eve"
    print("PASS  /registrations/{id}/status resolves rejected+deleted row")


def t_status_registration_404_orphan():
    app, SL = make_app()
    r = TestClient(app).get("/api/registrations/99999/status", headers=HEADERS)
    assert r.status_code == 404
    print("PASS  /registrations/{id}/status 404 on true orphan")


if __name__ == "__main__":
    t_accept_writes_event()
    t_reject_writes_event()
    t_migrate_writes_event()
    t_approve_writes_event()
    t_reject_registration_writes_event()
    t_status_app_pending()
    t_status_app_after_accept()
    t_status_app_after_reject()
    t_status_app_404_orphan()
    t_status_registration_pending()
    t_status_registration_approved()
    t_status_registration_rejected_after_delete()
    t_status_registration_404_orphan()
    print("\nAll tests passed.")
