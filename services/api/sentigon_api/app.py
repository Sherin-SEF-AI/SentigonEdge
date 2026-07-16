"""Sentigon core API: incidents, zones, signatures, events, summary."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client.core import REGISTRY, GaugeMetricFamily
from pydantic import BaseModel, Field
from sentigon_common.auth import (
    cors_headers_for,
    create_access_token,
    hash_password,
    is_writer,
    secure_compare,
    user_from_token,
    verify_password,
)
from sentigon_common.config import settings as common_settings
from sentigon_common.db import async_session_factory, sync_session_factory
from sentigon_common.db.models import (
    AccessEvent,
    AuditLogEntry,
    Camera,
    Case,
    EvalRun,
    Event,
    EvidenceRecord,
    Incident,
    IncidentStatusLog,
    ModelVersion,
    NLAlert,
    Recording,
    ScheduleWindow,
    Signature,
    Site,
    User,
    WatchlistEntry,
    Zone,
    ZoneSnapshot,
    case_incidents,
)
from sentigon_common.health import check_postgres, make_health_router
from sentigon_common.logging import configure_logging, get_logger
from sentigon_common.metrics import mount_metrics
from sentigon_common.redact import redact_url_credentials
from sentigon_common.risk import compute_risk_score, priority_band
from sentigon_common.schemas.enums import (
    AccessEventType,
    IncidentStatus,
    ModelRole,
    ModelStage,
    Severity,
    UserRole,
    Verdict,
    ZoneType,
)
from sentigon_common.storage import get_store
from sentigon_common.vault import append_evidence, verify_chain
from sqlalchemy import delete, func, select

log = get_logger("api")
configure_logging("api")

# Fail fast on a missing JWT secret: an empty key would sign forgeable tokens.
# (Set JWT_SECRET_KEY in the environment / .env; e.g. `openssl rand -hex 32`.)
if not common_settings.jwt_secret_key or len(common_settings.jwt_secret_key) < 16:
    raise RuntimeError(
        "JWT_SECRET_KEY is unset or too short. Refusing to start with a weak/empty "
        "signing key. Set a strong secret (>=16 chars) in the environment."
    )

app = FastAPI(title="Sentigon Core API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=common_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(make_health_router("api", {"postgres": check_postgres}))

_store = get_store()


class _SentigonCollector:
    """Prometheus collector: real Sentigon posture from the DB, on each scrape."""

    def collect(self):  # noqa: ANN201
        try:
            with sync_session_factory() as s:
                total = s.scalar(select(func.count()).select_from(Incident)) or 0
                by_sev = s.execute(
                    select(Incident.severity, func.count()).group_by(Incident.severity)
                ).all()
                by_status = s.execute(
                    select(Incident.status, func.count()).group_by(Incident.status)
                ).all()
                verified = s.scalar(
                    select(func.count()).select_from(Incident).where(Incident.verdict.is_not(None))
                ) or 0
                rejected = s.scalar(
                    select(func.count()).select_from(Incident).where(Incident.verdict == Verdict.REJECTED)
                ) or 0
                by_cam = s.execute(
                    select(Camera.name, func.count())
                    .join(Incident, Incident.camera_id == Camera.id)
                    .group_by(Camera.name)
                ).all()
        except Exception:  # noqa: BLE001
            return

        g = GaugeMetricFamily("sentigon_incidents_total", "Total incidents")
        g.add_metric([], total)
        yield g
        sev = GaugeMetricFamily("sentigon_incidents_by_severity", "Incidents by severity", labels=["severity"])
        for k, c in by_sev:
            sev.add_metric([k.value], c)
        yield sev
        st = GaugeMetricFamily("sentigon_incidents_by_status", "Incidents by status", labels=["status"])
        for k, c in by_status:
            st.add_metric([k.value], c)
        yield st
        far = GaugeMetricFamily("sentigon_false_alarm_rate", "VLM false-alarm rate")
        far.add_metric([], (rejected / verified) if verified else 0.0)
        yield far
        cam = GaugeMetricFamily("sentigon_incidents_by_camera", "Incidents by camera", labels=["camera"])
        for name, c in by_cam:
            cam.add_metric([name], c)
        yield cam


REGISTRY.register(_SentigonCollector())
mount_metrics(app)

_WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
# Paths reachable without a token. Everything else — including all reads (which
# expose incidents, snapshots, badge PII, RTSP URIs) — now requires viewer+.
_PUBLIC_PATHS = {"/auth/login", "/healthz", "/readyz", "/metrics", "/docs", "/openapi.json"}


def _bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else None


@app.middleware("http")
async def rbac(request: Request, call_next):  # noqa: ANN001, ANN201
    """Authenticate every request. Reads require a viewer+ JWT (or the internal
    service token); writes require operator+. Identity is resolved once and
    stashed on request.state for handlers and _allowed_sites. CORS preflight and
    a small public allowlist (login/health/metrics/docs) are exempt."""
    request.state.user = None
    request.state.service = False
    path = request.url.path
    if request.method == "OPTIONS" or path in _PUBLIC_PATHS or path.startswith("/health"):
        return await call_next(request)

    service_ok = secure_compare(request.headers.get("x-service-token"), common_settings.service_token)
    user = None if service_ok else await user_from_token(_bearer_token(request))
    request.state.user = user
    request.state.service = service_ok

    if not service_ok:
        if user is None:
            return JSONResponse(
                {"detail": "authentication required"},
                status_code=401,
                headers=cors_headers_for(request),
            )
        if request.method in _WRITE_METHODS and not is_writer(user):
            return JSONResponse(
                {"detail": "insufficient role (operator+ required)"},
                status_code=403,
                headers=cors_headers_for(request),
            )
    return await call_next(request)


class LoginIn(BaseModel):
    email: str
    password: str


# Per-IP login throttle (in-process; one API instance per box on the Orin). A
# fixed dummy bcrypt hash is verified for unknown emails so response time does not
# reveal whether an account exists (removes the enumeration timing oracle).
_LOGIN_MAX = 10
_LOGIN_WINDOW_S = 60.0
_login_hits: dict[str, list[float]] = {}
_DUMMY_HASH = hash_password("sentigon-timing-equalizer")


def _login_rate_limited(ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _login_hits.get(ip, []) if now - t < _LOGIN_WINDOW_S]
    hits.append(now)
    _login_hits[ip] = hits
    return len(hits) > _LOGIN_MAX


@app.post("/auth/login")
async def login(payload: LoginIn, request: Request) -> dict:
    if _login_rate_limited(request.client.host if request.client else "unknown"):
        raise HTTPException(429, "too many login attempts, slow down")
    async with async_session_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == payload.email))
        ).scalar_one_or_none()
    if user is None:
        verify_password(payload.password, _DUMMY_HASH)  # equalize timing
        raise HTTPException(401, "invalid credentials")
    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(401, "invalid credentials")
    return {
        "access_token": create_access_token(str(user.id), user.role.value),
        "token_type": "bearer",
        "role": user.role.value,
        "email": user.email,
        "name": user.full_name,
    }


@app.get("/auth/me")
async def auth_me(request: Request) -> dict:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    user = await user_from_token(token)
    if user is None:
        raise HTTPException(401, "not authenticated")
    return {"id": str(user.id), "email": user.email, "name": user.full_name, "role": user.role.value}

_ACTION_STATUS = {
    "ack": (IncidentStatus.ACK, "acknowledged_at"),
    "escalate": (IncidentStatus.ESCALATED, None),
    "resolve": (IncidentStatus.RESOLVED, "resolved_at"),
    "false": (IncidentStatus.FALSE_POSITIVE, "resolved_at"),
}


def _presigned(ref: str | None) -> str | None:
    if not ref or "/" not in ref:
        return None
    bucket, key = ref.split("/", 1)
    try:
        return _store.presigned_get(bucket, key, 3600)
    except Exception:  # noqa: BLE001
        return None


# ── incidents ─────────────────────────────────────────────────


@app.get("/incidents")
async def list_incidents(
    request: Request,
    status: str | None = None,
    severity: str | None = None,
    limit: int = Query(100, le=500),
) -> list[dict]:
    allowed = await _allowed_sites(request)
    async with async_session_factory() as session:
        q = (
            select(Incident, Signature.name, Camera.name)
            .join(Signature, Incident.signature_id == Signature.id, isouter=True)
            .join(Camera, Incident.camera_id == Camera.id, isouter=True)
            .order_by(Incident.created_at.desc())
            .limit(limit)
        )
        if allowed is not None:
            q = q.where(Camera.site_id.in_(allowed))
        if status:
            q = q.where(Incident.status == IncidentStatus(status))
        if severity:
            q = q.where(Incident.severity == Severity(severity))
        rows = (await session.execute(q)).all()
    out = []
    for inc, sig_name, cam_name in rows:
        out.append(
            {
                "id": str(inc.id),
                "seq": inc.seq,
                "title": inc.title,
                "severity": inc.severity.value,
                "status": inc.status.value,
                "verdict": inc.verdict.value if inc.verdict else None,
                "confidence": inc.confidence,
                "risk_score": inc.risk_score,
                "priority": priority_band(inc.risk_score) if inc.risk_score is not None else None,
                "occurrence_count": inc.occurrence_count,
                "signature": sig_name,
                "camera_id": str(inc.camera_id),
                "camera": cam_name,
                "zone_id": str(inc.zone_id) if inc.zone_id else None,
                "snapshot_url": _presigned(inc.snapshot_ref),
                "created_at": inc.created_at.isoformat(),
                "acknowledged_at": inc.acknowledged_at.isoformat() if inc.acknowledged_at else None,
                "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
            }
        )
    return out


@app.get("/threats")
async def threat_queue(
    request: Request, limit: int = Query(25, le=100), min_score: int = 0
) -> dict:
    """Prioritized threat queue: OPEN incidents ranked by composite risk score, so
    operators triage the few that matter first (alert-fatigue reduction). Each threat
    carries its live corroboration count (bound access-control signals)."""
    allowed = await _allowed_sites(request)
    open_states = [IncidentStatus.NEW, IncidentStatus.ACK, IncidentStatus.ESCALATED]
    async with async_session_factory() as session:
        q = (
            select(Incident, Signature.name, Signature.category, Camera.name)
            .join(Signature, Incident.signature_id == Signature.id, isouter=True)
            .join(Camera, Incident.camera_id == Camera.id, isouter=True)
            .where(Incident.status.in_(open_states))
            .order_by(Incident.risk_score.desc().nullslast(), Incident.created_at.desc())
            .limit(limit * 3)
        )
        if allowed is not None:
            q = q.where(Camera.site_id.in_(allowed))
        rows = (await session.execute(q)).all()
        # live corroboration: access-control events bound to each incident
        inc_ids = [inc.id for inc, *_ in rows]
        corr_counts: dict = {}
        if inc_ids:
            for iid, cnt in (
                await session.execute(
                    select(AccessEvent.incident_id, func.count())
                    .where(AccessEvent.incident_id.in_(inc_ids))
                    .group_by(AccessEvent.incident_id)
                )
            ).all():
                corr_counts[iid] = cnt

    threats = []
    for inc, sig_name, sig_cat, cam_name in rows:
        corr = corr_counts.get(inc.id, 0)
        # re-score live with current corroboration so fused signals lift the rank
        score, breakdown = compute_risk_score(
            severity=inc.severity.value,
            category=sig_cat,
            confidence=inc.confidence,
            verdict=inc.verdict.value if inc.verdict else None,
            zone_type=None,
            correlated_signals=corr,
        )
        if score < min_score:
            continue
        threats.append(
            {
                "id": str(inc.id),
                "title": inc.title,
                "signature": sig_name,
                "severity": inc.severity.value,
                "status": inc.status.value,
                "verdict": inc.verdict.value if inc.verdict else None,
                "camera": cam_name,
                "risk_score": score,
                "priority": priority_band(score),
                "occurrence_count": inc.occurrence_count,
                "corroborating_signals": corr,
                "score_breakdown": breakdown,
                "created_at": inc.created_at.isoformat(),
            }
        )
    threats.sort(key=lambda t: (t["risk_score"], t["created_at"]), reverse=True)
    return {"count": len(threats[:limit]), "threats": threats[:limit]}


@app.get("/incidents/{incident_id}")
async def get_incident(incident_id: uuid.UUID) -> dict:
    async with async_session_factory() as session:
        inc = await session.get(Incident, incident_id)
        if inc is None:
            raise HTTPException(404, "incident not found")
        sig = await session.get(Signature, inc.signature_id) if inc.signature_id else None
        cam = await session.get(Camera, inc.camera_id)
        event = await session.get(Event, inc.event_id) if inc.event_id else None
        logs = (
            (
                await session.execute(
                    select(IncidentStatusLog)
                    .where(IncidentStatusLog.incident_id == incident_id)
                    .order_by(IncidentStatusLog.ts.asc())
                )
            )
            .scalars()
            .all()
        )
    return {
        "id": str(inc.id),
        "seq": inc.seq,
        "title": inc.title,
        "severity": inc.severity.value,
        "status": inc.status.value,
        "verdict": inc.verdict.value if inc.verdict else None,
        "confidence": inc.confidence,
        "sitrep": inc.sitrep,
        "reasoning_trace": inc.reasoning_trace,
        "attributes": inc.attributes,
        "signature": sig.name if sig else None,
        "signature_category": sig.category if sig else None,
        "camera": cam.name if cam else None,
        "camera_id": str(inc.camera_id),
        "zone_id": str(inc.zone_id) if inc.zone_id else None,
        "snapshot_url": _presigned(inc.snapshot_ref),
        "clip_url": _presigned(inc.clip_ref),
        "event": (
            {
                "event_type": event.event_type,
                "confidence": event.confidence,
                "object_refs": event.object_refs,
                "context": event.context,
                "ts": event.ts.isoformat(),
            }
            if event
            else None
        ),
        "timeline": [
            {"to": lg.to_status, "from": lg.from_status, "note": lg.note, "ts": lg.ts.isoformat()}
            for lg in logs
        ],
        "created_at": inc.created_at.isoformat(),
    }


@app.post("/incidents/{incident_id}/{action}")
async def incident_action(
    incident_id: uuid.UUID, action: str, note: str = Body("", embed=True)
) -> dict:
    if action == "investigate":
        return await incident_investigate(incident_id)
    if action not in _ACTION_STATUS:
        raise HTTPException(400, f"unknown action: {action}")
    new_status, ts_field = _ACTION_STATUS[action]
    async with async_session_factory() as session:
        inc = await session.get(Incident, incident_id)
        if inc is None:
            raise HTTPException(404, "incident not found")
        prev = inc.status.value
        inc.status = new_status
        if ts_field:
            setattr(inc, ts_field, datetime.now(UTC))
        session.add(
            IncidentStatusLog(
                incident_id=inc.id, from_status=prev, to_status=new_status.value, note=note or None
            )
        )
        session.add(
            AuditLogEntry(
                action=f"incident.{action}",
                resource_type="incident",
                resource_id=str(inc.id),
                details={"from": prev, "to": new_status.value},
                correlation_id=inc.correlation_id,
            )
        )
        await session.commit()
    return {"id": str(incident_id), "status": new_status.value}


@app.get("/incidents/{incident_id}/snapshot")
async def incident_snapshot(incident_id: uuid.UUID, blur: bool = False, faces: bool = False):
    """Serve an incident snapshot. `blur` obscures the incident's object region;
    `faces` detects and blurs every face in the frame (privacy-preserving export,
    behavior evidence retained, identities obscured)."""
    from io import BytesIO

    from fastapi.responses import Response
    from PIL import Image, ImageFilter

    async with async_session_factory() as session:
        inc = await session.get(Incident, incident_id)
        if inc is None or not inc.snapshot_ref or "/" not in inc.snapshot_ref:
            raise HTTPException(404, "no snapshot")
        ev = await session.get(Event, inc.event_id) if inc.event_id else None
    bucket, key = inc.snapshot_ref.split("/", 1)
    try:
        raw = _store.get_bytes(bucket, key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "snapshot object missing") from exc
    if not blur and not faces:
        return Response(content=raw, media_type="image/jpeg")

    headers = {}
    if faces:
        import cv2
        import numpy as np
        from sentigon_common.faceblur import blur_faces

        arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        arr, n = await asyncio.to_thread(blur_faces, arr)
        headers["X-Faces-Blurred"] = str(n)
        raw = cv2.imencode(".jpg", arr)[1].tobytes()

    if blur:
        img = Image.open(BytesIO(raw)).convert("RGB")
        bbox = ((ev.object_refs or {}) if ev else {}).get("bbox")
        if bbox and len(bbox) == 4:
            x, y, w, h = (int(v) for v in bbox)
            region = img.crop((x, y, x + w, y + h)).filter(ImageFilter.GaussianBlur(18))
            img.paste(region, (x, y))
        out = BytesIO()
        img.save(out, format="JPEG")
        raw = out.getvalue()
    headers["X-Privacy-Blur"] = "applied"
    return Response(content=raw, media_type="image/jpeg", headers=headers)


def _subject_track(object_refs: dict) -> int | None:
    if not object_refs:
        return None
    if isinstance(object_refs.get("track_ids"), list) and object_refs["track_ids"]:
        return object_refs["track_ids"][0]
    for k in ("track_id", "from_track"):
        if object_refs.get(k) is not None:
            return object_refs[k]
    return None


@app.get("/incidents/{incident_id}/reconstruction")
async def incident_reconstruction(incident_id: uuid.UUID, window_s: int = Query(120, le=1800)) -> dict:
    return await _reconstruct(incident_id, window_s)


@app.post("/incidents/{incident_id}/investigate")
async def incident_investigate(incident_id: uuid.UUID, window_s: int = 180) -> dict:
    """Autonomously assemble the incident's multi-camera investigation timeline and
    persist it on the incident (attributes.investigation), so an operator opens a
    ready-made investigation instead of building it by hand (Agentic Investigations)."""
    recon = await _reconstruct(incident_id, window_s)
    summary = {
        "assembled_at": datetime.now(UTC).isoformat(),
        "involved_cameras": recon.get("involved_cameras", []),
        "cross_camera_appearances": recon.get("counts", {}).get("cross_camera_appearances", 0),
        "related_incidents": recon.get("counts", {}).get("related_incidents", 0),
        "recording_segments": recon.get("counts", {}).get("recording_segments", 0),
        "timeline": recon.get("timeline", [])[:60],
    }
    async with async_session_factory() as session:
        inc = await session.get(Incident, incident_id)
        if inc is None:
            raise HTTPException(404, "incident not found")
        inc.attributes = {**(inc.attributes or {}), "investigation": summary}
        session.add(
            AuditLogEntry(
                action="incident.auto_investigate", resource_type="incident",
                resource_id=str(incident_id),
                details={"cameras": summary["involved_cameras"], "events": len(summary["timeline"])},
            )
        )
        await session.commit()
    return {"incident_id": str(incident_id), "assembled": True, **summary}


async def _reconstruct(incident_id: uuid.UUID, window_s: int = 120) -> dict:
    """Reconstruct an incident as a time-ordered, multi-camera timeline: the anchor
    incident, the subject's cross-camera path (OSNet ReID trajectory), related
    incidents and camera handoffs in the window, and the recording segments that
    cover it, all merged and sorted by time for investigation/replay."""
    import httpx

    async with async_session_factory() as session:
        inc = await session.get(Incident, incident_id)
        if inc is None:
            raise HTTPException(404, "incident not found")
        ev = await session.get(Event, inc.event_id) if inc.event_id else None
        anchor_ts = inc.created_at
        lo = anchor_ts - timedelta(seconds=window_s)
        hi = anchor_ts + timedelta(seconds=window_s)
        cam_names = {
            str(cid): name for cid, name in (await session.execute(select(Camera.id, Camera.name))).all()
        }
        sig = await session.get(Signature, inc.signature_id) if inc.signature_id else None

    subject_track = _subject_track((ev.object_refs if ev else None) or {})
    involved = {str(inc.camera_id)} if inc.camera_id else set()

    timeline: list[dict] = []
    timeline.append({
        "type": "incident", "kind": "anchor", "ts": anchor_ts.isoformat(),
        "camera_id": str(inc.camera_id), "camera": cam_names.get(str(inc.camera_id), "?"),
        "signature": sig.name if sig else None, "severity": inc.severity.value,
        "title": inc.title, "incident_id": str(inc.id),
        "snapshot": bool(inc.snapshot_ref),
    })

    # subject's cross-camera path via the ReID trajectory service
    trajectory = None
    if subject_track is not None and inc.camera_id:
        try:
            async with httpx.AsyncClient(timeout=8.0) as c:
                r = await c.get(
                    "http://localhost:8060/reid/trajectory",
                    params={
                        "camera_id": str(inc.camera_id),
                        "track_id": subject_track,
                        # require the OSNet same-identity band so the path is the
                        # subject, not any visually-loose match
                        "min_score": 0.86,
                    },
                )
                if r.status_code == 200 and r.json().get("found"):
                    trajectory = r.json()
                    lo_iso, hi_iso = lo.isoformat(), hi.isoformat()
                    for m in trajectory.get("cross_camera_matches", []):
                        mts = m.get("matched_ts")
                        # only appearances inside the reconstruction window belong
                        # on this incident's timeline
                        if not mts or not (lo_iso <= mts <= hi_iso):
                            continue
                        involved.add(m["camera_id"])
                        timeline.append({
                            "type": "appearance", "kind": "cross_camera",
                            "ts": mts, "camera_id": m["camera_id"],
                            "camera": cam_names.get(m["camera_id"], m["camera_id"][:8]),
                            "match_score": m.get("match_score"),
                        })
        except Exception:  # noqa: BLE001
            trajectory = None

    async with async_session_factory() as session:
        involved_uuids = [uuid.UUID(c) for c in involved if c]
        related = (
            await session.execute(
                select(Incident)
                .where(
                    Incident.camera_id.in_(involved_uuids),
                    Incident.created_at.between(lo, hi),
                    Incident.id != inc.id,
                )
                .order_by(Incident.created_at)
                .limit(50)
            )
        ).scalars().all()
        for r in related:
            rsig = await session.get(Signature, r.signature_id) if r.signature_id else None
            timeline.append({
                "type": "related_incident", "kind": "handoff" if rsig and rsig.name == "Cross-Camera Handoff" else "incident",
                "ts": r.created_at.isoformat(), "camera_id": str(r.camera_id),
                "camera": cam_names.get(str(r.camera_id), "?"),
                "signature": rsig.name if rsig else None, "severity": r.severity.value,
                "title": r.title, "incident_id": str(r.id),
            })
        recs = (
            await session.execute(
                select(Recording)
                .where(
                    Recording.camera_id.in_(involved_uuids),
                    Recording.start_time <= hi,
                    (Recording.end_time.is_(None)) | (Recording.end_time >= lo),
                )
                .order_by(Recording.start_time)
                .limit(200)
            )
        ).scalars().all()

    recordings = [
        {
            "camera_id": str(r.camera_id), "camera": cam_names.get(str(r.camera_id), "?"),
            "start": r.start_time.isoformat(),
            "end": r.end_time.isoformat() if r.end_time else None,
            "object_key": r.object_key, "bucket": r.bucket,
        }
        for r in recs
    ]

    timeline.sort(key=lambda e: e.get("ts") or "")
    return {
        "incident_id": str(incident_id),
        "anchor_ts": anchor_ts.isoformat(),
        "window_s": window_s,
        "subject_track": subject_track,
        "involved_cameras": [cam_names.get(c, c[:8]) for c in involved],
        "trajectory_found": trajectory is not None,
        "timeline": timeline,
        "recording_segments": recordings,
        "counts": {
            "timeline_entries": len(timeline),
            "cross_camera_appearances": sum(1 for e in timeline if e["type"] == "appearance"),
            "related_incidents": sum(1 for e in timeline if e["type"] == "related_incident"),
            "recording_segments": len(recordings),
        },
    }


class TamperIn(BaseModel):
    camera_id: uuid.UUID
    kind: str = "blackout"  # blackout | defocus | moved
    metric: float = 0.0


@app.post("/system/camera-tamper", status_code=201)
async def raise_camera_tamper(body: TamperIn) -> dict:
    """Raise a camera tamper/blindness incident (covered, defocused, or moved
    camera). Called by the perception watchdog when a stream is compromised."""
    async with async_session_factory() as session:
        sig = (
            await session.execute(select(Signature).where(Signature.name == "Camera Tamper"))
        ).scalar_one_or_none()
        if sig is None:
            raise HTTPException(500, "Camera Tamper signature not provisioned")
        cam = await session.get(Camera, body.camera_id)
        corr = uuid.uuid4().hex
        inc = Incident(
            signature_id=sig.id,
            camera_id=body.camera_id,
            title=f"Camera tamper ({body.kind}) on {cam.name if cam else str(body.camera_id)[:8]}",
            severity=Severity.HIGH,
            status=IncidentStatus.NEW,
            confidence=0.9,
            correlation_id=corr,
            attributes={"tamper_kind": body.kind, "metric": round(body.metric, 2)},
        )
        session.add(inc)
        await session.flush()
        session.add(
            AuditLogEntry(
                action="camera.tamper_detected",
                resource_type="camera",
                resource_id=str(body.camera_id),
                details={"kind": body.kind, "metric": round(body.metric, 2), "incident": str(inc.id)},
                correlation_id=corr,
            )
        )
        await session.commit()
        iid = inc.id
    return {"incident_id": str(iid), "kind": body.kind}


class BulkAction(BaseModel):
    ids: list[uuid.UUID]
    action: str
    note: str = ""


@app.post("/incidents/bulk")
async def bulk_incident_action(body: BulkAction) -> dict:
    """Apply one triage action to many incidents at once (keyboard-first triage)."""
    if body.action not in _ACTION_STATUS:
        raise HTTPException(400, f"unknown action: {body.action}")
    new_status, ts_field = _ACTION_STATUS[body.action]
    updated = 0
    async with async_session_factory() as session:
        for iid in body.ids:
            inc = await session.get(Incident, iid)
            if inc is None:
                continue
            prev = inc.status.value
            inc.status = new_status
            if ts_field:
                setattr(inc, ts_field, datetime.now(UTC))
            session.add(
                IncidentStatusLog(
                    incident_id=inc.id, from_status=prev, to_status=new_status.value, note=body.note or None
                )
            )
            updated += 1
        session.add(
            AuditLogEntry(
                action=f"incident.bulk_{body.action}",
                resource_type="incident",
                resource_id=f"{updated} incidents",
                details={"count": updated, "action": body.action, "ids": [str(i) for i in body.ids]},
            )
        )
        await session.commit()
    return {"updated": updated, "action": body.action, "status": new_status.value}


@app.get("/shift-handover")
async def shift_handover(hours: int = Query(8, le=48)) -> dict:
    """End-of-shift summary the next operator inherits: open + unacknowledged
    incidents, escalations, and camera health over the shift window."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    async with async_session_factory() as session:
        open_by_sev = (
            await session.execute(
                select(Incident.severity, func.count())
                .where(Incident.status.in_([IncidentStatus.NEW, IncidentStatus.ACK, IncidentStatus.ESCALATED]))
                .group_by(Incident.severity)
            )
        ).all()
        unack = await session.scalar(
            select(func.count()).select_from(Incident).where(Incident.status == IncidentStatus.NEW)
        )
        escalated = await session.scalar(
            select(func.count())
            .select_from(AuditLogEntry)
            .where(AuditLogEntry.action == "incident.escalated", AuditLogEntry.ts >= since)
        )
        shift_total = await session.scalar(
            select(func.count()).select_from(Incident).where(Incident.created_at >= since)
        )
        top_open = (
            await session.execute(
                select(Incident.id, Incident.title, Incident.severity, Incident.status, Incident.created_at)
                .where(Incident.status == IncidentStatus.NEW)
                .order_by(Incident.severity, Incident.created_at.desc())
                .limit(20)
            )
        ).all()
        cams = (await session.execute(select(Camera))).scalars().all()
    return {
        "shift_hours": hours,
        "incidents_this_shift": shift_total or 0,
        "open_by_severity": {s.value: c for s, c in open_by_sev},
        "unacknowledged": unack or 0,
        "escalations_this_shift": escalated or 0,
        "cameras": [{"name": c.name, "status": c.status.value} for c in cams],
        "top_open_incidents": [
            {
                "id": str(i),
                "title": t,
                "severity": sev.value,
                "status": st.value,
                "created_at": ca.isoformat() if ca else None,
            }
            for i, t, sev, st, ca in top_open
        ],
    }


