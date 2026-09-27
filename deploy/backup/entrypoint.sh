#!/bin/sh
set -eu
BACKUP_CRON="${BACKUP_CRON:-0 2 * * *}"
echo "$BACKUP_CRON /usr/local/bin/backup.sh >> /backups/backup.log 2>&1" > /etc/crontabs/root
/usr/local/bin/backup.sh
exec crond -f -l 2
