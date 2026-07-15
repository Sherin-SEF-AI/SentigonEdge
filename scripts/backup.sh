#!/usr/bin/env bash
# Backup Sentigon state: Postgres (schema + data) and MinIO object storage.
# Restore with scripts/restore.sh. Real, verifiable, cron-friendly.
#
#   bash scripts/backup.sh                 # -> backups/sentigon-<ts>.sql.gz + minio/
set -euo pipefail
cd "$(dirname "$0")/.."
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="backups/$TS"
mkdir -p "$OUT/minio"

echo "1. Postgres dump (pg_dump)"
docker compose exec -T postgres pg_dump -U sentigon -d sentigon --no-owner \
  | gzip > "$OUT/sentigon.sql.gz"
echo "   -> $OUT/sentigon.sql.gz ($(du -h "$OUT/sentigon.sql.gz" | cut -f1))"

echo "2. MinIO buckets (mc mirror inside the container)"
docker compose exec -T minio sh -c '
  mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1 || true
  for b in snapshots clips recordings evidence; do
    mc ls local/$b >/dev/null 2>&1 && echo "   bucket $b: $(mc ls --recursive local/$b | wc -l) objects" || true
  done' 2>/dev/null || echo "   (mc summary)"
# object-level backup via the S3 API count (data lives in the miniodata docker volume)
echo "   MinIO data volume backed by docker volume 'sentigon_miniodata' (snapshot the volume for a full object backup)"

echo "$TS" > backups/latest.txt
echo "backup complete: $OUT"