# ── events ────────────────────────────────────────────────────


@app.get("/events")
async def list_events(limit: int = Query(100, le=500)) -> list[dict]:
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(Event, Signature.name)
                .join(Signature, Event.signature_id == Signature.id, isouter=True)
                .order_by(Event.ts.desc())
                .limit(limit)
            )
        ).all()
    return [
        {
            "id": str(e.id),
            "seq": e.seq,
            "event_type": e.event_type,
            "signature": name,
            "severity": e.severity.value,
            "confidence": e.confidence,
            "camera_id": str(e.camera_id),
            "ts": e.ts.isoformat(),
        }
        for e, name in rows
    ]


# ── sites / cameras (onboarding lookups) ─────────────────────


@app.get("/sites")
async def list_sites() -> list[dict]:
    async with async_session_factory() as session:
        sites = (await session.execute(select(Site))).scalars().all()
    return [{"id": str(s.id), "name": s.name, "timezone": s.timezone} for s in sites]


async def _allowed_sites(request: Request) -> list[uuid.UUID] | None:
    """Site-scope enforcement. Returns the site_ids the request may see, or None
    for full scope (admins, unscoped operators, the internal service token).

    Identity is taken from request.state (resolved once by the rbac middleware).
    Fails CLOSED: a request with no authenticated user resolves to an empty scope
    (see nothing), never full scope — the previous version treated anonymous the
    same as admin, so dropping the token granted MORE access than a scoped token."""
    if getattr(request.state, "service", False):
        return None
    user = getattr(request.state, "user", None)
    if user is None:
        return []  # unreachable behind rbac, but never fail open
    if user.role == UserRole.ADMIN:
        return None
    sites = (user.site_scope or {}).get("sites")
    if not sites:
        return None
    return [uuid.UUID(str(s)) for s in sites]


