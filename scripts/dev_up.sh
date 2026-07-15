#!/usr/bin/env bash
# Bring up the full Sentigon host-service stack (idempotent). Infra runs in Docker;
# the Python services + web console run on the host via uv. Safe to re-run: it only
# starts what is not already healthy. Logs land in /tmp/sentigon-<service>.log.
set -u
cd "$(dirname "$0")/.."

log() { echo "[dev_up] $*"; }
up() { curl -s -o /dev/null --max-time 2 "http://localhost:$1/healthz" 2>/dev/null; }
wait_up() { for _ in $(seq 1 "${2:-40}"); do up "$1" && return 0; sleep 1; done; return 1; }
run() { # name port "ENV=v ..." module
  local name="$1" port="$2" envs="$3" mod="$4"
  if up "$port"; then log "$name already up (:$port)"; return; fi
  log "starting $name (:$port)"
  env $envs nohup uv run python -m "$mod" > "/tmp/sentigon-$name.log" 2>&1 &
}

log "infra (docker compose)"; docker compose up -d >/dev/null 2>&1
bash scripts/wait_healthy.sh >/dev/null 2>&1 || true

run api 8010 "API_HTTP_PORT=8010" sentigon_api
run ingest 8020 "INGEST_HTTP_PORT=8020" sentigon_ingest
wait_up 8010 30; wait_up 8020 30

if ! up 8055; then
  log "media-source: republishing internet streams + onboarding"
  MEDIASOURCE_HTTP_PORT=8055 nohup uv run python -m sentigon_mediasource > /tmp/sentigon-mediasource.log 2>&1 &
  sleep 22
fi

run perception 8030 "PERCEPTION_HTTP_PORT=8030" sentigon_perception
wait_up 8030 60; sleep 6
run context 8040 "CONTEXT_HTTP_PORT=8040" sentigon_context
run reason 8050 "REASON_HTTP_PORT=8050" sentigon_reason
run notify 8070 "NOTIFY_HTTP_PORT=8070" sentigon_notify
run search 8060 "SEARCH_HTTP_PORT=8060" sentigon_search
run dispatch 8081 "DISPATCH_HTTP_PORT=8081" sentigon_dispatch
run fleet 8082 "FLEET_HTTP_PORT=8082" sentigon_fleet
run crosssite 8086 "CROSSSITE_HTTP_PORT=8086" sentigon_crosssite

# MCP server (streamable-http; /healthz is not exposed, so gate on the port).
if ! ss -ltn 2>/dev/null | grep -q ':8065'; then
  log "starting mcp (:8065)"
  MCP_HTTP_PORT=8065 nohup uv run python -m sentigon_mcp > /tmp/sentigon-mcp.log 2>&1 &
fi

if ! curl -s -o /dev/null --max-time 2 http://localhost:3001 2>/dev/null; then
  log "web console (:3001)"
  (cd web && nohup npm run dev > /tmp/sentigon-web.log 2>&1 &)
fi
log "done. perception + search load models over ~30-60s."
