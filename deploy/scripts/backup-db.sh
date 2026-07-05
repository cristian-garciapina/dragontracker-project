#!/bin/bash
# Nightly SQLite backup via .backup API (safe during writes).
# Keeps 7 days rolling. Runs as user cod-app via systemd timer.
set -euo pipefail

DB="/opt/dashboard/data/eternal_vanguard.db"
DEST="/opt/dashboard/backups"
STAMP="$(date -u +%Y-%m-%d_%H%M%S)"
OUT="${DEST}/eternal_vanguard.db.${STAMP}"

sqlite3 "$DB" ".backup '${OUT}'"

INTEGRITY="$(sqlite3 "$OUT" 'PRAGMA integrity_check;')"
if [ "$INTEGRITY" != "ok" ]; then
    echo "ERROR: integrity check failed for $OUT: $INTEGRITY" >&2
    rm -f "$OUT"
    exit 1
fi

gzip -9 "$OUT"
FINAL="${OUT}.gz"

cd "$DEST"
ls -1t eternal_vanguard.db.*.gz 2>/dev/null | tail -n +8 | xargs -r rm --

SIZE="$(du -h "$FINAL" | cut -f1)"
COUNT="$(ls -1 eternal_vanguard.db.*.gz 2>/dev/null | wc -l)"
echo "OK: $FINAL ($SIZE) — $COUNT backup(s) retained"
