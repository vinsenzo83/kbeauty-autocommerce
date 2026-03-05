#!/usr/bin/env bash
# =============================================================================
# infra/scripts/backup.sh
# KBeauty AutoCommerce – Database Backup Script
#
# Usage:
#   bash infra/scripts/backup.sh                 # Backup to /opt/apps/backups/
#   BACKUP_DIR=/mnt/storage bash infra/scripts/backup.sh
#
# Cron (daily at 02:00):
#   0 2 * * * /opt/apps/kbeauty-autocommerce/infra/scripts/backup.sh >> /var/log/kbeauty-backup.log 2>&1
# =============================================================================

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/apps/kbeauty-autocommerce}"
BACKUP_DIR="${BACKUP_DIR:-/opt/apps/backups}"
COMPOSE_FILE="$APP_DIR/infra/docker-compose.prod.yml"
RETAIN_DAYS="${RETAIN_DAYS:-14}"  # Keep 14 days of backups

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/kbeauty_db_${TIMESTAMP}.sql.gz"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "$(date '+%Y-%m-%d %H:%M:%S') ${GREEN}[backup]${NC} $*"; }
warn() { echo -e "$(date '+%Y-%m-%d %H:%M:%S') ${YELLOW}[warn]  ${NC} $*"; }
err()  { echo -e "$(date '+%Y-%m-%d %H:%M:%S') ${RED}[error] ${NC} $*" >&2; }

# ── Source env vars ───────────────────────────────────────────────────────────
source "$APP_DIR/.env" 2>/dev/null || true
POSTGRES_USER="${POSTGRES_USER:-kbeauty}"
POSTGRES_DB="${POSTGRES_DB:-kbeauty}"

# ── Create backup directory ───────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

log "Starting backup → $BACKUP_FILE"

# ── Dump PostgreSQL ───────────────────────────────────────────────────────────
COMPOSE="docker compose -f $COMPOSE_FILE --env-file $APP_DIR/.env"

if docker exec kbeauty-postgres pg_dump \
    -U "$POSTGRES_USER" \
    "$POSTGRES_DB" \
    --no-owner \
    --no-acl \
    --clean \
    --if-exists \
    | gzip > "$BACKUP_FILE"; then

    SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
    log "Backup complete: $BACKUP_FILE ($SIZE)"
else
    err "Backup FAILED for $BACKUP_FILE"
    rm -f "$BACKUP_FILE"
    exit 1
fi

# Restrict permissions
chmod 600 "$BACKUP_FILE"

# ── Rotate old backups ────────────────────────────────────────────────────────
DELETED=$(find "$BACKUP_DIR" -name "kbeauty_db_*.sql.gz" -mtime "+$RETAIN_DAYS" -delete -print | wc -l)
if [[ $DELETED -gt 0 ]]; then
    log "Rotated $DELETED backup(s) older than $RETAIN_DAYS days"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
TOTAL=$(find "$BACKUP_DIR" -name "kbeauty_db_*.sql.gz" | wc -l)
log "Backup vault: $TOTAL file(s) in $BACKUP_DIR"

# ── Restore instructions ──────────────────────────────────────────────────────
echo ""
echo "To restore:"
echo "  gunzip -c $BACKUP_FILE | docker exec -i kbeauty-postgres psql -U $POSTGRES_USER -d $POSTGRES_DB"
