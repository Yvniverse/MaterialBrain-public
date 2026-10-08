#!/bin/sh
set -eu
if [ "$#" -ne 1 ]; then echo "Usage: ./deploy/restore/restore.sh storage/backups/db_TIMESTAMP.dump"; exit 2; fi
DB_FILE="$1"
[ -f "$DB_FILE" ] || { echo "Backup not found: $DB_FILE"; exit 2; }
if [ -f .env ]; then
  POSTGRES_USER="${POSTGRES_USER:-$(sed -n 's/^POSTGRES_USER=//p' .env | tail -1)}"
  POSTGRES_DB="${POSTGRES_DB:-$(sed -n 's/^POSTGRES_DB=//p' .env | tail -1)}"
  MATERIALBRAIN_STORAGE_ROOT="${MATERIALBRAIN_STORAGE_ROOT:-$(sed -n 's/^MATERIALBRAIN_STORAGE_ROOT=//p' .env | tail -1)}"
fi
STORAGE_ROOT="${MATERIALBRAIN_STORAGE_ROOT:-./storage}"
BACKUP_ROOT="$(cd "$STORAGE_ROOT/backups" && pwd -P)"
DB_PARENT="$(cd "$(dirname "$DB_FILE")" && pwd -P)"
DB_NAME="$(basename "$DB_FILE")"
case "$DB_PARENT" in "$BACKUP_ROOT"|"$BACKUP_ROOT"/*) ;; *) echo "Restore file must be under the configured backups directory"; exit 2;; esac
case "$DB_NAME" in db_*.dump) ;; *) echo "Expected db_TIMESTAMP.dump"; exit 2;; esac
export MATERIALBRAIN_STORAGE_ROOT POSTGRES_USER POSTGRES_DB
PROTECTIVE_DIR="$(mktemp -d "$BACKUP_ROOT/restore_guard_XXXXXX")"
BACKUP_DIR="$PROTECTIVE_DIR" BACKUP_SKIP_RETENTION=1 sh ./deploy/backup/backup.sh
docker compose stop backend
docker compose exec -T db pg_restore -U "${POSTGRES_USER:-materialbrain_public}" -d "${POSTGRES_DB:-materialbrain_public}" --clean --if-exists --no-owner < "$DB_FILE"
STAMP="${DB_NAME#db_}"; STAMP="${STAMP%.dump}"
ATT_FILE="$DB_PARENT/attachments_${STAMP}.tar.gz"
if [ -f "$ATT_FILE" ]; then mkdir -p "$STORAGE_ROOT/attachments"; tar -xzf "$ATT_FILE" -C "$STORAGE_ROOT/attachments"; fi
EVIDENCE_FILE="$DB_PARENT/evidence_${STAMP}.tar.gz"
if [ -f "$EVIDENCE_FILE" ]; then
  mkdir -p "$STORAGE_ROOT/evidence"
  tar -xzf "$EVIDENCE_FILE" -C "$STORAGE_ROOT/evidence"
fi
docker compose start backend
echo "Restore completed. Review: docker compose logs backend"
