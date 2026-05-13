#!/usr/bin/env bash
set -euo pipefail

DATABASE_URL="${PEACEPULSE_DATABASE_URL:-sqlite:////app/data/peacepulse-prod.db}"
if [[ "$DATABASE_URL" != sqlite:///* ]]; then
  echo "Only sqlite PEACEPULSE_DATABASE_URL values can be backed up by this script." >&2
  exit 1
fi
DB_PATH="${DATABASE_URL#sqlite:///}"
BACKUP_DIR="${PEACEPULSE_BACKUP_DIR:-/app/data/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$BACKUP_DIR"
sqlite3 "$DB_PATH" "PRAGMA wal_checkpoint(TRUNCATE);"
sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/peacepulse-$STAMP.db'"
sqlite3 "$BACKUP_DIR/peacepulse-$STAMP.db" "PRAGMA integrity_check;"
find "$BACKUP_DIR" -type f -name 'peacepulse-*.db' -mtime +14 -delete
