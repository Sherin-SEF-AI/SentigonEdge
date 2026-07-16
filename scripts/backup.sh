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

echo "2. MinIO objects -> $OUT/minio (real object copy via the S3 API)"
uv run python - "$OUT/minio" <<'PY'
import os
import sys

from minio import Minio
from sentigon_common.config import settings

dest = sys.argv[1]
client = Minio(
    settings.minio_endpoint,
    access_key=settings.minio_access_key,
    secret_key=settings.minio_secret_key,
    secure=settings.minio_secure,
)
total = 0
for bucket in settings.all_buckets:
    if not client.bucket_exists(bucket):
        print(f"   bucket {bucket}: absent, skipped")
        continue
    n = 0
    for obj in client.list_objects(bucket, recursive=True):
        target = os.path.join(dest, bucket, obj.object_name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        client.fget_object(bucket, obj.object_name, target)
        n += 1
    total += n
    print(f"   bucket {bucket}: {n} objects copied")
print(f"   MinIO objects backed up: {total}")
PY

echo "$TS" > backups/latest.txt
echo "backup complete: $OUT"
