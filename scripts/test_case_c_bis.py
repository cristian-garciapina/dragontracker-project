#!/usr/bin/env python3
"""Test Case C-bis: auto-link Discord to existing account when email matches.

In-memory SQLite. httpx monkey-patched to fake the Discord OAuth exchange.
Run: python scripts/test_case_c_bis.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DISCORD_CLIENT_ID", "test-id")
os.environ.setdefault("DISCORD_CLIENT_SECRET", "test-secret")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import discord_oauth
from app.models import Base, User


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
    def json(self):
        return self._payload


class FakeAsyncClient:
    token_status = 200
    token_payload = {"access_token": "faketoken"}
    me_status = 200
    me_payload = {"id": "999", "username": "spy", "email": "match@example.com"}

    def __init__(self, *a, **kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, url, data=None, headers=None):
        return FakeResponse(self.token_status, self.token_payload)
    async def get(self, url, headers=None):
        return FakeResponse(self.me_status, self.me_payload)


async def fake_notify(discord_id): pass


class FakeRequest:
    def __init__(self, cookies=None):
        self.cookies = cookies or {"discord_oauth_state": "s"}
        self.client = types.SimpleNamespace(host="127.0.0.1")
        self.headers = {"user-agent": "test"}


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(bind=engine)


def add_user(db, **overrides):
    now = datetime.utcnow()
    defaults = dict(
        username="alice", password_hash="!x!", role="member",
        character_id=None, is_active=True, created_at=now,
        pending_approval=False, email="match@example.com", discord_id=None,
    )
    defaults.update(overrides)
    u = User(**defaults)
    db.add(u); db.commit(); db.refresh(u)
    return u


async def call_callback(db, current=None):
    req = FakeRequest()
    return await discord_oauth.discord_callback(
        request=req, code="c", state="s", error="",
        db=db, current=current,
    )


def install_fakes():
    discord_oauth.httpx.AsyncClient = FakeAsyncClient
    discord_oauth.notify_verified_link = fake_notify
    discord_oauth.notify_verified_unlink = fake_notify


# --- Scenarios --------------------------------------------------------------

async def t_positive():
    db = make_session()
    user = add_user(db)
    resp = await call_callback(db)
    db.refresh(user)
    assert user.discord_id == "999", f"expected linked, got {user.discord_id!r}"
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard", resp.headers["location"]
    dc = db.query(User).filter(User.username.like("dc_%")).count()
    assert dc == 0, f"expected 0 dc_*, got {dc}"
    print("PASS  C-bis positive: email match auto-links and logs in")


async def t_blocked_has_discord():
    db = make_session()
    add_user(db, discord_id="already-linked-id")
    resp = await call_callback(db)
    assert resp.headers["location"] == "/auth/discord/choose", resp.headers["location"]
    print("PASS  C-bis blocked when existing user already has discord_id (no hijack)")


async def t_blocked_pending():
    db = make_session()
    add_user(db, pending_approval=True)
    resp = await call_callback(db)
    assert resp.headers["location"] == "/auth/discord/choose"
    print("PASS  C-bis blocked when existing user is pending_approval")


async def t_blocked_inactive():
    db = make_session()
    add_user(db, is_active=False)
    resp = await call_callback(db)
    assert resp.headers["location"] == "/auth/discord/choose"
    print("PASS  C-bis blocked when existing user is inactive")


async def t_no_email_match():
    db = make_session()
    add_user(db, email="other@example.com")
    resp = await call_callback(db)
    assert resp.headers["location"] == "/auth/discord/choose"
    u = db.query(User).filter(User.email == "other@example.com").one()
    assert u.discord_id is None
    print("PASS  Case C fallback when no email match")


async def t_no_email_from_discord():
    db = make_session()
    add_user(db)
    orig = FakeAsyncClient.me_payload
    FakeAsyncClient.me_payload = {"id": "999", "username": "spy", "email": ""}
    try:
        resp = await call_callback(db)
        assert resp.headers["location"] == "/auth/discord/choose"
        u = db.query(User).filter(User.email == "match@example.com").one()
        assert u.discord_id is None
        print("PASS  Skip C-bis when Discord provides no email")
    finally:
        FakeAsyncClient.me_payload = orig


async def t_case_b_still_works():
    db = make_session()
    add_user(db, discord_id="999", email="other@example.com")
    resp = await call_callback(db)
    assert resp.headers["location"] == "/dashboard", resp.headers["location"]
    print("PASS  Case B still works (discord_id match logs in)")


async def main():
    install_fakes()
    await t_positive()
    await t_blocked_has_discord()
    await t_blocked_pending()
    await t_blocked_inactive()
    await t_no_email_match()
    await t_no_email_from_discord()
    await t_case_b_still_works()
    print("\nAll tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
