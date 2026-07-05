#!/bin/bash
# Safely restore SQLite DB from a compressed backup.
# Usage: ./restore-db.sh /opt/dashboard/backups/eternal_vanguard.db.YYYY-MM-DD_HHMMSS.gz
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <backup.gz>"
    echo ""
    echo "Available backups:"
    ls -lht /opt/dashboard/backups/eternal_vanguard.db.*.gz 2>/dev/null || echo "  (none)"
    exit 1
fi

BACKUP="$1"
DB="/opt/dashboard/data/eternal_vanguard.db"

if [ ! -f "$BACKUP" ]; then
    echo "ERROR: backup file does not exist: $BACKUP" >&2
    exit 1
fi

if ! gzip -t "$BACKUP" 2>/dev/null; then
    echo "ERROR: file is not a valid gzip archive: $BACKUP" >&2
    exit 1
fi

echo "About to restore:"
echo "  from: $BACKUP ($(du -h "$BACKUP" | cut -f1))"
echo "  to:   $DB (current: $(du -h "$DB" 2>/dev/null | cut -f1 || echo missing))"
echo ""
read -p "Type 'yes' to proceed: " confirm
if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

# Stop app
sudo systemctl stop eternal-vanguard

# Backup current DB before overwrite
STAMP="$(date -u +%Y-%m-%d_%H%M%S)"
sudo -u cod-app cp "$DB" "${DB}.pre-restore-${STAMP}" 2>/dev/null || true

# Decompress to temp, verify integrity, then swap
TMP="${DB}.restoring.$$"
gunzip -c "$BACKUP" > "$TMP"

INTEGRITY="$(sqlite3 "$TMP" 'PRAGMA integrity_check;')"
if [ "$INTEGRITY" != "ok" ]; then
    echo "ERROR: integrity check failed on restored DB: $INTEGRITY" >&2
    rm -f "$TMP"
    sudo systemctl start eternal-vanguard
    exit 1
fi

sudo -u cod-app mv "$TMP" "$DB"
sudo chown cod-app:cod-app "$DB"

sudo systemctl start eternal-vanguard
sleep 1

echo ""
echo "Restore complete."
echo "Pre-restore snapshot saved as: ${DB}.pre-restore-${STAMP}"
sudo systemctl status eternal-vanguard --no-pager | head -6
