"""SMTP mailer. Sends transactional emails using OVH SMTP.

Config from env:
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM, SMTP_FROM_NAME

All send_* functions return True on success, False otherwise.
Errors are logged via print (captured by systemd journal).
"""

from __future__ import annotations

import os
import smtplib
import ssl
import traceback
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)


def _smtp_config() -> Optional[dict]:
    host = os.getenv("SMTP_HOST")
    port_raw = os.getenv("SMTP_PORT")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")
    sender = os.getenv("SMTP_FROM", user)
    from_name = os.getenv("SMTP_FROM_NAME", "Eternal Vanguard")
    if not (host and port_raw and user and password and sender):
        return None
    try:
        port = int(port_raw)
    except ValueError:
        return None
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "from_addr": sender,
        "from_name": from_name,
    }


def send_email(to: str, subject: str, html_body: str, text_body: str) -> bool:
    """Send a transactional email. Returns True on success."""
    cfg = _smtp_config()
    if cfg is None:
        print("[mailer] SMTP not configured, cannot send email")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg["from_name"], cfg["from_addr"]))
    msg["To"] = to
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    try:
        context = ssl.create_default_context()
        if cfg["port"] == 465:
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=context, timeout=15) as s:
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as s:
                s.starttls(context=context)
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        return True
    except Exception:
        print(f"[mailer] Failed to send to {to}:")
        traceback.print_exc()
        return False


def send_password_reset(to: str, username: str, reset_url: str) -> bool:
    tmpl_html = _env.get_template("emails/password_reset.html")
    tmpl_text = _env.get_template("emails/password_reset.txt")
    ctx = {"username": username, "reset_url": reset_url}
    return send_email(
        to=to,
        subject="Reset your Eternal Vanguard password",
        html_body=tmpl_html.render(**ctx),
        text_body=tmpl_text.render(**ctx),
    )
