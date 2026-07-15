#!/usr/bin/env bash
# Install Sentigon host services as systemd --user units (no sudo needed).
# Unlike loose nohup processes, these are owned by the user systemd manager, so
# they survive shell/session teardown, auto-restart on crash (Restart=always),
# and start/stop together via the sentigon.target.
#
#   bash scripts/install_services.sh          # install + start everything
#   systemctl --user status 'sentigon-*'      # see state + restart counts
#   journalctl --user -u sentigon-perception  # logs for one service
#   systemctl --user stop sentigon.target     # stop all
#
# Note: enabling boot/logout survival needs `sudo loginctl enable-linger $USER`
# (one sudo command, run it yourself); everything else here is sudo-free.
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv)"
OLLAMA="$(command -v ollama)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

# service: name port module
SERVICES=(
  "api 8010 sentigon_api"
  "ingest 8020 sentigon_ingest"
  "mediasource 8055 sentigon_mediasource"
  "perception 8030 sentigon_perception"
  "context 8040 sentigon_context"
  "reason 8050 sentigon_reason"
  "notify 8070 sentigon_notify"
  "search 8060 sentigon_search"
  "mcp 8065 sentigon_mcp"
)

# Ollama (VLM backend) as its own unit, pointed at the real model store.
cat > "$UNIT_DIR/sentigon-ollama.service" <<EOF
[Unit]
Description=Sentigon Ollama (VLM backend)
PartOf=sentigon.target
[Service]
Type=simple
Environment=OLLAMA_MODELS=/usr/share/ollama/.ollama/models
ExecStart=$OLLAMA serve
Restart=always
RestartSec=3
[Install]
WantedBy=sentigon.target
EOF

env_var_name() { echo "$1" | tr '[:lower:]' '[:upper:]'; }

for row in "${SERVICES[@]}"; do
  read -r name port module <<< "$row"
  PORTVAR="$(env_var_name "$name")_HTTP_PORT"
  # reason waits for ollama; everything waits for the network
  extra_after=""
  [ "$name" = "reason" ] && extra_after="sentigon-ollama.service"
  cat > "$UNIT_DIR/sentigon-$name.service" <<EOF
[Unit]
Description=Sentigon $name
After=network-online.target $extra_after
Wants=network-online.target
PartOf=sentigon.target
[Service]
Type=simple
WorkingDirectory=$PROJ
Environment=${PORTVAR}=$port
Environment=MCP_HTTP_PORT=8065
ExecStart=$UV run python -m $module
Restart=always
RestartSec=3
StartLimitIntervalSec=0
[Install]
WantedBy=sentigon.target
EOF
done

# Aggregate target so everything starts/stops together.
cat > "$UNIT_DIR/sentigon.target" <<EOF
[Unit]
Description=Sentigon full host stack
[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
echo "installed units:"
ls "$UNIT_DIR" | grep sentigon
