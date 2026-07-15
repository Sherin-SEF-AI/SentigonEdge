# Sentigon Operator Runbook

Operational reference for running Sentigon in production. Pairs with
[README.md](README.md) (Kubernetes/RunPod) and [RUNPOD.md](RUNPOD.md) (GPU tier).

## One-command deploy

- **Local / on-box (dev + single-node prod):** `make dev` (or `bash scripts/dev_up.sh`)
  brings up the infra (Docker) + all host services. For unattended operation the
  services run as **systemd --user units** (auto-restart, survive session/crash):
  ```
  bash scripts/install_services.sh          # install + start all units
  systemctl --user status 'sentigon-*'      # health + restart counts
  systemctl --user start sentigon.target    # start everything
  ```
  Optional boot survival: `sudo loginctl enable-linger $USER`.
- **Kubernetes / RunPod:** `helm install sentigon deploy/helm/sentigon ...`
  (see README.md). The post-install Job runs `alembic upgrade head` + seeds config.

## Secrets

Set via environment / `.env` (never commit real values): `JWT_SECRET_KEY`
(the API refuses to start if unset/weak), `SERVICE_TOKEN`, `DEFAULT_ADMIN_PASSWORD`,
`MINIO_*`, `NOTIFY_WEBPUSH_VAPID_KEY`, `HF_TOKEN` (RunPod). In k8s these are a
Secret; the Helm chart wires them. SSO uses the OIDC issuer (`OIDC_ISSUER`).

## Health + self-diagnosis

- Per-service: `systemctl --user status sentigon-<svc>`; logs `journalctl --user -u sentigon-<svc>`.
- Aggregate: the **Health** console screen, Prometheus (`:9090`), Grafana (`:3002`), Loki logs.
- **Golden-path self-test:** `uv run python -m bench.golden_path` (also on a 15-min
  systemd timer). Non-zero exit = a pipeline stage is not producing.
- Camera tamper/blindness raises a real incident; a camera going dark self-alerts
  and escalates on the on-call chain.

## Common failure modes + recovery

| Symptom | Cause | Recovery |
|---|---|---|
| A service is down | crash | systemd auto-restarts it; `systemctl --user restart sentigon-<svc>` to force |
| Streams offline, wall blank | media-source publishers dropped | `systemctl --user restart sentigon-mediasource`; watchdog auto-recovers within ~30s |
| Perception idle (0 objects) | model not loaded / GPU busy | check `journalctl --user -u sentigon-perception`; restart it (reloads model) |
| Incidents fire but no verdicts | VLM backend down | check Ollama (`sentigon-ollama`) / vLLM endpoint; reason auto-resumes when it is back |
| No event loss on any restart | at-least-once Kafka consumers | offsets commit only after handling; killed mid-incident replays on restart |
| VLM overloaded | too many candidates | severity backpressure defers low-severity, always verifies critical |
| Golden-path self-test FAIL | a stage stalled | the failing check names the stage; restart that service |

## Backup + restore

```
bash scripts/backup.sh                                   # Postgres dump + MinIO summary
bash scripts/restore.sh backups/<ts>/sentigon.sql.gz     # non-destructive verify restore
bash scripts/restore.sh backups/<ts>/sentigon.sql.gz --into-prod   # restore over prod
```
MinIO object data lives in the `sentigon_miniodata` Docker volume (snapshot the
volume, or `mc mirror`, for a full object backup).

## Upgrade / migration

`alembic upgrade head` is idempotent and data-preserving (transactional DDL).
Upgrade flow: `git pull` -> `uv sync` -> `alembic upgrade head` ->
`systemctl --user restart sentigon.target`. Incidents, cases, evidence, and config
are preserved across upgrades (verified).

## Retention + governance

- Retention auto-delete runs daily (`sentigon-retention.timer`); tune the window
  in `scripts/retention.py`. Open incidents + the evidence ledger are never deleted.
- Evidence chain integrity: `GET /evidence/verify` (fails if any record altered).
- Footage exports are audit-logged (`footage.exported`, actor + time).

## Model operations

- Hot-swap the detector with zero stream drop: `POST :8030/model/swap {"model": "..."}`.
- Shadow a challenger on live traffic: `uv run python -m bench.shadow_infer --challenger yolo26x.pt`.
- Per-model drift: `GET /models/drift` (confirm-rate trend; `drift_flag` on a sustained drop).

## Scale

Measured on one RTX 5080: ~160 detector fps/GPU, ~20 streams/GPU at 8 fps target.
Scale-out is per-GPU perception workers behind the Kafka bus; add GPUs/nodes to
multiply density. Run `uv run python -m bench.load_benchmark` for the current box.
