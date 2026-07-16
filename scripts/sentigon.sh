#!/usr/bin/env bash
# sentigon.sh — one control script for the whole SentigonEdge stack.
#
# The stack is two layers: Docker infra (Postgres, Redpanda, Qdrant, MinIO,
# MediaMTX, Keycloak, Grafana, ...) and the host services + web console, which
# run as systemd --user units grouped under `sentigon.target`. This drives both.
#
#   scripts/sentigon.sh start      # infra up (wait healthy) then all services
#   scripts/sentigon.sh stop       # all services then infra (containers kept)
#   scripts/sentigon.sh restart    # stop then start
#   scripts/sentigon.sh bounce     # restart only the services (leave infra up)
#   scripts/sentigon.sh status     # docker + service + per-port health
#   scripts/sentigon.sh logs [svc] # follow journald logs (one service, or all)
#
# Prereqs: services installed as user units (bash scripts/install_services.sh).
# For a non-systemd dev run instead, use scripts/dev_up.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

DC="docker compose"
TARGET="sentigon.target"

# service:healthport  (web serves / not /healthz; mcp/ollama have no /healthz)
PORTS=(
  api:8010 ingest:8020 perception:8030 context:8040 reason:8050
  search:8060 notify:8070 dispatch:8081 fleet:8082 crosssite:8086
  mediasource:8055 web:3001
)

log() { printf '[sentigon] %s\n' "$*"; }

have_target() { systemctl --user cat "$TARGET" >/dev/null 2>&1; }

require_target() {
  if ! have_target; then
    log "services are not installed as systemd --user units ('$TARGET' missing)."
    log "install them once with:  bash scripts/install_services.sh"
    log "(or run a non-systemd dev stack with:  bash scripts/dev_up.sh)"
    exit 1
  fi
}

start() {
  log "infra: docker compose up -d"
  $DC up -d
  if [ -x scripts/wait_healthy.sh ]; then
    log "waiting for infra to be healthy"
    bash scripts/wait_healthy.sh || log "warn: some infra not healthy yet (continuing)"
  fi
  require_target
  log "services: systemctl --user start $TARGET"
  systemctl --user start "$TARGET"
  log "up. check with:  $0 status"
}

stop() {
  if have_target; then
    log "services: systemctl --user stop $TARGET"
    systemctl --user stop "$TARGET" || true
  fi
  log "infra: docker compose stop"
  $DC stop || true
  log "stopped. containers + volumes preserved (use 'docker compose down -v' to wipe data)."
}

bounce() {
  require_target
  log "restarting services only (infra left running)"
  systemctl --user restart "$TARGET"
  log "done. check with:  $0 status"
}

status() {
  echo "== docker infra =="
  $DC ps --format 'table {{.Name}}\t{{.Status}}' 2>/dev/null || $DC ps || true
  echo
  echo "== services ($TARGET: $(systemctl --user is-active "$TARGET" 2>/dev/null || echo unknown)) =="
  if have_target; then
    systemctl --user --no-legend list-units 'sentigon-*.service' 2>/dev/null \
      | awk '{printf "  %-26s %s\n", $1, $4}'
  else
    echo "  (not installed as systemd units)"
  fi
  echo
  echo "== health (/healthz) =="
  for p in "${PORTS[@]}"; do
    name="${p%%:*}"; port="${p##*:}"
    [ "$name" = web ] && path="/" || path="/healthz"
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://localhost:${port}${path}" 2>/dev/null || echo "---")
    printf "  %-12s :%-5s %s\n" "$name" "$port" "$code"
  done
}

logs() {
  local svc="${1:-}"
  if [ -n "$svc" ]; then
    journalctl --user -u "sentigon-${svc}" -n 100 -f
  else
    journalctl --user -u 'sentigon-*' -n 50 -f
  fi
}

case "${1:-}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  bounce)  bounce ;;
  status)  status ;;
  logs)    shift; logs "${1:-}" ;;
  *) echo "usage: $0 {start|stop|restart|bounce|status|logs [service]}"; exit 1 ;;
esac
