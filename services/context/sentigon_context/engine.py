"""Stateful context engine: consume perception objects, maintain track/zone windows,
evaluate behavior signatures, emit candidate events + persist Event/Incident rows.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
from sentigon_common.db import async_session_factory
from sentigon_common.db.models import (
    Camera,
    Event,
    Incident,
    IncidentStatusLog,
    ScheduleWindow,
    Signature,
    WatchlistEntry,
    Zone,
    ZoneSnapshot,
)
from sentigon_common.kafka import BusProducer
from sentigon_common.logging import get_logger, set_correlation_id
from sentigon_common.risk import compute_risk_score
from sentigon_common.schemas.bus import CandidateEventMsg, Topics
from sentigon_common.schemas.enums import IncidentStatus, Severity
from sqlalchemy import func, select, update

from .config import settings

log = get_logger("context.engine")


def _apply_homography(h: list, x: float, y: float) -> tuple[float, float] | None:
    """Map an image pixel (x, y) to ground-plane metres via a 3x3 homography."""
    d = h[2][0] * x + h[2][1] * y + h[2][2]
    if abs(d) < 1e-9:
        return None
    return (
        (h[0][0] * x + h[0][1] * y + h[0][2]) / d,
        (h[1][0] * x + h[1][1] * y + h[1][2]) / d,
    )


# ── state ─────────────────────────────────────────────────────


@dataclass
class TrackState:
    track_id: int
    object_class: str
    first_seen: float
    last_seen: float
    positions: deque = field(default_factory=lambda: deque(maxlen=30))
    zones: set[str] = field(default_factory=set)
    zone_enter: dict[str, float] = field(default_factory=dict)
    last_bbox: list[float] = field(default_factory=list)

    def speed(self) -> float:
        if len(self.positions) < 2:
            return 0.0
        t0, x0, y0 = self.positions[0]
        t1, x1, y1 = self.positions[-1]
        dt = t1 - t0
        return math.hypot(x1 - x0, y1 - y0) / dt if dt > 0 else 0.0

    def real_speed(self, homography: list, fw: int, fh: int) -> float:
        """Robust ground-plane speed in metres/second via the camera homography.
        Uses the MEDIAN of per-step speeds over the recent window, so a single
        track jump / ID switch (which would otherwise inflate a first-to-last
        estimate, especially in the far field where the homography amplifies
        pixel noise) does not produce a spurious high speed."""
        if len(self.positions) < 4 or not homography:
            return 0.0
        recent = list(self.positions)[-8:]
        steps: list[float] = []
        for (ta, xa, ya), (tb, xb, yb) in zip(recent, recent[1:], strict=False):
            dt = tb - ta
            # only measure within the calibrated ground region (below the far edge
            # of the calibration trapezoid); the far field extrapolates the
            # homography and is unreliable
            if dt <= 0 or ya < 0.34 or yb < 0.34:
                continue
            wa = _apply_homography(homography, xa * fw, ya * fh)
            wb = _apply_homography(homography, xb * fw, yb * fh)
            if wa is None or wb is None:
                continue
            steps.append(math.hypot(wb[0] - wa[0], wb[1] - wa[1]) / dt)
        if len(steps) < 2:
            return 0.0
        steps.sort()
        return steps[len(steps) // 2]  # median


@dataclass
class ZoneState:
    zone_id: str
    occupants: set[int] = field(default_factory=set)
    entries: deque = field(default_factory=lambda: deque(maxlen=64))  # (ts, track_id, cls)
    coverage: float = 0.0  # latest seg mask-area coverage of the zone (0..1)
    peak_occupancy: int = 0
    dwell_sum: float = 0.0  # completed dwell seconds
    dwell_count: int = 0


@dataclass
class CameraState:
    camera_id: uuid.UUID
    tracks: dict[int, TrackState] = field(default_factory=dict)
    zones: dict[str, ZoneState] = field(default_factory=dict)
    fw: int = 1280
    fh: int = 720


@dataclass
class Candidate:
    signature_name: str
    event_type: str
    title: str
    zone_id: str | None
    scope: str
    confidence: float
    object_refs: dict
    context: dict


# ── signature registry (hot-reload) ───────────────────────────


class SignatureRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, dict] = {}
        self._loaded = 0.0

    async def refresh(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._loaded < settings.meta_refresh_seconds:
            return
        async with async_session_factory() as session:
            rows = (
                (await session.execute(select(Signature).where(Signature.enabled.is_(True))))
                .scalars()
                .all()
            )
        self._by_name = {
            s.name: {
                "id": s.id,
                "severity": s.severity,
                "cooldown": float(s.cooldown_seconds),
                "params": s.params or {},
                "category": s.category,
            }
            for s in rows
        }
        self._loaded = time.monotonic()

    def get(self, name: str) -> dict | None:
        return self._by_name.get(name)


# ── snapshot client (pull a frame from ingest's ring buffer) ──


class Snapshotter:
    async def snapshot(self, camera_id: uuid.UUID) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                r = await client.post(f"{settings.ingest_url}/cameras/{camera_id}/snapshot")
                if r.status_code == 200:
                    return r.json().get("ref")
        except Exception:  # noqa: BLE001
            return None
        return None


# ── engine ────────────────────────────────────────────────────


class ContextEngine:
    def __init__(self) -> None:
        self.producer = BusProducer("context")
        self.registry = SignatureRegistry()
        self.snap = Snapshotter()
        self.store: dict[uuid.UUID, CameraState] = {}
        self.zone_meta: dict[str, dict] = {}
        self.cam_homography: dict[str, list] = {}  # camera_id -> 3x3 image->ground homography
        self.plate_watch: dict[str, dict] = {}  # plate_hash -> {id, label, reason}
        self.schedules: list[dict] = []  # roster/delivery windows that suppress expected activity
        self.handoff_fires: dict[tuple, float] = {}  # (from_track, to_camera) -> last fire
        self._reid_client = None  # lazy Qdrant client for cross-camera handoff
        # learned per-(zone, hour-of-day) activity baselines for anomaly detection
        self.baselines: dict[tuple, dict] = {}
        self._baselines_loaded = 0.0
        self.anomaly_fires: dict[str, float] = {}
        self.cooldowns: dict[tuple, float] = {}
        self._meta_loaded = 0.0
        self._last_state_prune = 0.0
        self.stats = {"events": 0, "incidents": 0, "frames": 0}

    async def start(self) -> None:
        await self.producer.start()
        await self._load_zones(force=True)
        await self.registry.refresh(force=True)
        log.info(
            "context.started", signatures=len(self.registry._by_name), zones=len(self.zone_meta)
        )

    async def stop(self) -> None:
        await self.producer.stop()

    async def _load_zones(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._meta_loaded < settings.meta_refresh_seconds:
            return
        async with async_session_factory() as session:
            zones = (await session.execute(select(Zone))).scalars().all()
            cam_rows = (
                await session.execute(select(Camera.id, Camera.name, Camera.homography))
            ).all()
            cams = [(cid, h) for cid, _n, h in cam_rows]
            self._cam_names = {str(cid): name for cid, name, _h in cam_rows}
            plates = (
                await session.execute(
                    select(WatchlistEntry).where(
                        WatchlistEntry.category == "plate", WatchlistEntry.active.is_(True)
                    )
                )
            ).scalars().all()
        self.plate_watch = {
            p.embedding_ref: {"id": str(p.id), "label": p.label, "reason": p.reason}
            for p in plates
        }
        async with async_session_factory() as session:
            scheds = (
                await session.execute(
                    select(ScheduleWindow).where(ScheduleWindow.active.is_(True))
                )
            ).scalars().all()
        self.schedules = [
            {
                "id": str(s.id),
                "name": s.name,
                "camera_id": str(s.camera_id) if s.camera_id else None,
                "zone_id": str(s.zone_id) if s.zone_id else None,
                "signatures": s.signatures or [],
                "days": s.days_of_week or [],
                "start": s.start_minute,
                "end": s.end_minute,
            }
            for s in scheds
        ]
        self.cam_homography = {
            str(cid): (h or {}).get("matrix")
            for cid, h in cams
            if h and (h or {}).get("matrix")
        }
        self.zone_meta = {
            str(z.id): {
                "name": z.name,
                "type": z.zone_type.value,
                "camera_id": z.camera_id,
                "max_occupancy": z.max_occupancy,
                # Tailgating only makes sense at an access-controlled door.
                "access_controlled": bool((z.meta or {}).get("access_controlled", False)),
                "polygon": (z.polygon_image or {}).get("points"),
            }
            for z in zones
        }
        self._meta_loaded = time.monotonic()

    # main entry: one perception.objects message
    async def handle(self, payload: dict, correlation_id: str | None) -> None:
        await self._load_zones()
        await self.registry.refresh()
        try:
            camera_id = uuid.UUID(payload["camera_id"])
            now = datetime.fromisoformat(payload["frame_ts"]).timestamp()
        except (KeyError, ValueError):
            return
        fw = payload.get("frame_width", 1280)
        fh = payload.get("frame_height", 720)
        cam = self.store.setdefault(camera_id, CameraState(camera_id))
        cam.fw, cam.fh = fw, fh
        self.stats["frames"] += 1

        newly_entered: list[tuple[TrackState, str]] = []
        for o in payload.get("objects", []):
            tid = o.get("track_id", -1)
            if tid < 0:
                continue
            cls = o["object_class"]
            st = cam.tracks.get(tid)
            if st is None:
                st = TrackState(tid, cls, now, now)
                cam.tracks[tid] = st
            st.last_seen = now
            st.object_class = cls
            st.last_bbox = o["bbox"]
            x, y, w, h = o["bbox"]
            st.positions.append((now, (x + w / 2) / fw, (y + h / 2) / fh))
            new_zones = set(o.get("zone_hits", []))
            for z in new_zones - st.zones:
                st.zone_enter[z] = now
                zs = cam.zones.setdefault(z, ZoneState(z))
                zs.occupants.add(tid)
                zs.entries.append((now, tid, cls))
                newly_entered.append((st, z))
            for z in st.zones - new_zones:
                if z in cam.zones:
                    zs = cam.zones[z]
                    zs.occupants.discard(tid)
                    # record completed dwell for zone analytics
                    entered = st.zone_enter.get(z)
                    if entered is not None:
                        zs.dwell_sum += now - entered
                        zs.dwell_count += 1
                st.zone_enter.pop(z, None)
            st.zones = new_zones

        pruned = self._prune(cam, now)
        self._prune_state(now)
        if settings.handoff_enabled and pruned:
            await self._check_handoffs(camera_id, pruned, now)
        if settings.zone_analytics_enabled:
            self._update_zone_coverage(cam, payload.get("objects", []))
            await self._snapshot_zones(camera_id, cam, now)
        if settings.anomaly_enabled:
            await self._refresh_baselines(now)
            candidates_anom = self._check_anomalies(camera_id, cam, now)
        else:
            candidates_anom = []

        candidates: list[Candidate] = list(candidates_anom)
        if self.plate_watch:
            candidates += self._eval_plate_watch(payload.get("objects", []))
        candidates += self._eval_intrusion(newly_entered, now)
        candidates += self._eval_tailgating(cam, now)
        candidates += self._eval_loitering(cam, now)
        candidates += self._eval_crowd(cam, now)
        candidates += self._eval_speed(cam, now)
        candidates += self._eval_abandoned(cam, now)

        for c in candidates:
            await self._fire(camera_id, c, now)

    def _eval_plate_watch(self, objects: list[dict]) -> list[Candidate]:
        """Fire when a live plate read matches a watchlisted plate hash."""
        out: list[Candidate] = []
        for o in objects:
            a = o.get("attributes", {})
            ph = a.get("plate_hash")
            entry = self.plate_watch.get(ph) if ph else None
            if entry is None:
                continue
            ctx = {
                "watchlist_id": entry["id"],
                "label": entry["label"],
                "watch_reason": entry.get("reason"),
                "plate_conf": a.get("plate_conf"),
                "method": "anpr",
            }
            if a.get("plate"):
                ctx["plate"] = a["plate"]
            out.append(
                Candidate(
                    signature_name="Plate Watchlist Hit",
                    event_type="watchlist.plate",
                    title=f"Plate watchlist hit: {entry['label']}",
                    zone_id=None,
                    scope=f"plate:{entry['id']}",
                    confidence=float(a.get("plate_conf") or 0.8),
                    object_refs={"track_id": o.get("track_id")},
                    context=ctx,
                )
            )
        return out

    async def _refresh_baselines(self, now: float) -> None:
        """Learn each zone's normal occupancy from its snapshot history (mean + std),
        over a trailing window that EXCLUDES the most recent snapshots so a sustained
        anomaly is not absorbed into 'normal'. This is the baseline the live scene is
        compared against (deviation = anomaly)."""
        if now - self._baselines_loaded < settings.anomaly_baseline_refresh_s:
            return
        cutoff = datetime.fromtimestamp(now - settings.anomaly_baseline_exclude_recent_s)
        window_start = datetime.fromtimestamp(now - settings.anomaly_baseline_window_s)
        async with async_session_factory() as session:
            rows = (
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
        self.baselines = {
            str(zid): {"mean": float(mean), "std": float(std), "n": int(n)}
            for zid, mean, std, n in rows
        }
        self._baselines_loaded = now
        log.info("context.baselines_loaded", zones=len(self.baselines))

    def _check_anomalies(self, camera_id: uuid.UUID, cam: CameraState, now: float) -> list[Candidate]:
        """Fire Anomalous Activity when a zone's live occupancy is far above its
        learned baseline for this hour (e.g. a crowd in a normally-empty area)."""
        out: list[Candidate] = []
        for zid, zs in cam.zones.items():
            bl = self.baselines.get(zid)
            if not bl or bl["n"] < settings.anomaly_min_samples:
                continue
            occ = len(zs.occupants)
            std = max(bl["std"], 0.5)  # floor avoids div-by-zero on always-quiet zones
            z = (occ - bl["mean"]) / std
            if z >= settings.anomaly_sigma and (occ - bl["mean"]) >= settings.anomaly_min_occ_delta:
                key = f"{zid}"
                if now - self.anomaly_fires.get(key, -1e12) < settings.anomaly_cooldown_s:
                    continue
                self.anomaly_fires[key] = now
                zname = (self.zone_meta.get(zid) or {}).get("name", zid[:8])
                out.append(
                    Candidate(
                        signature_name="Anomalous Activity",
                        event_type="anomaly.occupancy",
                        title=f"Anomalous activity in {zname}: {occ} present (normal {bl['mean']:.1f})",
                        zone_id=zid,
                        scope=f"anomaly:{zid}",
                        confidence=round(min(1.0, z / (settings.anomaly_sigma * 2)), 3),
                        object_refs={"zone_id": zid},
                        context={
                            "zone": zname,
                            "observed_occupancy": occ,
                            "baseline_mean": round(bl["mean"], 2),
                            "baseline_std": round(bl["std"], 2),
                            "z_score": round(z, 2),
                            "baseline_samples": bl["n"],
                            "method": "learned-baseline",
                        },
                    )
                )
        return out

    def _update_zone_coverage(self, cam: CameraState, objects: list[dict]) -> None:
        """Segmentation-based density: the fraction of each zone polygon covered by
        the union of object masks. More faithful than counting boxes, since it uses
        the actual silhouettes and de-overlaps them."""
        from shapely.geometry import Polygon
        from shapely.ops import unary_union

        # gather masks per zone (normalized polygons already in 0..1 image space)
        by_zone: dict[str, list] = {}
        for o in objects:
            mask = o.get("mask")
            if not mask or len(mask) < 3:
                continue
            for z in o.get("zone_hits", []):
                by_zone.setdefault(z, []).append(mask)
        for zid, zs in cam.zones.items():
            zs.peak_occupancy = max(zs.peak_occupancy, len(zs.occupants))
            zmeta = self.zone_meta.get(zid, {})
            zpoly_pts = zmeta.get("polygon")
            masks = by_zone.get(zid)
            if not zpoly_pts or len(zpoly_pts) < 3 or not masks:
                zs.coverage = 0.0
                continue
            try:
                zpoly = Polygon(zpoly_pts)
                if zpoly.area <= 0:
                    zs.coverage = 0.0
                    continue
                polys = [Polygon(m).buffer(0) for m in masks if len(m) >= 3]
                merged = unary_union(polys)
                inter = merged.intersection(zpoly).area
                zs.coverage = round(min(1.0, inter / zpoly.area), 4)
            except Exception:  # noqa: BLE001
                zs.coverage = 0.0

    async def _snapshot_zones(self, camera_id: uuid.UUID, cam: CameraState, now: float) -> None:
        """Persist a per-zone metric sample at the configured interval."""
        last = getattr(cam, "_last_zone_snap", 0.0)
        if now - last < settings.zone_snapshot_interval_s:
            return
        cam._last_zone_snap = now  # type: ignore[attr-defined]
        ts = datetime.fromtimestamp(now, tz=UTC)
        rows = []
        for zid, zs in cam.zones.items():
            avg_dwell = zs.dwell_sum / zs.dwell_count if zs.dwell_count else 0.0
            rows.append(
                ZoneSnapshot(
                    zone_id=uuid.UUID(zid),
                    camera_id=camera_id,
                    ts=ts,
                    occupancy=len(zs.occupants),
                    mask_coverage=zs.coverage,
                    avg_dwell_s=round(avg_dwell, 2),
                )
            )
        if not rows:
            return
        async with async_session_factory() as session:
            session.add_all(rows)
            await session.commit()

    async def _check_handoffs(
        self, from_camera: uuid.UUID, pruned: list[TrackState], now: float
    ) -> None:
        """For each person that just left this camera, see if the same appearance
        (OSNet ReID) was seen on another camera within the window. If so, fire a
        Cross-Camera Handoff linking the two, so a person is followed across the
        estate in real time."""
        for st in pruned:
            if st.object_class != "person":
                continue
            hit = await asyncio.to_thread(self._reid_handoff_query, str(from_camera), st.track_id, now)
            if hit is None:
                continue
            to_camera, to_track, score, gap = hit
            key = (str(from_camera), st.track_id, to_camera)
            if now - self.handoff_fires.get(key, -1e12) < settings.handoff_cooldown_s:
                continue
            self.handoff_fires[key] = now
            to_name = self.zone_meta_cam_name(to_camera)
            cand = Candidate(
                signature_name="Cross-Camera Handoff",
                event_type="reid.handoff",
                title=f"Person handoff to {to_name}",
                zone_id=None,
                scope=f"handoff:{st.track_id}:{to_camera}",
                confidence=round(min(score, 1.0), 3),
                object_refs={"from_track": st.track_id, "to_track": to_track},
                context={
                    "from_camera": str(from_camera),
                    "to_camera": to_camera,
                    "to_camera_name": to_name,
                    "match_score": round(score, 4),
                    "seconds_apart": round(gap, 1),
                    "method": "osnet-reid",
                },
            )
            await self._fire(from_camera, cand, now)

    def zone_meta_cam_name(self, camera_id: str) -> str:
        return self._cam_names.get(camera_id, camera_id[:8]) if hasattr(self, "_cam_names") else camera_id[:8]

    def _reid_handoff_query(self, from_camera: str, track_id: int, now: float):
        """Blocking Qdrant work: centroid of the just-ended track, then nearest
        appearances on OTHER cameras. Returns (to_camera, to_track, score, gap_s)
        or None."""
        from qdrant_client import models as qm

        if self._reid_client is None:
            from qdrant_client import QdrantClient
            from sentigon_common.config import settings as common
            self._reid_client = QdrantClient(url=common.qdrant_url)
        col = settings.reid_collection
        flt = qm.Filter(must=[
            qm.FieldCondition(key="camera_id", match=qm.MatchValue(value=from_camera)),
            qm.FieldCondition(key="track_id", match=qm.MatchValue(value=track_id)),
        ])
        vecs: list[list[float]] = []
        offset = None
        while True:
            pts, offset = self._reid_client.scroll(
                col, scroll_filter=flt, limit=128, offset=offset, with_vectors=True
            )
            vecs += [list(p.vector) for p in pts if p.vector is not None]
            if offset is None or not pts:
                break
        if len(vecs) < settings.handoff_min_appearances:
            return None
        dim = len(vecs[0])
        centroid = [sum(v[i] for v in vecs) / len(vecs) for i in range(dim)]
        hits = self._reid_client.query_points(
            col, query=centroid, limit=30, with_payload=True,
            query_filter=qm.Filter(
                must_not=[qm.FieldCondition(key="camera_id", match=qm.MatchValue(value=from_camera))]
            ),
        ).points
        for h in hits:
            if float(h.score) < settings.handoff_threshold:
                break  # sorted descending
            pl = h.payload or {}
            ts = pl.get("ts")
            if not ts:
                continue
            gap = now - datetime.fromisoformat(ts).timestamp()
            if -settings.handoff_window_s <= gap <= settings.handoff_window_s:
                return pl.get("camera_id"), pl.get("track_id"), float(h.score), abs(gap)
        return None

    def _prune_state(self, now: float) -> None:
        """Evict stale cooldown / handoff-fire entries so these maps do not grow
        unbounded on busy cameras with high track-id churn (TrackState is already
        pruned; these were not). Throttled to once a minute; horizons are far larger
        than any real cooldown so an active cooldown is never dropped."""
        if now - self._last_state_prune < 60.0:
            return
        self._last_state_prune = now
        self.cooldowns = {k: t for k, t in self.cooldowns.items() if now - t < 3600.0}
        self.handoff_fires = {
            k: t for k, t in self.handoff_fires.items() if now - t < settings.handoff_cooldown_s * 8
        }
        self.anomaly_fires = {
            k: t for k, t in self.anomaly_fires.items() if now - t < settings.anomaly_cooldown_s * 8
        }

    def _prune(self, cam: CameraState, now: float) -> list[TrackState]:
        """Drop tracks unseen past the stale window. Returns the pruned tracks so
        a person leaving frame can be correlated to another camera (handoff)."""
        pruned: list[TrackState] = []
        for tid in list(cam.tracks):
            if now - cam.tracks[tid].last_seen > settings.track_stale_seconds:
                st = cam.tracks.pop(tid)
                for z in st.zones:
                    if z in cam.zones:
                        cam.zones[z].occupants.discard(tid)
                pruned.append(st)
        return pruned

    # ── evaluators (pure over state) ─────────────────────────
    def _threshold(self, name: str, keys: str | list[str], default: float) -> float:
        """Read a tuning threshold from a signature's conditions. Accepts key
        aliases so the governed ontology's names (e.g. dwell_time_min,
        person_count_min) are honored, not silently ignored in favour of a
        hardcoded default."""
        sig = self.registry.get(name)
        cond = (sig or {}).get("params", {}).get("conditions") or {}
        for key in [keys] if isinstance(keys, str) else keys:
            if key in cond and cond[key] is not None:
                try:
                    val = float(cond[key])
                except (TypeError, ValueError):
                    continue
                # non-positive thresholds are a misconfiguration and are used as
                # `x / (thr*2)` confidence denominators — fall back to the (positive)
                # default rather than dividing by zero and crashing frame processing.
                if val > 0:
                    return val
        return default

    def _eval_intrusion(self, entered: list[tuple[TrackState, str]], now: float) -> list[Candidate]:
        out = []
        for st, zid in entered:
            zm = self.zone_meta.get(zid)
            if not zm or now - st.first_seen < settings.min_track_age_seconds:
                continue
            ztype = zm["type"]
            is_vehicle = st.object_class in settings.vehicle_classes
            if ztype == "perimeter":
                name = "Perimeter Breach"
            elif ztype in ("restricted", "exclusion"):
                name = "Vehicle in Restricted Zone" if is_vehicle else "Unauthorized Entry"
            else:
                continue
            out.append(
                Candidate(
                    name,
                    "zone_entry",
                    f"{st.object_class} entered {zm['name']}",
                    zid,
                    f"{st.track_id}:{zid}",
                    0.85,
                    {"track_ids": [st.track_id], "bbox": st.last_bbox},
                    {"zone": zm["name"], "zone_type": ztype, "object_class": st.object_class},
                )
            )
        return out

    def _eval_tailgating(self, cam: CameraState, now: float) -> list[Candidate]:
        out = []
        window = self._threshold("Tailgating", "window_seconds", settings.tailgate_window_seconds)
        need = int(self._threshold("Tailgating", "min_count", settings.tailgate_min_count))
        for zid, zs in cam.zones.items():
            zm = self.zone_meta.get(zid)
            # Tailgating is an access-control violation: it requires a controlled
            # door. Without one, two people entering together is not a threat
            # (the VLM rejected 100% of such firings), so do not raise it.
            if not zm or zm["type"] != "entry" or not zm.get("access_controlled"):
                continue
            recent = {t for (ts, t, cls) in zs.entries if now - ts <= window and cls == "person"}
            if len(recent) >= need:
                out.append(
                    Candidate(
                        "Tailgating",
                        "tailgating",
                        f"{len(recent)} people entered {zm['name']} together",
                        zid,
                        zid,
                        0.8,
                        {"track_ids": list(recent)},
                        {"zone": zm["name"], "count": len(recent), "window_s": window},
                    )
                )
        return out

    def _eval_loitering(self, cam: CameraState, now: float) -> list[Candidate]:
        out = []
        thr = self._threshold(
            "Loitering", ["dwell_seconds", "dwell_time_min"], settings.loiter_seconds
        )
        for st in cam.tracks.values():
            for zid, enter in st.zone_enter.items():
                dwell = now - enter
                if dwell >= thr:
                    zm = self.zone_meta.get(zid, {})
                    out.append(
                        Candidate(
                            "Loitering",
                            "loitering",
                            f"{st.object_class} loitering in {zm.get('name', 'zone')} ({int(dwell)}s)",
                            zid,
                            f"{st.track_id}:{zid}",
                            min(1.0, dwell / (thr * 2)),
                            {"track_ids": [st.track_id], "bbox": st.last_bbox},
                            {"dwell_s": round(dwell, 1), "zone": zm.get("name")},
                        )
                    )
        return out

    def _eval_crowd(self, cam: CameraState, now: float) -> list[Candidate]:
        out = []
        thr = int(
            self._threshold(
                "Crowd Formation", ["occupancy", "person_count_min"], settings.crowd_threshold
            )
        )
        for zid, zs in cam.zones.items():
            occ = len(zs.occupants)
            zm = self.zone_meta.get(zid, {})
            cap = zm.get("max_occupancy")
            if cap and occ > cap:
                out.append(
                    Candidate(
                        "Occupancy Exceeded",
                        "occupancy",
                        f"{zm.get('name', 'zone')} occupancy {occ} exceeds {cap}",
                        zid,
                        zid,
                        min(1.0, occ / (cap * 1.5)),
                        {"track_ids": list(zs.occupants)},
                        {"occupancy": occ, "max": cap, "zone": zm.get("name")},
                    )
                )
            elif occ >= thr:
                out.append(
                    Candidate(
                        "Crowd Formation",
                        "crowd",
                        f"Crowd of {occ} forming in {zm.get('name', 'zone')}",
                        zid,
                        zid,
                        min(1.0, occ / (thr * 2)),
                        {"track_ids": list(zs.occupants)},
                        {"occupancy": occ, "threshold": thr, "zone": zm.get("name")},
                    )
                )
        return out

    def _eval_speed(self, cam: CameraState, now: float) -> list[Candidate]:
        out = []
        homography = self.cam_homography.get(str(cam.camera_id))
        veh_kmh = self._threshold("Speeding Vehicle", "speed_kmh", settings.vehicle_speed_kmh)
        run_ms = self._threshold("Running/Fleeing", "speed_ms", settings.run_speed_ms)
        for st in cam.tracks.values():
            if now - st.first_seen <= settings.min_track_age_seconds:
                continue
            is_vehicle = st.object_class in settings.vehicle_classes
            if homography:
                mps = st.real_speed(homography, cam.fw, cam.fh)
                # plausibility guard: reject readings above physical limits as
                # calibration/tracking artifacts rather than firing on them
                max_mps = 70.0 if is_vehicle else 12.0
                if mps > max_mps:
                    continue
                kmh = mps * 3.6
                if is_vehicle and kmh > veh_kmh:
                    out.append(
                        Candidate(
                            "Speeding Vehicle", "speeding",
                            f"{st.object_class} speeding ({kmh:.1f} km/h)", None, str(st.track_id),
                            min(1.0, kmh / (veh_kmh * 2)),
                            {"track_ids": [st.track_id], "bbox": st.last_bbox},
                            {"speed_kmh": round(kmh, 1), "speed_ms": round(mps, 2), "threshold_kmh": veh_kmh, "calibrated": True},
                        )
                    )
                elif st.object_class == "person" and mps > run_ms:
                    out.append(
                        Candidate(
                            "Running/Fleeing", "running",
                            f"person running ({mps:.1f} m/s)", None, str(st.track_id),
                            min(1.0, mps / (run_ms * 2)),
                            {"track_ids": [st.track_id], "bbox": st.last_bbox},
                            {"speed_ms": round(mps, 2), "threshold_ms": run_ms, "calibrated": True},
                        )
                    )
            elif is_vehicle:
                sp = st.speed()
                vthr = self._threshold("Speeding Vehicle", "speed", settings.vehicle_speed_threshold)
                if sp > vthr:
                    out.append(
                        Candidate(
                            "Speeding Vehicle", "speeding",
                            f"{st.object_class} moving fast ({sp:.2f}/s)", None, str(st.track_id),
                            min(1.0, sp / (vthr * 2)),
                            {"track_ids": [st.track_id], "bbox": st.last_bbox},
                            {"speed_px_s": round(sp, 3), "calibrated": False},
                        )
                    )
        return out

    def _eval_abandoned(self, cam: CameraState, now: float) -> list[Candidate]:
        out = []
        thr = self._threshold(
            "Suspicious Package", "stationary_seconds", settings.abandoned_seconds
        )
        for st in cam.tracks.values():
            if st.object_class not in settings.abandoned_classes:
                continue
            if now - st.first_seen >= thr and st.speed() < settings.abandoned_stationary_speed:
                out.append(
                    Candidate(
                        "Suspicious Package",
                        "abandoned_object",
                        f"Stationary {st.object_class} for {int(now - st.first_seen)}s",
                        next(iter(st.zones), None),
                        str(st.track_id),
                        0.7,
                        {"track_ids": [st.track_id], "bbox": st.last_bbox},
                        {"stationary_s": round(now - st.first_seen, 1)},
                    )
                )
        return out

    async def _group_into_open(
        self, sig_id, camera_id: uuid.UUID, zone_uuid, now: float
    ) -> bool:
        """If an open, recent incident with the same signature+camera+zone exists,
        bump its occurrence_count + last_seen and return True (this detection was
        grouped). Otherwise return False (caller creates a fresh incident)."""
        since = datetime.fromtimestamp(now - settings.dedup_window_s)
        nowdt = datetime.fromtimestamp(now, tz=UTC)
        open_states = [IncidentStatus.NEW, IncidentStatus.ACK, IncidentStatus.ESCALATED]
        zone_pred = Incident.zone_id.is_(None) if zone_uuid is None else Incident.zone_id == zone_uuid
        async with async_session_factory() as session:
            existing = (
                await session.execute(
                    select(Incident)
                    .where(
                        Incident.signature_id == sig_id,
                        Incident.camera_id == camera_id,
                        zone_pred,
                        Incident.status.in_(open_states),
                        func.coalesce(Incident.last_seen_at, Incident.created_at) >= since,
                    )
                    .order_by(Incident.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is None:
                return False
            existing.occurrence_count += 1
            existing.last_seen_at = nowdt
            await session.commit()
        return True

    async def handle_fall(self, payload: dict) -> dict:
        """Fire a Person Fall incident from a pose-based fall reported by perception."""
        try:
            camera_id = uuid.UUID(payload["camera_id"])
        except (KeyError, ValueError, TypeError):
            return {"fired": False}
        now = time.time()
        bbox = payload.get("bbox") or []
        cand = Candidate(
            signature_name="Person Fall",
            event_type="safety.fall",
            title="Person fall detected (pose)",
            zone_id=None,
            scope=f"fall:{camera_id}",
            confidence=0.9,
            object_refs={"bbox": bbox},
            context={"method": "pose-fall", "detector": "yolo-pose"},
        )
        await self._fire(camera_id, cand, now)
        return {"fired": True}

    async def handle_access(self, payload: dict) -> dict:
        """Correlate an access-control event with the LIVE video state on its camera
        to fire composite signals (Ambient-style): an invalid badge while a person
        loiters, an invalid badge followed by tailgating, or a forced door that video
        confirms. Returns which composite (if any) fired."""
        try:
            camera_id = uuid.UUID(payload["camera_id"])
        except (KeyError, ValueError, TypeError):
            return {"fired": None, "reason": "no camera"}
        event_type = payload.get("event_type", "")
        now = time.time()
        cam = self.store.get(camera_id)
        if cam is None:
            return {"fired": None, "reason": "no live state for camera"}
        # people currently visible on this camera (non-stale person tracks)
        present = [
            t for t in cam.tracks.values()
            if t.object_class == "person" and now - t.last_seen < settings.track_stale_seconds
        ]
        loiter_s = self._threshold("Loitering", ["dwell_time_min", "dwell_seconds"], settings.loiter_seconds)
        dwelling = [t for t in present if now - t.first_seen > loiter_s]

        fired = None
        cand = None
        refs = {"door_id": payload.get("door_id"), "badge_id": payload.get("badge_id"),
                "people_present": len(present)}
        if event_type == "access_denied" and len(present) >= 2:
            fired = "Invalid Badge Followed By Tailgating"
            cand = Candidate(
                fired, "access.tailgating",
                f"Invalid badge then tailgating ({len(present)} people at door {payload.get('door_id')})",
                None, f"acc:{camera_id}", 0.9, refs,
                {"event_type": event_type, "people": len(present), "method": "access+video"},
            )
        elif event_type == "access_denied" and dwelling:
            fired = "Invalid Badge with Loitering"
            cand = Candidate(
                fired, "access.loiter",
                f"Invalid badge with loitering at door {payload.get('door_id')}",
                None, f"acc:{camera_id}", 0.85, refs,
                {"event_type": event_type, "dwell_people": len(dwelling), "method": "access+video"},
            )
        elif event_type == "door_forced" and present:
            fired = "Verified Forced Door"
            cand = Candidate(
                fired, "access.forced",
                f"Forced door confirmed on video ({len(present)} present)",
                None, f"acc:{camera_id}", 0.95, refs,
                {"event_type": event_type, "people": len(present), "method": "access+video"},
            )
        if cand is not None:
            await self._fire(camera_id, cand, now)
        return {"fired": fired, "people_present": len(present), "dwelling": len(dwelling)}

    # ── generic sensor-plane fusion (door/PIR/environmental/panic/...) ──
    @staticmethod
    def _sensor_is_alarm(
        event_type: str, state: str | None, severity: str, raw: dict
    ) -> bool:
        """Decide whether a sensor event is an alarm condition. Policy lives at the
        edge (the sender marks severity / event_type / state / raw.alarm) so ANY
        sensor type works without per-class code here."""
        if severity in ("high", "critical"):
            return True
        if raw.get("alarm") is True:
            return True
        if event_type in ("alarm", "panic", "duress", "glassbreak", "tamper", "threshold", "fault"):
            return True
        return (state or "").lower() in ("forced", "held", "breach", "open_alarm")

    @staticmethod
    def _sensor_signature(device_class: str, has_video: bool) -> tuple[str, str]:
        if device_class == "panic_button":
            return "Panic Alarm", "Panic / duress button activated"
        if device_class == "environmental":
            return "Environmental Alarm", "Environmental sensor threshold breached"
        if has_video:
            return "Verified Sensor Alarm", f"{device_class} alarm corroborated by a person on video"
        return "Sensor Alarm", f"{device_class} reported an alarm condition"

    async def handle_sensor(self, payload: dict, correlation_id: str | None = None) -> dict:
        """Consume a `sensor.events` message and, when it is an alarm condition, fuse
        it with live video on its co-located camera to fire an incident — elevated to
        'Verified' when a person is present. A sensor with no bound camera is persisted
        (by the API) but creates no incident here (an Incident requires a camera)."""
        await self.registry.refresh()
        device_class = payload.get("device_class", "generic")
        event_type = payload.get("event_type", "")
        state = payload.get("state")
        severity = (payload.get("severity") or "").lower()
        raw = payload.get("raw") or {}
        if not self._sensor_is_alarm(event_type, state, severity, raw):
            return {"fired": None, "reason": "not an alarm condition"}
        try:
            camera_id = uuid.UUID(payload["camera_id"]) if payload.get("camera_id") else None
        except (ValueError, TypeError):
            camera_id = None
        if camera_id is None:
            return {"fired": None, "reason": "no camera bound (event persisted only)"}

        now = time.time()
        cam = self.store.get(camera_id)
        present = (
            [
                t
                for t in cam.tracks.values()
                if t.object_class == "person" and now - t.last_seen < settings.track_stale_seconds
            ]
            if cam is not None
            else []
        )
        sig_name, title = self._sensor_signature(device_class, bool(present))
        zone_id = payload.get("zone_id") if isinstance(payload.get("zone_id"), str) else None
        refs = {
            "device_id": payload.get("device_id"),
            "external_id": payload.get("external_id"),
            "device_class": device_class,
            "people_present": len(present),
        }
        cand = Candidate(
            sig_name,
            f"sensor.{device_class}",
            title,
            zone_id,
            f"sensor:{payload.get('device_id')}",
            0.9 if present else 0.75,
            refs,
            {
                "event_type": event_type,
                "state": state,
                "value": payload.get("value"),
                "unit": payload.get("unit"),
                "people": len(present),
                "method": "sensor+video" if present else "sensor",
            },
        )
        await self._fire(camera_id, cand, now)
        return {"fired": sig_name, "people_present": len(present)}

    def _suppressing_schedule(
        self, sig_name: str, camera_id: uuid.UUID, zone_id: str | None, now: float
    ) -> dict | None:
        """Return the first active schedule window that covers this signature at this
        camera/zone right now (local time), or None. Handles overnight windows."""
        if not self.schedules:
            return None
        # windows are authored in site-local time (minutes since local midnight,
        # local weekday), so evaluate them in the site's timezone, not server/UTC.
        try:
            tz = ZoneInfo(settings.schedule_timezone)
        except Exception:  # noqa: BLE001
            tz = UTC
        dt = datetime.fromtimestamp(now, tz=tz)
        minute = dt.hour * 60 + dt.minute
        weekday = dt.weekday()  # 0=Mon..6=Sun
        cam = str(camera_id)
        for s in self.schedules:
            if s["camera_id"] and s["camera_id"] != cam:
                continue
            if s["zone_id"] and s["zone_id"] != zone_id:
                continue
            if s["signatures"] and sig_name not in s["signatures"]:
                continue
            if s["days"] and weekday not in s["days"]:
                continue
            lo, hi = s["start"], s["end"]
            inside = lo <= minute <= hi if lo <= hi else (minute >= lo or minute <= hi)
            if inside:
                return s
        return None

    async def _bump_schedule(self, sched_id: str) -> None:
        async with async_session_factory() as session:
            await session.execute(
                update(ScheduleWindow)
                .where(ScheduleWindow.id == uuid.UUID(sched_id))
                .values(suppressed_count=ScheduleWindow.suppressed_count + 1)
            )
            await session.commit()

    # ── firing: cooldown, snapshot, persist, emit ────────────
    async def _fire(self, camera_id: uuid.UUID, c: Candidate, now: float) -> None:
        sig = self.registry.get(c.signature_name)
        if sig is None:
            # A candidate whose signature name is not seeded would otherwise be
            # dropped silently. Surface it so unseeded/renamed signatures are
            # caught instead of vanishing (see catalog.py runtime signatures).
            log.warning("fire.unknown_signature", signature=c.signature_name)
            return
        key = (camera_id, c.signature_name, c.scope)
        if now - self.cooldowns.get(key, -1e12) < sig["cooldown"]:
            return
        self.cooldowns[key] = now

        # schedule/roster suppression: expected activity (a 2pm dock delivery,
        # nightly cleaning crew) does not alarm during its window.
        sched = self._suppressing_schedule(c.signature_name, camera_id, c.zone_id, now)
        if sched is not None:
            self.stats["suppressed"] = self.stats.get("suppressed", 0) + 1
            await self._bump_schedule(sched["id"])
            log.info(
                "context.schedule_suppressed",
                signature=c.signature_name, schedule=sched["name"], camera=str(camera_id),
            )
            return

        # dedup/grouping: if an equivalent incident (same signature + camera + zone)
        # is still open and recent, roll this detection into it (occurrence_count++)
        # rather than creating a new one. Collapses the flood + spares the VLM.
        zone_uuid = uuid.UUID(c.zone_id) if c.zone_id else None
        if settings.dedup_enabled and await self._group_into_open(
            sig["id"], camera_id, zone_uuid, now
        ):
            self.stats["grouped"] = self.stats.get("grouped", 0) + 1
            return

        correlation_id = uuid.uuid4().hex
        set_correlation_id(correlation_id)
        severity: Severity = sig["severity"]
        snapshot_ref = await self.snap.snapshot(camera_id)
        ts = datetime.fromtimestamp(now, tz=UTC)
        # initial composite threat score (re-scored by reason when the VLM verdict lands)
        zone_type = (self.zone_meta.get(c.zone_id) or {}).get("type") if c.zone_id else None
        risk_score, _ = compute_risk_score(
            severity=severity.value,
            category=sig.get("category"),
            confidence=c.confidence,
            zone_type=zone_type,
        )

        async with async_session_factory() as session:
            event = Event(
                signature_id=sig["id"],
                camera_id=camera_id,
                zone_id=zone_uuid,
                event_type=c.event_type,
                ts=ts,
                severity=severity,
                confidence=c.confidence,
                object_refs=c.object_refs,
                snapshot_ref=snapshot_ref,
                context=c.context,
                correlation_id=correlation_id,
            )
            session.add(event)
            await session.flush()
            incident = Incident(
                event_id=event.id,
                signature_id=sig["id"],
                camera_id=camera_id,
                zone_id=zone_uuid,
                title=c.title,
                severity=severity,
                status=IncidentStatus.NEW,
                confidence=c.confidence,
                risk_score=risk_score,
                attributes=c.context,
                snapshot_ref=snapshot_ref,
                correlation_id=correlation_id,
                last_seen_at=ts,
            )
            session.add(incident)
            await session.flush()
            session.add(
                IncidentStatusLog(incident_id=incident.id, to_status="new", note="auto-generated")
            )
            await session.commit()
            incident_id = incident.id

        await self.producer.publish(
            Topics.EVENTS_CANDIDATE,
            CandidateEventMsg(
                producer="context",
                correlation_id=correlation_id,
                camera_id=camera_id,
                zone_id=zone_uuid,
                signature_name=c.signature_name,
                event_type=c.event_type,
                severity=severity,
                confidence=c.confidence,
                ts=ts,
                object_refs=c.object_refs,
                context=c.context,
                snapshot_ref=snapshot_ref,
            ),
            key=str(camera_id),
        )
        self.stats["events"] += 1
        self.stats["incidents"] += 1
        log.info(
            "context.event",
            signature=c.signature_name,
            camera=str(camera_id),
            incident=str(incident_id),
            severity=severity.value,
        )
