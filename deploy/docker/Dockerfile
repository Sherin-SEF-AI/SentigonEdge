# Single Python image for every Sentigon service. The running service is chosen
# at deploy time by the container command (python -m sentigon_<svc>), so one
# build artifact backs api/ingest/context/search/notify/mediasource/mcp/perception.
# It bundles torch (cu128) so the same image runs the GPU perception worker when
# scheduled onto a GPU node (the CUDA userspace ships in the wheel; the driver
# comes from the node via the NVIDIA device plugin). Tag it as both
# sentigon:<v> and sentigon-gpu:<v>, or build a slimmer CPU-only variant later.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libgl1 libglib2.0-0 ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Create the runtime user up front and hand it /app, so the (multi-GB torch/cuda)
# venv is written already-owned. A post-hoc `chown -R` would duplicate the whole
# venv into a second layer and roughly double the image size.
RUN useradd --create-home --uid 10001 sentigon && mkdir -p /app && chown sentigon:sentigon /app
WORKDIR /app

# Copy the whole workspace (all member pyprojects must be present for uv sync).
COPY --chown=sentigon:sentigon pyproject.toml uv.lock alembic.ini ./
COPY --chown=sentigon:sentigon migrations ./migrations
COPY --chown=sentigon:sentigon services ./services
COPY --chown=sentigon:sentigon configs ./configs

USER sentigon
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Overridden per service by the Helm Deployment command. Default to the API.
CMD ["python", "-m", "sentigon_api"]
