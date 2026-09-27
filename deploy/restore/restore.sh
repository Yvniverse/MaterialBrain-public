#!/bin/sh
set -eu
if [ "$#" -ne 1 ]; then echo "Usage: ./deploy/restore/restore.sh storage/backups/db_TIMESTAMP.dump"; exit 2; fi
DB_FILE="$1"
[ -f "$DB_FILE" ] || { echo "Backup not found: $DB_FILE"; exit 2; }
case "$DB_FILE" in storage/backups/*.dump|./storage/backups/*.dump) ;; *) echo "Restore file must be under storage/backups"; exit 2;; esac
if [ -f .env ]; then
  POSTGRES_USER="${POSTGRES_USER:-$(sed -n 's/^POSTGRES_USER=//p' .env | tail -1)}"
  POSTGRES_DB="${POSTGRES_DB:-$(sed -n 's/^POSTGRES_DB=//p' .env | tail -1)}"
fi
sh ./deploy/backup/backup.sh
docker compose stop backend
docker compose exec -T db pg_restore -U "${POSTGRES_USER:-pengka}" -d "${POSTGRES_DB:-pengka_material}" --clean --if-exists --no-owner < "$DB_FILE"
ATT_FILE="${DB_FILE/db_/attachments_}"; ATT_FILE="${ATT_FILE/.dump/.tar.gz}"
if [ -f "$ATT_FILE" ]; then tar -xzf "$ATT_FILE" -C storage/attachments; fi
EVIDENCE_FILE="${DB_FILE/db_/evidence_}"; EVIDENCE_FILE="${EVIDENCE_FILE/.dump/.tar.gz}"
if [ -f "$EVIDENCE_FILE" ]; then
  mkdir -p storage/evidence
  tar -xzf "$EVIDENCE_FILE" -C storage/evidence
fi
docker compose start backend
echo "Restore completed. Review: docker compose logs backend"
