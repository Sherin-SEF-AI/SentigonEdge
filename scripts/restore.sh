#!/usr/bin/env bash
# Restore a Sentigon Postgres backup. By default restores into a scratch database
# and verifies row counts (safe, non-destructive proof). Pass --into-prod to
# restore over the live database (destructive; confirm first).
#
#   bash scripts/restore.sh backups/<ts>/sentigon.sql.gz            # verify restore
#   bash scripts/restore.sh backups/<ts>/sentigon.sql.gz --into-prod
set -euo pipefail
cd "$(dirname "$0")/.."
DUMP="${1:?usage: restore.sh <sentigon.sql.gz> [--into-prod]}"
MODE="${2:-verify}"
DB="sentigon_restore_test"
[ "$MODE" = "--into-prod" ] && DB="sentigon"

echo "restoring $DUMP into database '$DB'"
if [ "$DB" = "sentigon_restore_test" ]; then
  docker compose exec -T postgres psql -U sentigon -d postgres -c "DROP DATABASE IF EXISTS $DB;" >/dev/null
  docker compose exec -T postgres psql -U sentigon -d postgres -c "CREATE DATABASE $DB;" >/dev/null
fi
gunzip -c "$DUMP" | docker compose exec -T postgres psql -U sentigon -d "$DB" >/dev/null 2>&1

echo "verifying restored data:"
docker compose exec -T postgres psql -U sentigon -d "$DB" -c "
SELECT 'incidents' t, count(*) FROM incidents
UNION ALL SELECT 'signatures', count(*) FROM signatures
UNION ALL SELECT 'cameras', count(*) FROM cameras
UNION ALL SELECT 'users', count(*) FROM users
UNION ALL SELECT 'evidence_records', count(*) FROM evidence_records
UNION ALL SELECT 'audit_logs', count(*) FROM audit_logs;"
if [ "$DB" = "sentigon_restore_test" ]; then
  docker compose exec -T postgres psql -U sentigon -d postgres -c "DROP DATABASE $DB;" >/dev/null
  echo "scratch restore verified + cleaned up (non-destructive)"
fi
