# Deploy — Eternal Vanguard

Infrastructure files for a fresh Aegis install.

## Contents

- `scripts/backup-db.sh` — nightly SQLite backup via `.backup` API, gzip, 7-day rotation.
- `scripts/restore-db.sh` — interactive restore with integrity check and safety copy.
- `systemd/eternal-vanguard-backup.service` — oneshot backup unit.
- `systemd/eternal-vanguard-backup.timer` — nightly 03:00 UTC schedule.

## Install

```bash
# Scripts
sudo mkdir -p /opt/dashboard/scripts
sudo cp scripts/backup-db.sh /opt/dashboard/scripts/
sudo cp scripts/restore-db.sh /opt/dashboard/scripts/
sudo chown cod-app:cod-app /opt/dashboard/scripts/*.sh
sudo chmod 750 /opt/dashboard/scripts/*.sh

# systemd units
sudo cp systemd/*.service /etc/systemd/system/
sudo cp systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now eternal-vanguard-backup.timer

# Verify
systemctl list-timers eternal-vanguard-backup.timer
```

## Manual test

```bash
sudo systemctl start eternal-vanguard-backup.service
sudo systemctl status eternal-vanguard-backup.service
ls -lh /opt/dashboard/backups/
```

## Restore

```bash
sudo /opt/dashboard/scripts/restore-db.sh /opt/dashboard/backups/eternal_vanguard.db.YYYY-MM-DD_HHMMSS.gz
```
