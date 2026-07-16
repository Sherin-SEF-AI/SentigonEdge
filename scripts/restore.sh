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
if [ "$DB" = "sentigon" ]; then
  echo "WARNING: this OVERWRITES the live '$DB' database. Stop the Sentigon services first."
  read -r -p "Type 'yes' to proceed: " confirm
  [ "$confirm" = "yes" ] || { echo "aborted"; exit 1; }
fi

# Drop + recreate the target so the dump is not applied over an existing schema
# (which silently errored on every 'already exists' before). Terminate open
# connections first, then restore with errors surfaced (ON_ERROR_STOP, no 2>/dev/null).
docker compose exec -T postgres psql -U sentigon -d postgres -v ON_ERROR_STOP=1 -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB' AND pid<>pg_backend_pid();" >/dev/null
docker compose exec -T postgres psql -U sentigon -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $DB;"
docker compose exec -T postgres psql -U sentigon -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DB;"
gunzip -c "$DUMP" | docker compose exec -T postgres psql -U sentigon -d "$DB" -v ON_ERROR_STOP=1

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
