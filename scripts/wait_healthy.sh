#!/usr/bin/env bash
# Block until every infra service answers a real readiness probe (not just
# "container started"). Used by `make up`. Host has curl; DB/broker probes run
# inside their containers via docker compose exec.
set -u
cd "$(dirname "$0")/.."

if [ -f .env ]; then set -a; . ./.env; set +a; fi
DC="docker compose"
PGUSER="${POSTGRES_USER:-sentigon}"
PGDB="${POSTGRES_DB:-sentigon}"
DEADLINE=$(( $(date +%s) + 240 ))

probe() { # label, command...
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then printf '  \033[32mok  \033[0m %s\n' "$label"; return 0
  else printf '  \033[33m... \033[0m %s\n' "$label"; return 1; fi
}

rp_health() { $DC exec -T redpanda rpk cluster health -X admin.hosts=localhost:9644 2>/dev/null | grep -q 'Healthy:.*true'; }

echo "waiting for the sentigon stack to become healthy..."
while :; do
  ok=0; total=6
  probe postgres  $DC exec -T postgres pg_isready -U "$PGUSER" -d "$PGDB" && ok=$((ok+1))
  probe redis     $DC exec -T redis redis-cli ping && ok=$((ok+1))
  probe redpanda  rp_health && ok=$((ok+1))
  probe qdrant    curl -sf http://localhost:6335/readyz && ok=$((ok+1))
  probe minio     curl -sf http://localhost:9002/minio/health/live && ok=$((ok+1))
  probe mediamtx  curl -sf http://localhost:9997/v3/config/global/get && ok=$((ok+1))
  echo "  --> $ok/$total healthy"
  if [ "$ok" -eq "$total" ]; then echo "stack healthy."; exit 0; fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    echo "timeout waiting for stack; current status:"; $DC ps
    exit 1
  fi
  sleep 4
done