def _current_user(request: Request) -> User | None:
    return getattr(request.state, "user", None)


def _require_role(request: Request, *roles: UserRole) -> None:
    """Enforce a minimum role for actions stricter than the middleware baseline
    (operator+). The internal service token is trusted; ADMIN passes any check.
    Identity comes from request.state (resolved once by the rbac middleware)."""
    if getattr(request.state, "service", False):
        return
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "authentication required")
    if user.role != UserRole.ADMIN and user.role not in roles:
        raise HTTPException(403, f"requires role {[r.value for r in roles]}")


@app.get("/cameras")
async def list_cameras(request: Request) -> list[dict]:
    allowed = await _allowed_sites(request)
    async with async_session_factory() as session:
        q = select(Camera)
        if allowed is not None:
            q = q.where(Camera.site_id.in_(allowed))
        cams = (await session.execute(q)).scalars().all()
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "rtsp_uri": redact_url_credentials(c.rtsp_uri),
            "status": c.status.value,
            "map": (c.meta or {}).get("map"),
        }
        for c in cams
    ]


class CameraPatch(BaseModel):
    name: str | None = None
    fps: int | None = None
    resolution: str | None = None


@app.patch("/cameras/{camera_id}")
async def patch_camera(camera_id: uuid.UUID, payload: CameraPatch, request: Request) -> dict:
    """Rename / retune a camera (operator+). Renames are safe: media-source matches
    its config to cameras by RTSP path, not name, so no duplicate is created."""
    async with async_session_factory() as session:
        cam = await session.get(Camera, camera_id)
        if cam is None:
            raise HTTPException(404, "camera not found")
        if payload.name is not None and payload.name.strip():
            cam.name = payload.name.strip()
        if payload.fps is not None:
            cam.fps = payload.fps
        if payload.resolution is not None:
            cam.resolution = payload.resolution
        session.add(
            AuditLogEntry(
                action="camera.updated",
                resource_type="camera",
                resource_id=str(camera_id),
                details={"name": cam.name},
            )
        )
        await session.commit()
        return {"id": str(cam.id), "name": cam.name, "fps": cam.fps, "resolution": cam.resolution}


def _ref_pair(ref: str | None) -> tuple[str, str] | None:
    return tuple(ref.split("/", 1)) if ref and "/" in ref else None  # type: ignore[return-value]


@app.delete("/cameras/{camera_id}")
async def delete_camera(camera_id: uuid.UUID, request: Request) -> dict:
    """Permanently delete a camera AND all its data — events, incidents, recordings,
    and their object-storage objects — then de-register its media source + stop its
    ingest worker. Destroys evidence, so admin only."""
    import httpx

    _require_role(request, UserRole.ADMIN)
    async with async_session_factory() as session:
        cam = await session.get(Camera, camera_id)
        if cam is None:
            raise HTTPException(404, "camera not found")
        name = cam.name
        objs: set[tuple[str, str]] = set()
        for b, k in (
            await session.execute(
                select(Recording.bucket, Recording.object_key).where(Recording.camera_id == camera_id)
            )
        ).all():
            if b and k:
                objs.add((b, k))
        for tbl in (Event, Incident):
            for snap, clip in (
                await session.execute(
                    select(tbl.snapshot_ref, tbl.clip_ref).where(tbl.camera_id == camera_id)
                )
            ).all():
                for p in (_ref_pair(snap), _ref_pair(clip)):
                    if p:
                        objs.add(p)
        # zones are SET NULL on camera delete (not removed), so drop them explicitly;
        # then delete the camera — the DB cascades events/incidents/recordings.
        await session.execute(delete(Zone).where(Zone.camera_id == camera_id))
        await session.execute(delete(Camera).where(Camera.id == camera_id))
        session.add(
            AuditLogEntry(
                action="camera.deleted",
                resource_type="camera",
                resource_id=str(camera_id),
                details={"name": name, "objects": len(objs)},
            )
        )
        await session.commit()

    removed = 0
    for b, k in objs:
        try:
            await asyncio.to_thread(_store.remove, b, k)
            removed += 1
        except Exception:  # noqa: BLE001
            pass

    # best-effort: drop the media source (so it isn't re-created) and stop the worker
    async with httpx.AsyncClient(
        timeout=8.0, headers={"X-Service-Token": common_settings.service_token}
    ) as c:
        for coro in (
            c.delete(f"{common_settings.mediasource_url}/sources/by-camera/{camera_id}"),
            c.post(f"{common_settings.ingest_url}/cameras/{camera_id}/stop"),
        ):
            with contextlib.suppress(Exception):
                await coro

    return {"deleted": str(camera_id), "name": name, "objects_removed": removed}


# ── zones (ROI editor) ────────────────────────────────────────


class ZoneIn(BaseModel):
    name: str
    zone_type: str = "restricted"
    camera_id: uuid.UUID
    site_id: uuid.UUID | None = None
    polygon: list[list[float]] = Field(default_factory=list)  # normalized 0..1
    max_occupancy: int | None = None


@app.get("/zones")
async def list_zones(camera_id: uuid.UUID | None = None) -> list[dict]:
    async with async_session_factory() as session:
        q = select(Zone)
        if camera_id:
            q = q.where(Zone.camera_id == camera_id)
        zones = (await session.execute(q)).scalars().all()
    return [
        {
            "id": str(z.id),
            "name": z.name,
            "zone_type": z.zone_type.value,
            "camera_id": str(z.camera_id) if z.camera_id else None,
            "polygon": (z.polygon_image or {}).get("points"),
            "max_occupancy": z.max_occupancy,
        }
        for z in zones
    ]


@app.post("/zones", status_code=201)
async def create_zone(payload: ZoneIn) -> dict:
    async with async_session_factory() as session:
        site_id = payload.site_id
        if site_id is None:
            cam = await session.get(Camera, payload.camera_id)
            site_id = cam.site_id if cam else None
        if site_id is None:
            raise HTTPException(400, "camera has no site; provide site_id")
        zone = Zone(
            site_id=site_id,
            camera_id=payload.camera_id,
            name=payload.name,
            zone_type=ZoneType(payload.zone_type),
            polygon_image={"points": payload.polygon, "norm": True} if payload.polygon else None,
            max_occupancy=payload.max_occupancy,
        )
        session.add(zone)
        await session.commit()
        zid = zone.id
    return {"id": str(zid)}


@app.put("/zones/{zone_id}")
async def update_zone(zone_id: uuid.UUID, payload: ZoneIn) -> dict:
    async with async_session_factory() as session:
        zone = await session.get(Zone, zone_id)
        if zone is None:
            raise HTTPException(404, "zone not found")
        zone.name = payload.name
        zone.zone_type = ZoneType(payload.zone_type)
        zone.polygon_image = {"points": payload.polygon, "norm": True} if payload.polygon else None
        zone.max_occupancy = payload.max_occupancy
        await session.commit()
    return {"id": str(zone_id)}


