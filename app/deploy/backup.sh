#!/usr/bin/env bash
# Nightly PostgreSQL dump of the production database (docs/deployment.md).
#
#   crontab -e
#   0 3 * * * /opt/testing-agents/app/deploy/backup.sh >> /var/log/testing-agents-backup.log 2>&1
#
# Dumps are compressed, kept for KEEP_DAYS, and should be copied off the server (S3, Backblaze,
# rsync) by whatever you add after the dump: a backup on the same disk is not a backup.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/testing-agents}"
KEEP_DAYS="${KEEP_DAYS:-14}"

# DB_USER / DB_NAME come from the same .env docker compose uses
set -a
# shellcheck disable=SC1091
. "$REPO_DIR/.env"
set +a

mkdir -p "$BACKUP_DIR"
file="$BACKUP_DIR/db-$(date +%F-%H%M).sql.gz"
docker exec postgres_db pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$file"
find "$BACKUP_DIR" -name 'db-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "$(date -Is) wrote $file ($(du -h "$file" | cut -f1))"
