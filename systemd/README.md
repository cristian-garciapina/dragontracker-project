# Systemd units for Eternal Vanguard

Units versioned here are canonical. `/etc/systemd/system/*` on Aegis
should be symlinks or straight copies of these.

## farlight-pull.{service,timer}

Nightly job that fetches K193 stats from the Farlight API and re-scores
the active season. See `app/farlight_pull.py`.

### Install on Aegis (as root)

```bash
sudo cp /opt/dashboard/app/systemd/farlight-pull.service /etc/systemd/system/
sudo cp /opt/dashboard/app/systemd/farlight-pull.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now farlight-pull.timer
```

### Operate

```bash
# Run now (bypasses the timer)
sudo systemctl start farlight-pull.service

# Watch the last run
sudo journalctl -u farlight-pull.service -n 100 --no-pager

# Next scheduled run
systemctl list-timers farlight-pull.timer --no-pager
```

### Rollback

```bash
sudo systemctl disable --now farlight-pull.timer
sudo rm /etc/systemd/system/farlight-pull.{service,timer}
sudo systemctl daemon-reload
```