@app.delete("/zones/{zone_id}")
async def delete_zone(zone_id: uuid.UUID) -> dict:
    async with async_session_factory() as session:
        zone = await session.get(Zone, zone_id)
        if zone is None:
            raise HTTPException(404, "zone not found")
        await session.delete(zone)
        await session.commit()
    return {"deleted": str(zone_id)}


# ── zone analytics (segmentation-based) ───────────────────────


@app.get("/zones/{zone_id}/analytics")
async def zone_analytics(zone_id: uuid.UUID, minutes: int = Query(30, le=1440)) -> dict:
    """Occupancy + segmentation density + dwell analytics for a zone over a window,
    from the persisted per-zone metric time-series."""
    since = datetime.now(UTC) - timedelta(minutes=minutes)
    async with async_session_factory() as session:
        zone = await session.get(Zone, zone_id)
        if zone is None:
            raise HTTPException(404, "zone not found")
        rows = (
            await session.execute(
                select(ZoneSnapshot)
                .where(ZoneSnapshot.zone_id == zone_id, ZoneSnapshot.ts >= since)
                .order_by(ZoneSnapshot.ts)
            )
        ).scalars().all()
    series = [
        {
            "ts": r.ts.isoformat(),
            "occupancy": r.occupancy,
            "mask_coverage": r.mask_coverage,
            "avg_dwell_s": r.avg_dwell_s,
        }
        for r in rows
    ]
    occ = [r.occupancy for r in rows]
    cov = [r.mask_coverage for r in rows]
    return {
        "zone_id": str(zone_id),
        "zone_name": zone.name,
        "window_minutes": minutes,
        "samples": len(series),
        "occupancy": {
            "current": occ[-1] if occ else 0,
            "peak": max(occ) if occ else 0,
            "avg": round(sum(occ) / len(occ), 2) if occ else 0.0,
        },
        "density": {
            "current_coverage": cov[-1] if cov else 0.0,
            "peak_coverage": max(cov) if cov else 0.0,
            "avg_coverage": round(sum(cov) / len(cov), 4) if cov else 0.0,
        },
        "avg_dwell_s": rows[-1].avg_dwell_s if rows else 0.0,
        "series": series,
    }


@app.get("/analytics/baselines")
async def zone_baselines() -> dict:
    """Per-zone learned occupancy baseline (mean +- std over trailing history,
    excluding recent) vs the live occupancy, with the current z-score. This is the
    'normal' the anomaly detector compares against, surfaced for the operator."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=300)
    window_start = now - timedelta(hours=6)
    async with async_session_factory() as session:
        base = (
            await session.execute(
                select(
                    ZoneSnapshot.zone_id,
                    func.avg(ZoneSnapshot.occupancy),
                    func.coalesce(func.stddev_pop(ZoneSnapshot.occupancy), 0.0),
                    func.count(),
                )
                .where(ZoneSnapshot.ts < cutoff, ZoneSnapshot.ts >= window_start)
                .group_by(ZoneSnapshot.zone_id)
            )
        ).all()
        # latest snapshot per zone = current state
        recent = (
            await session.execute(
                select(ZoneSnapshot)
                .where(ZoneSnapshot.ts >= now - timedelta(seconds=45))
                .order_by(ZoneSnapshot.ts.desc())
            )
        ).scalars().all()
        znames = {
            str(zid): (name, ztype)
            for zid, name, ztype in (
                await session.execute(select(Zone.id, Zone.name, Zone.zone_type))
            ).all()
        }
    current: dict[str, dict] = {}
    for r in recent:
        z = str(r.zone_id)
        if z not in current:  # first = most recent due to desc order
            current[z] = {"occupancy": r.occupancy, "coverage": r.mask_coverage}
    out = []
    for zid, mean, std, n in base:
        z = str(zid)
        cur = current.get(z, {}).get("occupancy")
        std_f = max(float(std), 0.5)
        zscore = round((cur - float(mean)) / std_f, 2) if cur is not None else None
        name, ztype = znames.get(z, ("?", None))
        out.append(
            {
                "zone_id": z,
                "zone": name,
                "zone_type": ztype.value if ztype else None,
                "baseline_mean": round(float(mean), 2),
                "baseline_std": round(float(std), 2),
                "samples": int(n),
                "current_occupancy": cur,
                "z_score": zscore,
                "anomalous": bool(
                    zscore is not None and zscore >= 3.0 and (cur - float(mean)) >= 3.0
                ),
                "learned": int(n) >= 30,
            }
        )
    out.sort(key=lambda r: (r["z_score"] is None, -(r["z_score"] or 0)))
    return {"zones": out}


@app.get("/playbooks/activity")
async def playbook_activity(limit: int = Query(30, le=100)) -> dict:
    """Recent SOP playbook executions (what fired, on which incident, which actions
    ran), from the audit trail, so operators see the automated response."""
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(AuditLogEntry)
                .where(AuditLogEntry.action == "playbook.triggered")
                .order_by(AuditLogEntry.ts.desc())
                .limit(limit)
            )
        ).scalars().all()
    return {
        "count": len(rows),
        "activity": [
            {
                "ts": r.ts.isoformat(),
                "incident_id": r.resource_id,
                "playbook": (r.details or {}).get("playbook"),
                "actions": (r.details or {}).get("actions", []),
            }
            for r in rows
        ],
    }


# ── door-health diagnostics ───────────────────────────────────

_UNSECURED_TYPES = {"door_forced", "door_held"}
_ALARM_TYPES = {"door_forced", "door_held", "access_denied", "zone_trip"}


@app.get("/doors/health")
async def doors_health(hours: int = Query(24, le=720)) -> dict:
    """Doors with Issues: per-door alarm volume, how many were real threats (bound
    to a video incident) vs noise, the chronic-noise flag, and a root-cause hint."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(AccessEvent).where(
                    AccessEvent.ts >= since, AccessEvent.door_id.is_not(None)
                )
            )
        ).scalars().all()
        cam_names = {
            str(cid): n for cid, n in (await session.execute(select(Camera.id, Camera.name))).all()
        }
    doors: dict[str, dict] = {}
    for a in rows:
        d = doors.setdefault(a.door_id, {"total": 0, "alarms": 0, "threats": 0,
                                          "by_type": {}, "cameras": set()})
        et = a.event_type.value
        d["total"] += 1
        d["by_type"][et] = d["by_type"].get(et, 0) + 1
        if et in _ALARM_TYPES:
            d["alarms"] += 1
        if a.incident_id is not None:
            d["threats"] += 1
        if a.camera_id:
            d["cameras"].add(cam_names.get(str(a.camera_id), str(a.camera_id)[:8]))
    out = []
    for door_id, d in doors.items():
        alarms = d["alarms"]
        noise_ratio = round(1 - (d["threats"] / alarms), 3) if alarms else 0.0
        dominant = max(d["by_type"].items(), key=lambda kv: kv[1])[0] if d["by_type"] else None
        # root-cause hint from the dominant nuisance type
        cause = None
        if dominant == "door_held":
            cause = "Door frequently held/propped open (check closer or habit)"
        elif dominant == "door_forced" and d["threats"] == 0:
            cause = "Forced-door alarms with no person on video (likely faulty door sensor)"
        elif dominant == "access_denied":
            cause = "High invalid-badge volume (reader mis-mapping or wrong credentials)"
        chronic = alarms >= 10 and noise_ratio >= 0.7
        out.append({
            "door_id": door_id, "cameras": sorted(d["cameras"]),
            "total_events": d["total"], "alarms": alarms,
            "verified_threats": d["threats"], "noise_ratio": noise_ratio,
            "dominant_event": dominant, "chronic_noise": chronic, "root_cause": cause,
            "by_type": d["by_type"],
        })
    out.sort(key=lambda x: (x["chronic_noise"], x["alarms"]), reverse=True)
    return {"window_hours": hours, "doors": out}


@app.get("/doors/unsecured")
async def doors_unsecured(minutes: int = Query(30, le=1440)) -> dict:
    """Doors Unsecured: doors whose most recent event is a forced/held (open) state
    with no later normalizing badge use, i.e. physically unsecured right now."""
    since = datetime.now(UTC) - timedelta(minutes=minutes)
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(AccessEvent)
                .where(AccessEvent.ts >= since, AccessEvent.door_id.is_not(None))
                .order_by(AccessEvent.ts.desc())
            )
        ).scalars().all()
        cam_names = {
            str(cid): n for cid, n in (await session.execute(select(Camera.id, Camera.name))).all()
        }
    latest: dict[str, object] = {}
    for a in rows:
        if a.door_id not in latest:  # first seen = most recent (desc order)
            latest[a.door_id] = a
    unsecured = []
    for door_id, a in latest.items():
        if a.event_type.value in _UNSECURED_TYPES:
            unsecured.append({
                "door_id": door_id,
                "state": a.event_type.value,
                "camera": cam_names.get(str(a.camera_id)) if a.camera_id else None,
                "since": a.ts.isoformat(),
                "unsecured_seconds": round((now - a.ts).total_seconds()),
            })
    unsecured.sort(key=lambda x: x["unsecured_seconds"], reverse=True)
    return {"count": len(unsecured), "unsecured_doors": unsecured}


# ── live audio talk-down ──────────────────────────────────────


class TalkDownIn(BaseModel):
    message: str | None = None
    preset: str = "warning"


@app.post("/incidents/{incident_id}/audio/talk-down", status_code=201)
async def incident_talk_down(incident_id: uuid.UUID, payload: TalkDownIn) -> dict:
    """Speak a real deterrence message through the site speaker at the incident's
    camera: render TTS, deliver the audio to the speaker (dev stand-in sink), audit."""
    from sentigon_common.tts import PRESETS, synth_wav

    async with async_session_factory() as session:
        inc = await session.get(Incident, incident_id)
        if inc is None:
            raise HTTPException(404, "incident not found")
        cam = await session.get(Camera, inc.camera_id) if inc.camera_id else None
        cam_name = cam.name if cam else "unknown"
        cam_id = str(inc.camera_id) if inc.camera_id else "unknown"

    text = payload.message or PRESETS.get(payload.preset, PRESETS["warning"])
    wav, duration = await asyncio.to_thread(synth_wav, text)
    delivered = False
    detail = ""
    with contextlib.suppress(Exception):
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(
                common_settings.talkdown_sink_url,
                content=wav,
                headers={
                    "Content-Type": "audio/wav",
                    "X-Camera": cam_name,
                    "X-Message": text,
                },
            )
            delivered = r.status_code == 200
            detail = r.text[:200]
    async with async_session_factory() as session:
        session.add(
            AuditLogEntry(
                action="talkdown.delivered", resource_type="incident",
                resource_id=str(incident_id),
                details={"camera": cam_name, "message": text, "duration_s": duration,
                         "delivered": delivered},
            )
        )
        await session.commit()
    return {
        "incident_id": str(incident_id), "camera": cam_name, "camera_id": cam_id,
        "message": text, "duration_s": duration, "delivered": delivered, "sink": detail,
    }


# ── agentic video wall (activity-prioritized) ────────────────


@app.get("/wall/priority")
async def wall_priority(minutes: int = Query(5, le=60)) -> dict:
    """Rank cameras by live threat + activity so the video wall auto-surfaces the
    feeds that matter (highest open-incident risk + live object activity first),
    instead of a static grid. Agentic Video Wall."""
    since = datetime.now(UTC) - timedelta(minutes=minutes)
    open_states = [IncidentStatus.NEW, IncidentStatus.ACK, IncidentStatus.ESCALATED]
    async with async_session_factory() as session:
        cams = (await session.execute(select(Camera.id, Camera.name))).all()
        rows = (
            await session.execute(
                select(
                    Incident.camera_id,
                    func.count(),
                    func.coalesce(func.sum(Incident.risk_score), 0),
                    func.coalesce(func.max(Incident.risk_score), 0),
                )
                .where(Incident.status.in_(open_states), Incident.created_at >= since)
                .group_by(Incident.camera_id)
            )
        ).all()
    inc_by_cam = {str(cid): (n, int(rsum), int(rmax)) for cid, n, rsum, rmax in rows}
    # live object activity per camera from the perception workers
    obj_by_name: dict[str, int] = {}
    with contextlib.suppress(Exception):
        import httpx

        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get("http://localhost:8030/stats")
            if r.status_code == 200:
                for w in (r.json().get("workers") or r.json().get("cameras") or []):
                    obj_by_name[w.get("camera", "")] = int(w.get("objects", 0) or 0)

    wall = []
    for cid, name in cams:
        n, rsum, rmax = inc_by_cam.get(str(cid), (0, 0, 0))
        objs = obj_by_name.get(name, 0)
        # score: open-threat risk dominates, live activity adds, peak risk breaks ties
        score = rsum + rmax + objs * 4
        wall.append({
            "camera_id": str(cid), "camera": name, "score": score,
            "open_incidents": n, "max_risk": rmax, "live_objects": objs,
        })
    wall.sort(key=lambda w: (w["score"], w["max_risk"]), reverse=True)
    for i, w in enumerate(wall):
        w["rank"] = i + 1
    return {"cameras": wall, "generated_at": datetime.now(UTC).isoformat()}


