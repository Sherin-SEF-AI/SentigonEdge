# Sentigon V2 developer entrypoints.
# Python tooling runs through uv with a pinned 3.11 interpreter (system Python is
# 3.14, which the ML/DB wheels do not yet target). Infra runs in docker-compose.

SHELL := /bin/bash
DC := docker compose
UVRUN := uv run

.DEFAULT_GOAL := help

.PHONY: help up down restart ps logs wait sync migrate seed eval bench fmt lint typecheck test clean nuke samples samples-stop ingest runpod-burst runpod-volume runpod-volume-delete

help: ## show targets
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

up: ## bring the infra stack up and wait until healthy
	$(DC) up -d
	@bash scripts/wait_healthy.sh

dev: ## bring up ALL host services + web console (idempotent one-command dev stack)
	@bash scripts/dev_up.sh

down: ## stop the infra stack
	$(DC) down

restart: down up ## restart the stack

ps: ## container status
	$(DC) ps

logs: ## tail all logs
	$(DC) logs -f --tail=100

wait: ## block until the stack reports healthy
	@bash scripts/wait_healthy.sh

sync: ## create/refresh the uv .venv (Python 3.11) with all deps
	uv sync

migrate: sync ## apply Alembic migrations to head
	$(UVRUN) alembic upgrade head

seed: sync ## load config only: ontology-root site, 142 signature defaults, admin
	$(UVRUN) python -m sentigon_common.seed

cameras: sync ## register the sample RTSP streams as cameras + zones via the real API (needs api + ingest up)
	$(UVRUN) python scripts/register_cameras.py

eval: sync ## run the gold-set evaluation harness (green on empty set)
	$(UVRUN) python -m bench.eval_harness

bench: sync ## run the latency benchmark harness
	$(UVRUN) python -m bench.latency_harness

ffmpeg: ## extract the static ffmpeg/ffprobe binaries used by media-source
	@mkdir -p tools; CID=$$(docker create mwader/static-ffmpeg:latest); \
	docker cp $$CID:/ffmpeg tools/ffmpeg >/dev/null && docker cp $$CID:/ffprobe tools/ffprobe >/dev/null; \
	docker rm $$CID >/dev/null; chmod +x tools/ffmpeg tools/ffprobe; echo "extracted tools/ffmpeg tools/ffprobe"

media: sync ## run media-source: real internet streams -> MediaMTX + camera onboarding (needs api + ingest)
	MEDIASOURCE_HTTP_PORT=8055 $(UVRUN) python -m sentigon_mediasource

samples: ## publish on-box sample videos into MediaMTX as RTSP (dev cameras)
	@bash scripts/publish_samples.sh

samples-stop: ## stop the sample publishers
	@docker ps -aq --filter "name=sentigon-pub-" | xargs -r docker rm -f >/dev/null 2>&1 || true
	@echo "sample publishers stopped"

ingest: sync ## run the ingest service (needs: make up, make samples)
	INGEST_HTTP_PORT=$(or $(INGEST_PORT),8020) $(UVRUN) python -m sentigon_ingest

perception: sync ## run the perception service (GPU detect/track/ReID; needs up + samples)
	PERCEPTION_HTTP_PORT=$(or $(PERCEPTION_PORT),8030) $(UVRUN) python -m sentigon_perception

context: sync ## run the context engine (signatures over perception.objects; needs perception)
	CONTEXT_HTTP_PORT=$(or $(CONTEXT_PORT),8040) $(UVRUN) python -m sentigon_context

api: sync ## run the core API (incidents, zones, signatures) on :8010
	API_HTTP_PORT=$(or $(API_PORT),8010) $(UVRUN) python -m sentigon_api

reason: sync ## run the Reason VLM verifier (needs Ollama qwen2.5vl + context emitting candidates)
	REASON_HTTP_PORT=$(or $(REASON_PORT),8050) $(UVRUN) python -m sentigon_reason

notify: sync ## run notify (real email+webhook on confirmed incidents; needs mailpit + webhook-sink)
	NOTIFY_HTTP_PORT=$(or $(NOTIFY_PORT),8070) $(UVRUN) python -m sentigon_notify

search: sync ## run semantic search (CLIP index of incident snapshots -> Qdrant) on :8060
	SEARCH_HTTP_PORT=$(or $(SEARCH_PORT),8060) $(UVRUN) python -m sentigon_search

fmt: sync ## format code
	$(UVRUN) ruff check --fix .
	$(UVRUN) black .

lint: sync ## lint
	$(UVRUN) ruff check .

typecheck: sync ## static type check
	$(UVRUN) mypy services/common/sentigon_common bench

test: sync ## run the test suite
	$(UVRUN) pytest -ra

clean: ## remove caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache

nuke: ## stop stack and delete all volumes (destroys data)
	$(DC) down -v

runpod-volume: ## create the persistent 32B weights-cache volume once (fast reloads thereafter)
	$(UVRUN) python scripts/runpod.py volume-create

runpod-volume-delete: ## delete the weights-cache volume (stops its storage cost)
	$(UVRUN) python scripts/runpod.py volume-delete

runpod-burst: sync ## burst the heavy VLM tier: deploy Qwen3-VL-32B (cached volume), repoint reason, run, auto-teardown (SECS=120)
	$(UVRUN) python scripts/runpod.py burst $(or $(SECS),120)
