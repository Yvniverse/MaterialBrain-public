#!/bin/sh
set -eu
STAMP="$(date +%Y-%m-%d_%H%M%S)"
if [ -n "${POSTGRES_HOST:-}" ]; then
  BACKUP_DIR="${BACKUP_DIR:-/backups}"
  ATTACHMENT_DIR="${ATTACHMENT_DIR:-/attachments}"
  EVIDENCE_DIR="${EVIDENCE_DIR:-/evidence}"
else
  if [ -f .env ]; then
    POSTGRES_USER="${POSTGRES_USER:-$(sed -n 's/^POSTGRES_USER=//p' .env | tail -1)}"
    POSTGRES_DB="${POSTGRES_DB:-$(sed -n 's/^POSTGRES_DB=//p' .env | tail -1)}"
    MATERIALBRAIN_STORAGE_ROOT="${MATERIALBRAIN_STORAGE_ROOT:-$(sed -n 's/^MATERIALBRAIN_STORAGE_ROOT=//p' .env | tail -1)}"
  fi
  STORAGE_ROOT="${MATERIALBRAIN_STORAGE_ROOT:-./storage}"
  BACKUP_DIR="${BACKUP_DIR:-$STORAGE_ROOT/backups}"
  ATTACHMENT_DIR="${ATTACHMENT_DIR:-$STORAGE_ROOT/attachments}"
  EVIDENCE_DIR="${EVIDENCE_DIR:-$STORAGE_ROOT/evidence}"
fi
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
mkdir -p "$BACKUP_DIR"
DB_FILE="$BACKUP_DIR/db_${STAMP}.dump"
ATT_FILE="$BACKUP_DIR/attachments_${STAMP}.tar.gz"
EVIDENCE_FILE="$BACKUP_DIR/evidence_${STAMP}.tar.gz"
MANIFEST="$BACKUP_DIR/backup_manifest_${STAMP}.json"

if command -v pg_dump >/dev/null 2>&1 && [ -n "${POSTGRES_HOST:-}" ]; then
  export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
  pg_dump -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "$DB_FILE"
else
  docker compose exec -T db pg_dump -U "${POSTGRES_USER:-materialbrain_public}" -d "${POSTGRES_DB:-materialbrain_public}" -Fc > "$DB_FILE"
fi
tar -czf "$ATT_FILE" -C "$ATTACHMENT_DIR" .
mkdir -p "$EVIDENCE_DIR"
tar -czf "$EVIDENCE_FILE" -C "$EVIDENCE_DIR" .
DB_SHA="$(sha256sum "$DB_FILE" | cut -d' ' -f1)"
ATT_SHA="$(sha256sum "$ATT_FILE" | cut -d' ' -f1)"
EVIDENCE_SHA="$(sha256sum "$EVIDENCE_FILE" | cut -d' ' -f1)"
cat > "$MANIFEST" <<EOF
{"timestamp":"$STAMP","database":"$(basename "$DB_FILE")","database_sha256":"$DB_SHA","attachments":"$(basename "$ATT_FILE")","attachments_sha256":"$ATT_SHA","evidence":"$(basename "$EVIDENCE_FILE")","evidence_sha256":"$EVIDENCE_SHA"}
EOF
if [ "${BACKUP_SKIP_RETENTION:-0}" != "1" ]; then
  find "$BACKUP_DIR" -type f -mtime "+$RETENTION_DAYS" \( -name 'db_*.dump' -o -name 'attachments_*.tar.gz' -o -name 'evidence_*.tar.gz' -o -name 'backup_manifest_*.json' \) -delete
fi
echo "Backup completed: $MANIFEST"