# ── natural-language activity notifications ───────────────────


class NLAlertIn(BaseModel):
    name: str
    prompt: str
    camera_id: uuid.UUID
    severity: str = "MEDIUM"
    eval_interval_s: int = 30
    cooldown_s: int = 120


@app.post("/nl-alerts", status_code=201)
async def create_nl_alert(payload: NLAlertIn) -> dict:
    """Define an alert in plain English; the VLM evaluates it against live frames on
    the camera and fires an incident on a match (open-set, no signature authored)."""
    async with async_session_factory() as session:
        a = NLAlert(
            name=payload.name, prompt=payload.prompt, camera_id=payload.camera_id,
            severity=Severity(payload.severity.lower()), eval_interval_s=payload.eval_interval_s,
            cooldown_s=payload.cooldown_s,
        )
        session.add(a)
        await session.flush()
        session.add(
            AuditLogEntry(
                action="nl_alert.create", resource_type="nl_alert", resource_id=str(a.id),
                details={"name": payload.name, "prompt": payload.prompt},
            )
        )
        await session.commit()
        aid = a.id
    return {"id": str(aid), "name": payload.name, "prompt": payload.prompt}


@app.get("/nl-alerts")
async def list_nl_alerts() -> list[dict]:
    async with async_session_factory() as session:
        rows = (
            await session.execute(select(NLAlert).order_by(NLAlert.created_at.desc()))
        ).scalars().all()
        names = {str(cid): n for cid, n in (await session.execute(select(Camera.id, Camera.name))).all()}
    return [
        {
            "id": str(a.id), "name": a.name, "prompt": a.prompt,
            "camera_id": str(a.camera_id) if a.camera_id else None,
            "camera": names.get(str(a.camera_id)),
            "severity": a.severity.value, "active": a.active,
            "eval_interval_s": a.eval_interval_s, "cooldown_s": a.cooldown_s,
            "fire_count": a.fire_count,
            "last_fired_at": a.last_fired_at.isoformat() if a.last_fired_at else None,
        }
        for a in rows
    ]


@app.delete("/nl-alerts/{alert_id}")
async def delete_nl_alert(alert_id: uuid.UUID) -> dict:
    async with async_session_factory() as session:
        a = await session.get(NLAlert, alert_id)
        if a is None:
            raise HTTPException(404, "nl alert not found")
        await session.delete(a)
        await session.commit()
    return {"deleted": str(alert_id)}


# ── schedules (roster/delivery-aware suppression) ─────────────


class ScheduleIn(BaseModel):
    name: str
    reason: str | None = None
    camera_id: uuid.UUID | None = None
    zone_id: uuid.UUID | None = None
    signatures: list[str] = Field(default_factory=list)  # signature names; [] = all
    days_of_week: list[int] = Field(default_factory=list)  # 0=Mon..6=Sun; [] = all
    start_minute: int  # minutes since local midnight
    end_minute: int


@app.post("/schedules", status_code=201)
async def create_schedule(payload: ScheduleIn, request: Request) -> dict:
    """Define an expected-activity window that suppresses matching alarms (a scheduled
    dock delivery or nightly cleaning crew does not alarm during its window)."""
    # A window with no camera/zone/signature scope suppresses ALL alarms globally —
    # that blinds the platform, so require admin. Scoped windows: investigator+.
    is_global = not payload.camera_id and not payload.zone_id and not payload.signatures
    _require_role(request, UserRole.ADMIN if is_global else UserRole.INVESTIGATOR)
    async with async_session_factory() as session:
        s = ScheduleWindow(
            name=payload.name, reason=payload.reason,
            camera_id=payload.camera_id, zone_id=payload.zone_id,
            signatures=payload.signatures, days_of_week=payload.days_of_week,
            start_minute=payload.start_minute, end_minute=payload.end_minute,
        )
        session.add(s)
        await session.flush()
        session.add(
            AuditLogEntry(
                action="schedule.create", resource_type="schedule", resource_id=str(s.id),
                details={"name": payload.name, "signatures": payload.signatures},
            )
        )
        await session.commit()
        sid = s.id
    return {"id": str(sid), "name": payload.name}


@app.get("/schedules")
async def list_schedules() -> list[dict]:
    async with async_session_factory() as session:
        rows = (
            await session.execute(select(ScheduleWindow).order_by(ScheduleWindow.created_at.desc()))
        ).scalars().all()
    return [
        {
            "id": str(s.id), "name": s.name, "reason": s.reason,
            "camera_id": str(s.camera_id) if s.camera_id else None,
            "zone_id": str(s.zone_id) if s.zone_id else None,
            "signatures": s.signatures or [], "days_of_week": s.days_of_week or [],
            "start_minute": s.start_minute, "end_minute": s.end_minute,
            "active": s.active, "suppressed_count": s.suppressed_count,
            "window": f"{s.start_minute // 60:02d}:{s.start_minute % 60:02d}-{s.end_minute // 60:02d}:{s.end_minute % 60:02d}",
        }
        for s in rows
    ]


@app.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: uuid.UUID) -> dict:
    async with async_session_factory() as session:
        s = await session.get(ScheduleWindow, schedule_id)
        if s is None:
            raise HTTPException(404, "schedule not found")
        await session.delete(s)
        await session.commit()
    return {"deleted": str(schedule_id)}


# ── watchlists (appearance BOLO) ──────────────────────────────

_REID_COLLECTION = "reid"
_WATCHLIST_COLLECTION = "watchlist"
_VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle", "bicycle", "auto-rickshaw"}


def _qdrant():
    from qdrant_client import QdrantClient

    return QdrantClient(url=common_settings.qdrant_url)


def _track_centroid(client, camera_id: str, track_id: int) -> tuple[list[float] | None, str, int]:
    """Reference appearance embedding for a track, from the reid index.

    ByteTrack reuses low track ids across footage loops, so a track's all-time
    appearances can span several distinct people. We group by timestamp gap
    (>8s = a new contiguous sighting) and take the mean of the MOST RECENT
    sighting only, so the reference is one coherent real person, not an average
    of many."""
    from datetime import datetime

    from qdrant_client import models as qm

    flt = qm.Filter(
        must=[
            qm.FieldCondition(key="camera_id", match=qm.MatchValue(value=camera_id)),
            qm.FieldCondition(key="track_id", match=qm.MatchValue(value=track_id)),
        ]
    )
    items: list[tuple[datetime, list[float]]] = []
    object_class = "person"
    offset = None
    while True:
        points, offset = client.scroll(
            _REID_COLLECTION, scroll_filter=flt, limit=256, offset=offset,
            with_payload=True, with_vectors=True,
        )
        for p in points:
            pl = p.payload or {}
            object_class = pl.get("object_class", object_class)
            ts = pl.get("ts")
            if p.vector is not None and ts:
                items.append((datetime.fromisoformat(ts), list(p.vector)))
        if offset is None or not points:
            break
    if not items:
        return None, object_class, 0
    items.sort(key=lambda x: x[0])
    # walk back from the newest appearance, collecting the contiguous session
    session = [items[-1][1]]
    prev = items[-1][0]
    for ts, vec in reversed(items[:-1]):
        if (prev - ts).total_seconds() > 8.0:
            break
        session.append(vec)
        prev = ts
    dim = len(session[0])
    centroid = [sum(v[i] for v in session) / len(session) for i in range(dim)]
    return centroid, object_class, len(session)


class WatchlistEnrollIn(BaseModel):
    camera_id: str
    track_id: int
    label: str
    reason: str | None = None
    # calibrated on real footage with the OSNet-AIN backbone (bench/watchlist_eval.py):
    # same-identity mean ~0.90, cross-identity mean ~0.57; 0.88 keeps strong recall
    # while the downstream VLM + human step confirms the tail.
    threshold: float = 0.88


@app.post("/watchlist/enroll", status_code=201)
async def watchlist_enroll(payload: WatchlistEnrollIn) -> dict:
    """Enroll a be-on-the-lookout target from a REAL captured track: its mean
    appearance embedding becomes the reference matched against live detections."""
    from qdrant_client.models import PointStruct

    client = _qdrant()
    centroid, object_class, appearances = await asyncio.to_thread(
        _track_centroid, client, payload.camera_id, payload.track_id
    )
    if centroid is None:
        raise HTTPException(404, "no appearances indexed for that camera/track; pick a live track")
    category = "vehicle" if object_class in _VEHICLE_CLASSES else "person"
    point_id = str(uuid.uuid4())

    async with async_session_factory() as session:
        entry = WatchlistEntry(
            label=payload.label,
            category=category,
            object_class=object_class,
            reason=payload.reason,
            threshold=payload.threshold,
            embedding_ref=point_id,
            enrolled_from=f"{payload.camera_id}:{payload.track_id}",
            appearances=appearances,
        )
        session.add(entry)
        await session.flush()
        wl_id = str(entry.id)
        session.add(
            AuditLogEntry(
                action="watchlist.enroll", resource_type="watchlist", resource_id=wl_id,
                details={"label": payload.label, "category": category, "from": entry.enrolled_from},
            )
        )
        await session.commit()

    await asyncio.to_thread(
        client.upsert, _WATCHLIST_COLLECTION,
        [PointStruct(
            id=point_id, vector=centroid,
            payload={"watchlist_id": wl_id, "label": payload.label,
                     "category": category, "object_class": object_class},
        )],
    )
    return {
        "id": wl_id, "label": payload.label, "category": category,
        "object_class": object_class, "appearances": appearances,
        "threshold": payload.threshold, "embedding_ref": point_id,
    }


class PlateWatchIn(BaseModel):
    plate: str
    label: str
    reason: str | None = None


@app.post("/watchlist/plate", status_code=201)
async def watchlist_plate(payload: PlateWatchIn) -> dict:
    """Enroll a number plate on the watchlist. The plate is stored only as a salted
    hash (DPDP): a live read hashes to the same value and fires a Plate Watchlist
    Hit, without the raw plate being persisted in the watchlist."""
    from sentigon_common.plates import normalize_plate, plate_hash

    norm = normalize_plate(payload.plate)
    if len(norm) < 3:
        raise HTTPException(400, "plate too short after normalization")
    h = plate_hash(norm)
    async with async_session_factory() as session:
        entry = WatchlistEntry(
            label=payload.label, category="plate", object_class="plate",
            reason=payload.reason, threshold=1.0, embedding_ref=h,
            enrolled_from="manual", appearances=1,
        )
        session.add(entry)
        await session.flush()
        wl_id = str(entry.id)
        session.add(
            AuditLogEntry(
                action="watchlist.plate_enroll", resource_type="watchlist",
                resource_id=wl_id, details={"label": payload.label},  # raw plate not logged
            )
        )
        await session.commit()
    return {"id": wl_id, "label": payload.label, "category": "plate", "plate_hash": h}


