# Eternal Vanguard

Alliance management website for the *Eternal Vanguard* alliance in **Call of Dragons** — Kingdom 193.

Private operational tool. Deployed and maintained on a self-hosted Hetzner VPS as part of an ongoing career transition from psychiatric nursing to systems administration.

Live: [eternal-vanguard.com](https://eternal-vanguard.com)

---

## Nature of this project

This repository contains the application code for an internal alliance dashboard. It is **not** presented as a software development portfolio.

- The application code (FastAPI backend, Jinja templates, SQLite schema, scoring logic, frontend) was written with substantial AI assistance (Claude). The author's role on the application layer is that of a product owner and progressive learner, not an autonomous developer.
- The **infrastructure and operations** layer — VPS provisioning, deployment, ongoing maintenance — is the author's own work and learning ground. That part is documented separately (see "Infrastructure notes" below).
- The project exists first and foremost to serve the alliance. Public visibility, when granted, is for transparency toward alliance members, not as a demonstration of coding proficiency.

---

## What it does

- Public landing page with two paths: apply to join the alliance in-game, or create a site account as an existing member.
- Internal dashboard: season overview, KPIs, S/A/B/C/D distribution, top performers, roster, farms.
- Scoring engine based on M/P% (cumulative merits ÷ start-of-season power).
- Recruitment tracking with reference codes and public status pages.
- Staff back-office for applications, users, seasons, and settings.
- Player profiles with cross-season history and burn event tracking.

---

## Tech stack

Backend: FastAPI (Python 3.12), SQLAlchemy 2.0, SQLite.
Frontend: Server-rendered Jinja2 templates, Tailwind CSS via CDN, Chart.js.
Auth: argon2 password hashing, server-side sessions.
Serving: Caddy reverse proxy on Aegis (Hetzner CPX22, Ubuntu 24.04 LTS), running as a systemd service.

Stack choices (SQLite over Postgres, server-rendered over SPA, no Docker) were made pragmatically for the current scale (~150 members, single operator). They are not architectural claims the author is prepared to defend in depth.

---

## Infrastructure notes

The application runs on **Aegis**, a Hetzner CPX22 VPS. The infrastructure work — hardening, reverse proxy, service management, DNS, backups — is the author's active learning area for the AFPA TSSR / AIS reconversion path.

Current state of author's understanding on each brick:

- **DNS (OVH → Aegis)**: understood at concept level (A record maps domain to public IP).
- **Caddy reverse proxy**: understood at concept level (forwards traffic, handles TLS via Let's Encrypt).
- **SSH hardening (Ed25519 keys, root login disabled)**: applied following guidance, deeper understanding in progress.
- **systemd service unit**: applied following guidance, not yet independently authored.
- **UFW firewall**: applied following guidance, deeper understanding in progress.

This list is deliberately honest. Items will move from "applied following guidance" to "understood and independently reproducible" as the LFCS preparation progresses.

---

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Then visit http://localhost:8000/.

---

## Deployment

Production runs on Aegis:

- Repo cloned in `/opt/dashboard/app/`.
- Python virtualenv in `/opt/dashboard/venv/`.
- SQLite database in `/opt/dashboard/data/`.
- systemd unit runs the app.
- Caddy handles TLS and reverse proxying to `127.0.0.1:8000`.
- Deploys: `git pull` followed by service restart.

---

## Contributing

Not open to external contributions. This is an operational tool for a specific alliance.

---

## License

Proprietary — see [LICENSE](LICENSE). All rights reserved.
