# Eternal Vanguard

Alliance management website for the *Eternal Vanguard* alliance in **Call of Dragons** — Kingdom 193.

Built and iterated by a solo developer as part of a career transition from psychiatric nursing to systems administration. The project doubles as a production deployment exercise on a self-hosted Hetzner VPS (codename *Aegis*).

Live: [eternal-vanguard.com](https://eternal-vanguard.com)

---

## What it does

- Serves a public landing page for the alliance with two distinct paths: applying to join the alliance in-game, or creating a site account as an existing member.
- Provides an internal dashboard for members: alliance overview, season KPIs, distribution S/A/B/C/D, top performers, roster and farms.
- Runs a transparent scoring engine (M/P% = merits ÷ start power) with staff-configurable thresholds; results computed per season and persisted.
- Public recruitment tracking: every applicant gets a short reference code (`EV-YYYY-XXXX`) and a rate-limited status page — no account required.
- Staff back-office for applications (approve / reject with public reason / delete), users (approval flow), seasons (upload wizard), and settings (audit-logged, auto-recompute).
- Player profiles with cross-season history, staff-only notes, and publicly visible burn events for the season.

---

## Tech stack

| Layer          | Choice                          | Why                                                    |
|----------------|---------------------------------|--------------------------------------------------------|
| Backend        | FastAPI (Python 3.12)           | Async I/O, native Excel parsing via pandas.            |
| Templates      | Jinja2 (server-rendered HTML)   | One service to deploy, no build step.                  |
| Styling        | Tailwind CSS via CDN            | Distinctive dark theme, zero tooling.                  |
| Charts         | Inline SVG + Chart.js           | Custom donuts, sparklines when needed.                 |
| Database       | SQLite                          | Single file, fits the scale (~150 members).            |
| ORM            | SQLAlchemy 2.0 (declarative)    | Typed schemas, clean relationships.                    |
| Auth           | argon2 + server-side sessions   | httpOnly cookie, 30-day lifetime.                      |
| Reverse proxy  | Caddy                           | Automatic HTTPS via Let's Encrypt.                     |
| Process manager| systemd                         | Standard Linux, restarts on failure.                   |
| Firewall       | UFW                             | Default-deny, explicit allow rules.                    |

**Not Docker.** A single Python service on 4 GB RAM doesn't benefit from containerization; the operational cost outweighs the return here. Revisit if a worker, an LLM inference process, or Postgres ever join the stack.

**Not a SPA.** No React / Next / SvelteKit. This is a dashboard, not a consumer app. Server-rendered Jinja is faster to build and maintain.

---

## Architecture

```
Internet → Caddy (:80, :443) → FastAPI (127.0.0.1:8000) → SQLite (file)
              ↑                       ↑
       TLS + ACME              Application logic
       reverse proxy           Excel ingestion
                               Scoring engine
                               Session store
```

FastAPI is bound to localhost only — Caddy is the sole entry point. UFW keeps only ports 22, 80, 443 open.

---

## Features

### Public

- Landing with two clearly separated CTAs: `/apply` (join the alliance in-game) and `/signup` (create a site account).
- Recruitment form with reference generation, public status page at `/apply/status/{ref}` (rate limited), and a lookup form at `/apply/status`.

### Members (authenticated)

- `/dashboard` — season overview, KPI cards, S/A/B/C/D distribution donut, top performers.
- `/roster` — full members table, sticky ID column, click-to-copy IDs, filters, 📝 badge on players with staff notes (staff only), 🔥 badge on players burned this season (visible to all, with count if > 1).
- `/farms` — dedicated view for farm accounts (Start Power ≤ 15M).
- `/player/{character_id}` — profile with identity, current vs previous M/P% and delta, full season history, staff notes (staff only), season burns (public list, staff can record and delete).
- `/profile` — self-service account editing.

### Staff (R4 / R5)

- `/staff/applications` — recruitment queue with tabs by status, Reject-with-public-reason (mandatory ≤ 500 chars), delete on all statuses via native `<dialog>` confirmation.
- `/staff/users` — pending approvals, role changes, deactivation.
- `/staff/seasons` — upload wizard, season lifecycle, snapshot management.
- `/staff/settings` — scoring thresholds, farm threshold, ingestion rules, audit log, auto-recompute on relevant changes.

### Scoring engine

**M/P% = cumulative season merits ÷ start-of-season power × 100.**

- Grades: S ≥ 10 %, A ≥ 7 %, B ≥ 4 %, C ≥ 1 %, D catch-all.
- Farm accounts (Start Power ≤ 15 M) are excluded from scoring and listed on `/farms`. Thresholds are staff-configurable.
- Statuses: KEEP (S/A/B), WATCH (C), EXPEL (D), FARM.
- Recomputed automatically when a new cumulative snapshot is ingested or when scoring settings change.

---

## Data pipeline

Manual ingestion, per snapshot:

1. Download the daily export from the official Farlight portal `cod-game-tools.farlightgames.com/topn` (17-column format).
2. Place the file on Aegis under `data/incoming/`.
3. Run `ingest.py` as user `cod-app`.

Snapshots are idempotent on `(source_filename, date_start, date_end)` and automatically bootstrap missing members. A season needs one "start-of-season" snapshot (`seasons.start_snapshot_id`) plus at least one cumulative snapshot to produce scores.

---

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate        # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Then visit http://localhost:8000/.

---

## Deployment

Production runs on **Aegis** (Hetzner CPX22, Ubuntu 24.04 LTS):

- Repo cloned in `/opt/dashboard/app/`, owned by group `cod-app`.
- Venv in `/opt/dashboard/venv/`, DB in `/opt/dashboard/data/`.
- systemd unit `eternal-vanguard.service` runs the app as user `cod-app`.
- Caddy handles TLS termination and reverse proxying to `127.0.0.1:8000`.
- Deploys are pulls from GitHub, followed by `sudo systemctl restart eternal-vanguard`.

---

## Contributing

Not open to external contributions at this stage. The project is a production tool for a specific alliance; the code is public for transparency and portfolio reasons.

Bug reports welcome via GitHub Issues.

---

## Status

- [x] Phase 1 — Infrastructure (SSH hardening, UFW, Caddy, DNS, systemd).
- [x] Phase 2 — SQLite schema, Excel ingestion, member bootstrapping.
- [x] Phase 3 — Scoring engine, M/P% algorithm, categories, statuses.
- [x] Phase 4 — Frontend: dashboard, roster, farms, hamburger nav.
- [x] Phase 5 — Auth, RBAC, self-service signup, staff approvals.
- [x] Phase 6 — Seasons management, upload wizard, settings + audit log.
- [x] Phase 7 — Recruitment: `/apply`, back-office, tracking references.
- [x] Phase 8 — Player profiles, staff notes, season burns.
- [ ] Phase 9 — Nightly DB backup + systemd timer.
- [ ] Phase 10 — Alembic baseline + staging on Aegis.
- [ ] Phase 11 — Hardening: fail2ban, CSP/HSTS headers, rate limiting.

---

## License

MIT — see [LICENSE](LICENSE).