@app.get("/watchlist")
async def watchlist_list(active: bool | None = None) -> list[dict]:
    async with async_session_factory() as session:
        q = select(WatchlistEntry).order_by(WatchlistEntry.created_at.desc())
        if active is not None:
            q = q.where(WatchlistEntry.active.is_(active))
        rows = (await session.execute(q)).scalars().all()
    return [
        {
            "id": str(r.id), "label": r.label, "category": r.category,
            "object_class": r.object_class, "reason": r.reason, "threshold": r.threshold,
            "active": r.active, "appearances": r.appearances, "hit_count": r.hit_count,
            "enrolled_from": r.enrolled_from,
            "last_hit_at": r.last_hit_at.isoformat() if r.last_hit_at else None,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@app.delete("/watchlist/{entry_id}")
async def watchlist_delete(entry_id: uuid.UUID) -> dict:
    async with async_session_factory() as session:
        entry = await session.get(WatchlistEntry, entry_id)
        if entry is None:
            raise HTTPException(404, "watchlist entry not found")
        point_id = entry.embedding_ref
        await session.delete(entry)
        session.add(
            AuditLogEntry(
                action="watchlist.delete", resource_type="watchlist",
                resource_id=str(entry_id), details={"label": entry.label},
            )
        )
        await session.commit()
    with contextlib.suppress(Exception):
        client = _qdrant()
        await asyncio.to_thread(client.delete, _WATCHLIST_COLLECTION, [point_id])
    return {"deleted": str(entry_id)}


# ── signatures ────────────────────────────────────────────────


@app.get("/signatures")
async def list_signatures(
    category: str | None = None, enabled: bool | None = None, limit: int = Query(300, le=500)
) -> list[dict]:
    async with async_session_factory() as session:
        q = select(Signature).order_by(Signature.category, Signature.name).limit(limit)
        if category:
            q = q.where(Signature.category == category)
        if enabled is not None:
            q = q.where(Signature.enabled.is_(enabled))
        sigs = (await session.execute(q)).scalars().all()
    return [
        {
            "id": str(s.id),
            "name": s.name,
            "category": s.category,
            "description": s.description,
            "severity": s.severity.value,
            "detection_method": s.detection_method.value,
            "enabled": s.enabled,
            "source": s.source,
            "detection_count": s.detection_count,
            "params": s.params,
        }
        for s in sigs
    ]


@app.patch("/signatures/{signature_id}")
async def patch_signature(
    signature_id: uuid.UUID,
    request: Request,
    enabled: bool | None = Body(None, embed=True),
    severity: str | None = Body(None, embed=True),
    params: dict | None = Body(None, embed=True),
) -> dict:
    # detection-integrity change (disabling/retuning a signature) — investigator+
    _require_role(request, UserRole.INVESTIGATOR)
    async with async_session_factory() as session:
        sig = await session.get(Signature, signature_id)
        if sig is None:
            raise HTTPException(404, "signature not found")
        if enabled is not None:
            sig.enabled = enabled
        if severity is not None:
            sig.severity = Severity(severity)
        if params is not None:
            sig.params = params
            sig.version += 1
        await session.commit()
    return {"id": str(signature_id)}


class OpenVocabIn(BaseModel):
    name: str
    prompt: str
    severity: str = "medium"


@app.post("/signatures/open-vocab", status_code=201)
async def create_open_vocab(payload: OpenVocabIn) -> dict:
    from sentigon_common.schemas.enums import DetectionMethod

    classes = [c.strip() for c in payload.prompt.split(",") if c.strip()]
    async with async_session_factory() as session:
        existing = (
            await session.execute(select(Signature).where(Signature.name == payload.name))
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(409, "signature name already exists")
        sig = Signature(
            name=payload.name,
            category="open_vocab",
            description=f"Operator-defined open-vocabulary target: {payload.prompt}",
            severity=Severity(payload.severity),
            detection_method=DetectionMethod.OPEN_VOCAB,
            params={"open_vocab_prompt": payload.prompt, "classes": classes},
            source="custom",
        )
        session.add(sig)
        await session.commit()
        sid = sig.id
    return {"id": str(sid), "classes": classes}


# ── tamper-evident evidence vault ─────────────────────────────


@app.post("/evidence/index")
async def index_evidence(limit: int = Query(300, le=2000)) -> dict:
    """Append incident snapshots to the append-only, hash-chained evidence ledger."""
    added = 0
    async with async_session_factory() as session:
        existing = set(
            (
                await session.execute(
                    select(EvidenceRecord.reference_id).where(
                        EvidenceRecord.reference_id.is_not(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        incs = (
            (
                await session.execute(
                    select(Incident)
                    .where(Incident.snapshot_ref.is_not(None))
                    .order_by(Incident.created_at.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for inc in incs:
            if inc.id in existing or not inc.snapshot_ref or "/" not in inc.snapshot_ref:
                continue
            bucket, key = inc.snapshot_ref.split("/", 1)
            try:
                data = _store.get_bytes(bucket, key)
            except Exception:  # noqa: BLE001
                continue
            await append_evidence(
                session,
                kind="snapshot",
                data=data,
                bucket=bucket,
                object_key=key,
                reference_id=inc.id,
                meta={"incident": str(inc.id), "title": inc.title},
            )
            added += 1
        await session.commit()
    return {"indexed": added}


@app.get("/evidence")
async def list_evidence(limit: int = Query(50, le=1000)) -> list[dict]:
    async with async_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(EvidenceRecord).order_by(EvidenceRecord.seq.asc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "seq": r.seq,
            "content_hash": r.content_hash,
            "prev_hash": r.prev_hash,
            "kind": r.kind,
            "ref": f"{r.bucket}/{r.object_key}" if r.bucket else None,
            "reference_id": str(r.reference_id) if r.reference_id else None,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@app.get("/evidence/verify")
async def verify_evidence() -> dict:
    async with async_session_factory() as session:
        ok, breaks = await verify_chain(session)
        count = await session.scalar(select(func.count()).select_from(EvidenceRecord))
    return {"ok": ok, "records": count or 0, "breaks": breaks}


# ── cases ─────────────────────────────────────────────────────


class CaseIn(BaseModel):
    title: str
    description: str | None = None
    priority: str = "medium"
    incident_ids: list[uuid.UUID] = Field(default_factory=list)


async def _link_incidents(session, case_id: uuid.UUID, incident_ids: list[uuid.UUID]) -> None:
    if not incident_ids:
        return
    existing = set(
        (
            await session.execute(
                select(case_incidents.c.incident_id).where(case_incidents.c.case_id == case_id)
            )
        )
        .scalars()
        .all()
    )
    rows = [{"case_id": case_id, "incident_id": iid} for iid in incident_ids if iid not in existing]
    if rows:
        await session.execute(case_incidents.insert().values(rows))


@app.post("/cases", status_code=201)
async def create_case(payload: CaseIn) -> dict:
    async with async_session_factory() as session:
        case = Case(
            title=payload.title,
            description=payload.description,
            priority=Severity(payload.priority),
        )
        session.add(case)
        await session.flush()
        await _link_incidents(session, case.id, payload.incident_ids)
        await session.commit()
        cid = case.id
    return {"id": str(cid)}


@app.get("/cases")
async def list_cases() -> list[dict]:
    async with async_session_factory() as session:
        cases = (
            (await session.execute(select(Case).order_by(Case.created_at.desc()))).scalars().all()
        )
        counts = dict(
            (
                await session.execute(
                    select(case_incidents.c.case_id, func.count()).group_by(
                        case_incidents.c.case_id
                    )
                )
            ).all()
        )
    return [
        {
            "id": str(c.id),
            "title": c.title,
            "status": c.status.value,
            "priority": c.priority.value,
            "incidents": counts.get(c.id, 0),
            "created_at": c.created_at.isoformat(),
        }
        for c in cases
    ]


@app.get("/cases/{case_id}")
async def get_case(case_id: uuid.UUID) -> dict:
    async with async_session_factory() as session:
        case = await session.get(Case, case_id)
        if case is None:
            raise HTTPException(404, "case not found")
        incs = (
            await session.execute(
                select(Incident, Signature.name, Camera.name)
                .join(case_incidents, case_incidents.c.incident_id == Incident.id)
                .join(Signature, Incident.signature_id == Signature.id, isouter=True)
                .join(Camera, Incident.camera_id == Camera.id, isouter=True)
                .where(case_incidents.c.case_id == case_id)
                .order_by(Incident.created_at.desc())
            )
        ).all()
    return {
        "id": str(case.id),
        "title": case.title,
        "description": case.description,
        "status": case.status.value,
        "priority": case.priority.value,
        "created_at": case.created_at.isoformat(),
        "incidents": [
            {
                "id": str(i.id),
                "title": i.title,
                "severity": i.severity.value,
                "verdict": i.verdict.value if i.verdict else None,
                "signature": sn,
                "camera": cn,
                "snapshot_url": _presigned(i.snapshot_ref),
                "created_at": i.created_at.isoformat(),
            }
            for i, sn, cn in incs
        ],
    }


@app.post("/cases/{case_id}/incidents")
async def add_case_incidents(
    case_id: uuid.UUID, incident_ids: list[uuid.UUID] = Body(..., embed=True)
) -> dict:
    async with async_session_factory() as session:
        if await session.get(Case, case_id) is None:
            raise HTTPException(404, "case not found")
        await _link_incidents(session, case_id, incident_ids)
        await session.commit()
    return {"case_id": str(case_id), "added": len(incident_ids)}


@app.get("/cases/{case_id}/export")
async def export_case(case_id: uuid.UUID, request: Request) -> dict:
    """Chain-of-custody export: the case, its incidents, and the evidence hash manifest.
    Seals evidence into the append-only ledger, so it requires investigator+ and
    attributes the actor from the authenticated identity (never 'anonymous')."""
    _require_role(request, UserRole.INVESTIGATOR)
    actor = _current_user(request)
    async with async_session_factory() as session:
        case = await session.get(Case, case_id)
        if case is None:
            raise HTTPException(404, "case not found")
        incident_ids = (
            (
                await session.execute(
                    select(case_incidents.c.incident_id).where(case_incidents.c.case_id == case_id)
                )
            )
            .scalars()
            .all()
        )
        # seal the case's evidence into the chain if not already present
        already = set(
            (
                await session.execute(
                    select(EvidenceRecord.reference_id).where(
                        EvidenceRecord.reference_id.in_(incident_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        to_index = (
            (
                await session.execute(
                    select(Incident).where(
                        Incident.id.in_(incident_ids), Incident.snapshot_ref.is_not(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        for inc in to_index:
            if inc.id in already or "/" not in (inc.snapshot_ref or ""):
                continue
            bucket, key = inc.snapshot_ref.split("/", 1)
            try:
                data = _store.get_bytes(bucket, key)
            except Exception:  # noqa: BLE001
                continue
            await append_evidence(
                session,
                kind="snapshot",
                data=data,
                bucket=bucket,
                object_key=key,
                reference_id=inc.id,
                meta={"incident": str(inc.id), "case": str(case_id)},
            )
        # Footage-access logging: who exported which footage, when (DPDP / enterprise).
        session.add(
            AuditLogEntry(
                user_id=actor.id if actor else None,
                action="footage.exported",
                resource_type="case",
                resource_id=str(case_id),
                details={
                    "actor": actor.email if actor else "anonymous",
                    "incident_count": len(incident_ids),
                    "kind": "chain-of-custody export",
                },
            )
        )
        await session.commit()
        evidence = (
            (
                await session.execute(
                    select(EvidenceRecord).where(EvidenceRecord.reference_id.in_(incident_ids))
                )
            )
            .scalars()
            .all()
        )
        chain_ok, _ = await verify_chain(session)
    return {
        "case": {"id": str(case.id), "title": case.title, "status": case.status.value},
        "incident_count": len(incident_ids),
        "evidence_chain_verified": chain_ok,
        "evidence_manifest": [
            {
                "seq": e.seq,
                "content_hash": e.content_hash,
                "prev_hash": e.prev_hash,
                "ref": f"{e.bucket}/{e.object_key}",
            }
            for e in evidence
        ],
    }


# ── summary ───────────────────────────────────────────────────


@app.get("/summary")
async def summary() -> dict:
    async with async_session_factory() as session:
        by_sev = dict(
            (
                await session.execute(
                    select(Incident.severity, func.count())
                    .where(Incident.status.in_([IncidentStatus.NEW, IncidentStatus.ESCALATED]))
                    .group_by(Incident.severity)
                )
            ).all()
        )
        total_open = sum(by_sev.values())
        total = await session.scalar(select(func.count()).select_from(Incident))
    return {
        "open_incidents": total_open,
        "total_incidents": total or 0,
        "by_severity": {k.value: v for k, v in by_sev.items()},
    }


# ── analytics ─────────────────────────────────────────────────


@app.get("/analytics/overview")
async def analytics_overview() -> dict:
    async with async_session_factory() as session:
        total = await session.scalar(select(func.count()).select_from(Incident)) or 0
        by_sev = {
            s.value: c
            for s, c in (
                await session.execute(
                    select(Incident.severity, func.count()).group_by(Incident.severity)
                )
            ).all()
        }
        by_status = {
            s.value: c
            for s, c in (
                await session.execute(
                    select(Incident.status, func.count()).group_by(Incident.status)
                )
            ).all()
        }
        verified = (
            await session.scalar(
                select(func.count()).select_from(Incident).where(Incident.verdict.is_not(None))
            )
            or 0
        )
        rejected = (
            await session.scalar(
                select(func.count())
                .select_from(Incident)
                .where(Incident.verdict == Verdict.REJECTED)
            )
            or 0
        )
        confirmed = (
            await session.scalar(
                select(func.count())
                .select_from(Incident)
                .where(Incident.verdict == Verdict.CONFIRMED)
            )
            or 0
        )
    return {
        "total_incidents": total,
        "by_severity": by_sev,
        "by_status": by_status,
        "verified": verified,
        "confirmed": confirmed,
        "rejected": rejected,
        "false_alarm_rate": round(rejected / verified, 4) if verified else 0.0,
    }


@app.get("/analytics/timeseries")
async def analytics_timeseries(hours: int = Query(6, le=168)) -> list[dict]:
    from sqlalchemy import text

    async with async_session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT to_char(date_trunc('minute', created_at), 'HH24:MI') b, "
                    "lower(severity::text) s, count(*) c FROM incidents "
                    "WHERE created_at > now() - make_interval(hours => :h) "
                    "GROUP BY 1, 2 ORDER BY 1"
                ),
                {"h": hours},
            )
        ).all()
    buckets: dict[str, dict] = {}
    for b, s, c in rows:
        d = buckets.setdefault(
            b, {"t": b, "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        )
        d[s] = c
    return list(buckets.values())


@app.get("/analytics/by-signature")
async def analytics_by_signature(limit: int = Query(12, le=50)) -> list[dict]:
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(Signature.name, func.count())
                .join(Incident, Incident.signature_id == Signature.id)
                .group_by(Signature.name)
                .order_by(func.count().desc())
                .limit(limit)
            )
        ).all()
    return [{"signature": n, "count": c} for n, c in rows]


@app.get("/analytics/by-camera")
async def analytics_by_camera() -> list[dict]:
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(Camera.name, func.count())
                .join(Incident, Incident.camera_id == Camera.id)
                .group_by(Camera.name)
                .order_by(func.count().desc())
            )
        ).all()
    return [{"camera": n, "count": c} for n, c in rows]


# ── model governance (champion-challenger) ────────────────────

_DEFAULT_MODELS = [
    {"name": "YOLO26m", "role": ModelRole.DETECTOR, "version": "yolo26m", "stage": ModelStage.CHAMPION, "artifact_ref": "yolo26m.pt", "params": {"imgsz": 640, "conf": 0.35, "tracker": "bytetrack"}},
    {"name": "YOLO26x", "role": ModelRole.DETECTOR, "version": "yolo26x", "stage": ModelStage.CHALLENGER, "artifact_ref": "yolo26x.pt", "params": {"imgsz": 640, "conf": 0.35}},
    {"name": "Qwen2.5-VL-7B (Ollama)", "role": ModelRole.VLM, "version": "qwen2.5vl:7b", "stage": ModelStage.CHAMPION, "artifact_ref": "ollama:qwen2.5vl:7b", "params": {"backend": "ollama"}},
    {"name": "Qwen3-VL-32B (RunPod/vLLM)", "role": ModelRole.VLM, "version": "qwen3-vl-32b", "stage": ModelStage.CHALLENGER, "artifact_ref": "vllm:Qwen/Qwen3-VL-32B-Instruct", "params": {"backend": "vllm", "target": "runpod"}},
    {"name": "ResNet50-ReID", "role": ModelRole.REID, "version": "resnet50", "stage": ModelStage.CHAMPION, "artifact_ref": "torchvision:resnet50", "params": {"dim": 2048}},
]


@app.post("/models/register")
async def register_models() -> dict:
    """Register the real running models (champions) plus challengers. Idempotent."""
    created = 0
    async with async_session_factory() as session:
        for m in _DEFAULT_MODELS:
            existing = (
                await session.execute(select(ModelVersion).where(ModelVersion.name == m["name"]))
            ).scalar_one_or_none()
            if existing is not None:
                continue
            mv = ModelVersion(
                name=m["name"],
                role=m["role"],
                version=m["version"],
                stage=m["stage"],
                artifact_ref=m["artifact_ref"],
                params=m["params"],
            )
            if m["stage"] == ModelStage.CHAMPION:
                mv.promoted_at = datetime.now(UTC)
            session.add(mv)
            await session.flush()
            created += 1
            if m["role"] == ModelRole.VLM and m["stage"] == ModelStage.CHAMPION:
                total = await session.scalar(
                    select(func.count()).select_from(Incident).where(Incident.verdict.is_not(None))
                ) or 0
                rejected = await session.scalar(
                    select(func.count()).select_from(Incident).where(Incident.verdict == Verdict.REJECTED)
                ) or 0
                confirmed = await session.scalar(
                    select(func.count()).select_from(Incident).where(Incident.verdict == Verdict.CONFIRMED)
                ) or 0
                session.add(
                    EvalRun(
                        model_version_id=mv.id,
                        gold_set="production",
                        metrics={
                            "verified": total,
                            "confirmed": confirmed,
                            "rejected": rejected,
                            "false_alarm_reduction": round(rejected / total, 4) if total else 0.0,
                        },
                        passed=True,
                        notes="live production verification metrics",
                    )
                )
        await session.commit()
    return {"registered": created}


@app.get("/models/drift")
async def model_drift(hours: int = Query(12, le=168)) -> dict:
    """Per-model drift signal: the VLM confirm-rate (precision proxy) and mean
    confidence over hourly buckets. A downward confirm-rate trend flags drift."""
    from sqlalchemy import text as _text

    async with async_session_factory() as session:
        rows = (
            await session.execute(
                _text(
                    "SELECT to_char(date_trunc('hour', created_at), 'MM-DD HH24:00') b, "
                    "count(*) FILTER (WHERE verdict='CONFIRMED') c, "
                    "count(*) FILTER (WHERE verdict='REJECTED') r, "
                    "round(avg(confidence)::numeric,3) conf "
                    "FROM incidents WHERE verdict IS NOT NULL "
                    "AND created_at > now() - make_interval(hours => :h) "
                    "GROUP BY 1 ORDER BY 1"
                ),
                {"h": hours},
            )
        ).all()
    buckets = [
        {
            "bucket": b,
            "confirmed": c,
            "rejected": r,
            "confirm_rate": round(c / (c + r), 3) if (c + r) else None,
            "mean_confidence": float(conf) if conf is not None else None,
        }
        for b, c, r, conf in rows
    ]
    rates = [x["confirm_rate"] for x in buckets if x["confirm_rate"] is not None]
    drift = round(rates[-1] - rates[0], 3) if len(rates) >= 2 else 0.0
    return {
        "model": common_settings.reason_model,
        "window_hours": hours,
        "confirm_rate_drift": drift,
        "drift_flag": drift < -0.15,
        "buckets": buckets,
    }


@app.get("/models")
async def list_models() -> list[dict]:
    async with async_session_factory() as session:
        mvs = (
            await session.execute(select(ModelVersion).order_by(ModelVersion.role, ModelVersion.stage))
        ).scalars().all()
        out = []
        for mv in mvs:
            latest = (
                await session.execute(
                    select(EvalRun)
                    .where(EvalRun.model_version_id == mv.id)
                    .order_by(EvalRun.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            out.append(
                {
                    "id": str(mv.id),
                    "name": mv.name,
                    "role": mv.role.value,
                    "version": mv.version,
                    "stage": mv.stage.value,
                    "artifact_ref": mv.artifact_ref,
                    "params": mv.params,
                    "promoted_at": mv.promoted_at.isoformat() if mv.promoted_at else None,
                    "latest_eval": latest.metrics if latest else None,
                }
            )
    return out


@app.post("/models/{model_id}/promote")
async def promote_model(model_id: uuid.UUID, request: Request, force: bool = False) -> dict:
    """Atomically promote a model to champion, retiring the current champion of its
    role. Champion-challenger gate: promotion is blocked unless the model has a
    passing eval run, so an unvalidated challenger cannot reach production. An admin
    may override with force=true (audited) for an emergency rollback."""
    _require_role(request, UserRole.ADMIN)
    async with async_session_factory() as session:
        mv = await session.get(ModelVersion, model_id)
        if mv is None:
            raise HTTPException(404, "model not found")
        latest_eval = (
            await session.execute(
                select(EvalRun)
                .where(EvalRun.model_version_id == mv.id)
                .order_by(EvalRun.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if not force and (latest_eval is None or not latest_eval.passed):
            raise HTTPException(
                409,
                "promotion blocked: model has no passing eval run (champion-challenger "
                "gate). Run an eval that passes, or pass force=true to override.",
            )
        current = (
            await session.execute(
                select(ModelVersion).where(
                    ModelVersion.role == mv.role, ModelVersion.stage == ModelStage.CHAMPION
                )
            )
        ).scalars().all()
        for c in current:
            if c.id != mv.id:
                c.stage = ModelStage.RETIRED
        mv.stage = ModelStage.CHAMPION
        mv.promoted_at = datetime.now(UTC)
        session.add(
            AuditLogEntry(
                action="model.promoted",
                resource_type="model_version",
                resource_id=str(mv.id),
                details={
                    "name": mv.name,
                    "role": mv.role.value,
                    "forced": force,
                    "eval_passed": bool(latest_eval and latest_eval.passed),
                },
            )
        )
        await session.commit()
        role = mv.role.value
    return {"promoted": str(model_id), "role": role}


# ── admin: users + audit (admin-role gated) ───────────────────


async def _require_admin(request: Request) -> User:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    user = await user_from_token(token)
    if user is None:
        raise HTTPException(401, "authentication required")
    if user.role != UserRole.ADMIN:
        raise HTTPException(403, "admin role required")
    return user


class UserIn(BaseModel):
    email: str
    full_name: str
    password: str
    role: str = "operator"


class UserPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None


def _user_dict(u: User) -> dict:
    return {
        "id": str(u.id),
        "email": u.email,
        "full_name": u.full_name,
        "role": u.role.value,
        "is_active": u.is_active,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@app.get("/users")
async def list_users(request: Request) -> list[dict]:
    await _require_admin(request)
    async with async_session_factory() as session:
        rows = (await session.execute(select(User).order_by(User.created_at))).scalars().all()
    return [_user_dict(u) for u in rows]


@app.post("/users")
async def create_user(request: Request, body: UserIn) -> dict:
    admin = await _require_admin(request)
    async with async_session_factory() as session:
        exists = (
            await session.execute(select(User).where(User.email == body.email))
        ).scalar_one_or_none()
        if exists is not None:
            raise HTTPException(409, "email already exists")
        u = User(
            email=body.email,
            full_name=body.full_name,
            hashed_password=hash_password(body.password),
            role=UserRole(body.role),
        )
        session.add(u)
        await session.flush()
        session.add(
            AuditLogEntry(
                user_id=admin.id,
                action="user.created",
                resource_type="user",
                resource_id=str(u.id),
                details={"email": u.email, "role": u.role.value},
            )
        )
        await session.commit()
        return _user_dict(u)


@app.patch("/users/{user_id}")
async def patch_user(request: Request, user_id: uuid.UUID, body: UserPatch) -> dict:
    admin = await _require_admin(request)
    async with async_session_factory() as session:
        u = await session.get(User, user_id)
        if u is None:
            raise HTTPException(404, "user not found")
        if body.role is not None:
            u.role = UserRole(body.role)
        if body.is_active is not None:
            u.is_active = body.is_active
        session.add(
            AuditLogEntry(
                user_id=admin.id,
                action="user.updated",
                resource_type="user",
                resource_id=str(u.id),
                details={"role": u.role.value, "is_active": u.is_active},
            )
        )
        await session.commit()
        return _user_dict(u)


@app.get("/audit")
async def list_audit(request: Request, limit: int = 100) -> list[dict]:
    await _require_admin(request)
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(AuditLogEntry).order_by(AuditLogEntry.ts.desc()).limit(min(limit, 500))
            )
        ).scalars().all()
    return [
        {
            "id": str(a.id),
            "action": a.action,
            "resource_type": a.resource_type,
            "resource_id": a.resource_id,
            "details": a.details,
            "ts": a.ts.isoformat() if a.ts else None,
        }
        for a in rows
    ]


# ── health aggregation (server-side probe of every service) ───

_SERVICE_PROBES = [
    ("api", "http://localhost:8010/healthz", None),
    ("ingest", "http://localhost:8020/healthz", "http://localhost:8020/health/summary"),
    ("perception", "http://localhost:8030/healthz", "http://localhost:8030/stats"),
    ("context", "http://localhost:8040/healthz", None),
    ("reason", "http://localhost:8050/healthz", "http://localhost:8050/stats"),
    ("mediasource", "http://localhost:8055/healthz", None),
    ("search", "http://localhost:8060/healthz", "http://localhost:8060/stats"),
    ("notify", "http://localhost:8070/healthz", "http://localhost:8070/stats"),
    ("dispatch", "http://localhost:8081/healthz", "http://localhost:8081/stats"),
    ("fleet", "http://localhost:8082/healthz", "http://localhost:8082/stats"),
    ("crosssite", "http://localhost:8086/healthz", "http://localhost:8086/stats"),
]


@app.get("/health/services")
async def health_services() -> dict:
    import httpx

    out = []
    async with httpx.AsyncClient(timeout=2.5) as client:
        for name, health_url, stats_url in _SERVICE_PROBES:
            entry: dict = {"name": name, "up": False, "stats": None}
            try:
                r = await client.get(health_url)
                entry["up"] = r.status_code == 200
            except Exception:  # noqa: BLE001
                entry["up"] = False
            if entry["up"] and stats_url:
                try:
                    entry["stats"] = (await client.get(stats_url)).json()
                except Exception:  # noqa: BLE001
                    entry["stats"] = None
            out.append(entry)
    # perception per-camera detail bubbled up for the dashboard
    perception = next((s for s in out if s["name"] == "perception"), None)
    cameras = (perception or {}).get("stats", {}).get("cameras", []) if perception else []
    return {"services": out, "cameras": cameras}


# ── context graph (entities + events node-link) ───────────────


@app.get("/graph")
async def context_graph(limit: int = 40) -> dict:
    """Nodes (cameras, zones, signatures, recent incidents) + edges linking them."""
    async with async_session_factory() as session:
        cameras = (await session.execute(select(Camera))).scalars().all()
        zones = (await session.execute(select(Zone))).scalars().all()
        incidents = (
            await session.execute(
                select(Incident).order_by(Incident.created_at.desc()).limit(min(limit, 100))
            )
        ).scalars().all()
        sig_ids = {i.signature_id for i in incidents if i.signature_id}
        sigs = (
            await session.execute(select(Signature).where(Signature.id.in_(sig_ids)))
        ).scalars().all() if sig_ids else []

    nodes = []
    for c in cameras:
        nodes.append({"id": f"cam:{c.id}", "kind": "camera", "label": c.name, "status": c.status.value})
    for z in zones:
        nodes.append({"id": f"zone:{z.id}", "kind": "zone", "label": z.name})
    for s in sigs:
        nodes.append({"id": f"sig:{s.id}", "kind": "signature", "label": s.name, "severity": s.severity.value})
    for i in incidents:
        nodes.append(
            {
                "id": f"inc:{i.id}",
                "kind": "incident",
                "label": i.title[:40],
                "severity": i.severity.value,
                "status": i.status.value,
            }
        )

    edges = []
    for z in zones:
        if z.camera_id:
            edges.append({"source": f"zone:{z.id}", "target": f"cam:{z.camera_id}", "rel": "on"})
    for i in incidents:
        if i.camera_id:
            edges.append({"source": f"inc:{i.id}", "target": f"cam:{i.camera_id}", "rel": "at"})
        if i.signature_id and i.signature_id in sig_ids:
            edges.append({"source": f"inc:{i.id}", "target": f"sig:{i.signature_id}", "rel": "matched"})
        if i.zone_id:
            edges.append({"source": f"inc:{i.id}", "target": f"zone:{i.zone_id}", "rel": "in"})
    return {"nodes": nodes, "edges": edges}


class CameraMapPatch(BaseModel):
    lat: float
    lng: float
    heading: float = 0.0
    fov: float = 60.0


@app.patch("/cameras/{camera_id}/map")
async def set_camera_map(camera_id: uuid.UUID, body: CameraMapPatch) -> dict:
    """Persist a camera's map placement (position + heading + FOV) in its meta."""
    async with async_session_factory() as session:
        c = await session.get(Camera, camera_id)
        if c is None:
            raise HTTPException(404, "camera not found")
        meta = dict(c.meta or {})
        meta["map"] = {"lat": body.lat, "lng": body.lng, "heading": body.heading, "fov": body.fov}
        c.meta = meta
        await session.commit()
    return {"camera_id": str(camera_id), "map": meta["map"]}


# ── access-control fusion (pluggable adapter: webhook/OSDP/MQTT feed) ──


class AccessEventIn(BaseModel):
    event_type: str  # access_granted | access_denied | door_forced | door_held | zone_trip
    panel_id: str | None = None
    door_id: str | None = None
    badge_id: str | None = None
    camera_id: uuid.UUID | None = None
    camera_name: str | None = None
    raw: dict = Field(default_factory=dict)


@app.post("/access-events", status_code=201)
async def ingest_access_event(payload: AccessEventIn) -> dict:
    """Generic access-control/alarm webhook adapter. Creates an AccessEvent and fuses
    it to a recent incident on the same camera (verified alarm = signal + video)."""
    async with async_session_factory() as session:
        camera_id = payload.camera_id
        if camera_id is None and payload.camera_name:
            cam = (
                await session.execute(select(Camera).where(Camera.name == payload.camera_name))
            ).scalar_one_or_none()
            camera_id = cam.id if cam else None
        ae = AccessEvent(
            panel_id=payload.panel_id,
            door_id=payload.door_id,
            event_type=AccessEventType(payload.event_type),
            ts=datetime.now(UTC),
            badge_id=payload.badge_id,
            camera_id=camera_id,
            raw=payload.raw,
        )
        session.add(ae)
        await session.flush()
        bound = None
        recent = None
        if camera_id:
            recent = (
                await session.execute(
                    select(Incident)
                    .where(
                        Incident.camera_id == camera_id,
                        Incident.created_at > datetime.now(UTC) - timedelta(seconds=90),
                    )
                    .order_by(Incident.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if recent is not None:
                ae.incident_id = recent.id
                bound = str(recent.id)
        # ── signal fusion: a video incident corroborated by an access-control
        # signal is a VERIFIED, higher-confidence threat. Elevate it. ──
        elevated = False
        threatening = payload.event_type in {
            "door_forced", "door_held", "access_denied", "zone_trip",
        }
        if recent is not None:
            corr = (
                await session.scalar(
                    select(func.count())
                    .select_from(AccessEvent)
                    .where(AccessEvent.incident_id == recent.id)
                )
            ) or 1
            sig_cat = (
                await session.scalar(
                    select(Signature.category).where(Signature.id == recent.signature_id)
                )
                if recent.signature_id
                else None
            )
            recent.risk_score, _ = compute_risk_score(
                severity=recent.severity.value,
                category=sig_cat,
                confidence=recent.confidence,
                verdict=recent.verdict.value if recent.verdict else None,
                correlated_signals=corr,
            )
            recent.attributes = {
                **(recent.attributes or {}),
                "fused_access": {
                    "type": payload.event_type,
                    "door_id": payload.door_id,
                    "badge_id": payload.badge_id,
                    "signals": corr,
                },
            }
            if threatening:
                if recent.severity in (Severity.LOW, Severity.MEDIUM, Severity.INFO):
                    recent.severity = Severity.HIGH
                    recent.risk_score, _ = compute_risk_score(
                        severity="HIGH", category=sig_cat, confidence=recent.confidence,
                        verdict=recent.verdict.value if recent.verdict else None,
                        correlated_signals=corr,
                    )
                if recent.status in (IncidentStatus.NEW, IncidentStatus.ACK):
                    recent.status = IncidentStatus.ESCALATED
                session.add(
                    IncidentStatusLog(
                        incident_id=recent.id, to_status="escalated",
                        note=f"Fused with access-control signal ({payload.event_type})",
                    )
                )
                elevated = True
        session.add(
            AuditLogEntry(
                action="access.ingested",
                resource_type="access_event",
                resource_id=str(ae.id),
                details={
                    "event_type": payload.event_type, "bound_incident": bound,
                    "elevated": elevated,
                },
            )
        )
        await session.commit()
        aid = ae.id
        new_score = recent.risk_score if recent is not None else None
    # correlate with live video in the context engine to fire composite signals
    # (invalid badge + loitering/tailgating, verified forced door)
    composite = None
    if camera_id:
        with contextlib.suppress(Exception):
            import httpx

            async with httpx.AsyncClient(timeout=4.0) as c:
                r = await c.post(
                    "http://localhost:8040/access-event",
                    json={
                        "camera_id": str(camera_id),
                        "event_type": payload.event_type,
                        "door_id": payload.door_id,
                        "badge_id": payload.badge_id,
                    },
                )
                if r.status_code == 200:
                    composite = r.json().get("fired")
    return {
        "id": str(aid), "event_type": payload.event_type, "bound_incident": bound,
        "fused": bound is not None, "elevated": elevated, "incident_risk_score": new_score,
        "composite_signal": composite,
    }


@app.get("/access-events")
async def list_access_events(limit: int = Query(50, le=200)) -> list[dict]:
    async with async_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(AccessEvent).order_by(AccessEvent.ts.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": str(a.id),
            "event_type": a.event_type.value,
            "door_id": a.door_id,
            "badge_id": a.badge_id,
            "camera_id": str(a.camera_id) if a.camera_id else None,
            "incident_id": str(a.incident_id) if a.incident_id else None,
            "ts": a.ts.isoformat(),
        }
        for a in rows
    ]


@app.get("/fusion/timeline")
async def fusion_timeline(minutes: int = Query(30, le=1440)) -> dict:
    """Two-lane signals view: access-control events and video incidents over a
    window, plus the fusions (access event bound to a video incident). This shows
    signals intelligence at work: an alarm + video = a verified, elevated threat."""
    _THREATENING = {"door_forced", "door_held", "access_denied", "zone_trip"}
    since = datetime.now(UTC) - timedelta(minutes=minutes)
    async with async_session_factory() as session:
        cam_names = {
            str(cid): name for cid, name in (await session.execute(select(Camera.id, Camera.name))).all()
        }
        access = (
            await session.execute(
                select(AccessEvent).where(AccessEvent.ts >= since).order_by(AccessEvent.ts.desc())
            )
        ).scalars().all()
        rows = (
            await session.execute(
                select(Incident, Signature.name)
                .join(Signature, Incident.signature_id == Signature.id, isouter=True)
                .where(Incident.created_at >= since)
                .order_by(Incident.created_at.desc())
                .limit(200)
            )
        ).all()

    access_events = [
        {
            "id": str(a.id),
            "ts": a.ts.isoformat(),
            "event_type": a.event_type.value,
            "threatening": a.event_type.value in _THREATENING,
            "door_id": a.door_id,
            "badge_id": a.badge_id,
            "camera": cam_names.get(str(a.camera_id)) if a.camera_id else None,
            "bound_incident": str(a.incident_id) if a.incident_id else None,
        }
        for a in access
    ]
    video_incidents = [
        {
            "id": str(inc.id),
            "ts": inc.created_at.isoformat(),
            "signature": sig_name,
            "severity": inc.severity.value,
            "status": inc.status.value,
            "risk_score": inc.risk_score,
            "camera": cam_names.get(str(inc.camera_id)) if inc.camera_id else None,
            "fused": bool((inc.attributes or {}).get("fused_access")),
        }
        for inc, sig_name in rows
    ]
    fusions = [
        {
            "access_event_id": a["id"],
            "incident_id": a["bound_incident"],
            "ts": a["ts"],
            "event_type": a["event_type"],
            "camera": a["camera"],
        }
        for a in access_events
        if a["bound_incident"]
    ]
    return {
        "window_minutes": minutes,
        "access_events": access_events,
        "video_incidents": video_incidents,
        "fusions": fusions,
        "counts": {
            "access": len(access_events),
            "video": len(video_incidents),
            "fused": len(fusions),
        },
    }
