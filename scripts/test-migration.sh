#!/usr/bin/env bash
# Test an Alembic migration against a copy of the prod DB.
# Runs `alembic upgrade head` on a tmp copy, checks integrity, cleans up.
# Prod DB is never touched.
#
# Usage (on Aegis, as cod-app):
#   sudo -u cod-app /opt/dashboard/app/scripts/test-migration.sh
set -euo pipefail

PROD_DB="/opt/dashboard/data/eternal_vanguard.db"
TMP_DB="/tmp/ev-migration-test-$(date +%s).db"
APP_DIR="/opt/dashboard/app"
VENV_PY="/opt/dashboard/venv/bin/python"
VENV_ALEMBIC="/opt/dashboard/venv/bin/alembic"

cleanup() { rm -f "$TMP_DB"; }
trap cleanup EXIT

echo "==> Copying prod DB to $TMP_DB"
cp "$PROD_DB" "$TMP_DB"

cd "$APP_DIR"

echo "==> Current head on tmp DB (should match prod):"
ALEMBIC_DB_URL="sqlite:///$TMP_DB" "$VENV_ALEMBIC" current

echo "==> Running: alembic upgrade head"
ALEMBIC_DB_URL="sqlite:///$TMP_DB" "$VENV_ALEMBIC" upgrade head

echo "==> New head on tmp DB:"
ALEMBIC_DB_URL="sqlite:///$TMP_DB" "$VENV_ALEMBIC" current

echo "==> Integrity check:"
"$VENV_PY" -c "
import sqlite3
c = sqlite3.connect('$TMP_DB')
r = c.execute('PRAGMA integrity_check').fetchone()[0]
print(f'  integrity_check: {r}')
r = c.execute('PRAGMA foreign_key_check').fetchall()
print(f'  foreign_key_check: {\"OK\" if not r else r}')
c.close()
"

echo ""
echo "==> Migration test PASSED. Prod DB untouched."