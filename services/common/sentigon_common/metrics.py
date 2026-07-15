"""Prometheus metrics. `mount_metrics` adds a /metrics endpoint (process + platform
collectors come for free from the default registry). Services can register custom
collectors before mounting.
"""
from __future__ import annotations

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest


def mount_metrics(app: FastAPI) -> None:
    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
