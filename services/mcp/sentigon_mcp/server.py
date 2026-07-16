"""Sentigon MCP tool server (backed by the real core API + search service)."""

from __future__ import annotations

import os
import uuid

import httpx
from mcp.server.fastmcp import FastMCP
from sentigon_common.config import settings

API = os.environ.get("SENTIGON_API_URL", "http://localhost:8010")
SEARCH = os.environ.get("SENTIGON_SEARCH_URL", "http://localhost:8060")

# The API and search reads are authenticated now, so the MCP tools authenticate as
# a trusted internal caller with the shared service token. The MCP transport itself
# binds to service_bind_host (localhost by default) so it is not LAN-exposed — a
# local agent reaches it, not the open internet.
_HEADERS = {"X-Service-Token": settings.service_token} if settings.service_token else {}

mcp = FastMCP(
    "sentigon",
    host=settings.service_bind_host,
    port=int(os.environ.get("MCP_HTTP_PORT", "8065")),
)


@mcp.tool()
async def search_incidents(status: str = "", severity: str = "", limit: int = 20) -> list[dict]:
    """List security incidents. status: new|ack|escalated|resolved|false_positive.
    severity: critical|high|medium|low. Returns real incidents with camera, signature,
    verdict and snapshot URL."""
    params = {"limit": limit}
    if status:
        params["status"] = status
    if severity:
        params["severity"] = severity
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(f"{API}/incidents", params=params, headers=_HEADERS)
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def semantic_search(query: str, limit: int = 10) -> list[dict]:
    """Natural-language search over captured video evidence (CLIP). Example:
    'person in a red jacket at the loading dock'. Returns matching incident snapshots."""
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(f"{SEARCH}/search", params={"q": query, "limit": limit}, headers=_HEADERS)
        r.raise_for_status()
        return r.json().get("results", [])


@mcp.tool()
async def get_incident(incident_id: str) -> dict:
    """Full incident detail: VLM verdict, SITREP, reasoning trace, context, timeline."""
    try:
        incident_id = str(uuid.UUID(incident_id))  # reject path-injection / malformed ids
    except ValueError:
        return {"error": "invalid incident_id (must be a UUID)"}
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(f"{API}/incidents/{incident_id}", headers=_HEADERS)
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def verify_evidence() -> dict:
    """Verify the tamper-evident evidence chain. Returns ok, record count, and any breaks."""
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(f"{API}/evidence/verify", headers=_HEADERS)
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def incident_summary() -> dict:
    """Aggregate posture: open incidents, totals, false-alarm rate, by-severity."""
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(f"{API}/analytics/overview", headers=_HEADERS)
        r.raise_for_status()
        return r.json()
