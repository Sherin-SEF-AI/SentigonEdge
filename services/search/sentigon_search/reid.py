"""Cross-camera ReID trajectory reconstruction over the Qdrant appearance index.

The perception service writes one 2048-d ResNet50 appearance embedding per person/
object detection into the `reid` Qdrant collection, tagged with camera_id, track_id,
object_class, and timestamp. This module turns those raw appearances into:

  - a track picker (appearances aggregated by camera + ByteTrack track_id), and
  - a trajectory: given one track, its centroid embedding is searched across ALL
    cameras; matches on OTHER cameras are grouped and scored by cosine similarity,
    producing a time-ordered, cross-camera path with per-hop match confidence.

Single-camera continuity comes from ByteTrack (a stable track_id across frames);
cross-camera links come from appearance similarity (this index). Match scores are
real cosine similarities. Note: identity overlap across cameras depends on the
footage actually containing the same entity on multiple cameras.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client import models as qm
from sentigon_common.config import settings as common_settings


@dataclass
class TrackKey:
    camera_id: str
    track_id: int


class ReidTrajectory:
    def __init__(self, collection: str = "reid") -> None:
        self.client = QdrantClient(url=common_settings.qdrant_url)
        self.collection = collection

    # -- vehicle attribute search ----------------------------------------
    def search_vehicles(self, color: str | None, vtype: str | None, limit: int = 30) -> list[dict]:
        """Attribute search over indexed vehicle sightings (colour + type), the
        backing for natural-language vehicle queries."""
        must = []
        if color:
            must.append(qm.FieldCondition(key="color", match=qm.MatchValue(value=color)))
        if vtype:
            must.append(qm.FieldCondition(key="object_class", match=qm.MatchValue(value=vtype)))
        flt = qm.Filter(must=must) if must else None
        seen: dict[tuple, dict] = {}
        offset = None
        scanned = 0
        while scanned < 20000:
            points, offset = self.client.scroll(
                self.collection, scroll_filter=flt, limit=512, offset=offset, with_payload=True
            )
            for p in points:
                pl = p.payload or {}
                key = (pl.get("camera_id"), pl.get("track_id"))
                if key not in seen:
                    seen[key] = {
                        "camera_id": pl.get("camera_id"),
                        "track_id": pl.get("track_id"),
                        "vehicle_type": pl.get("object_class"),
                        "color": pl.get("color"),
                        "ts": pl.get("ts"),
                    }
            scanned += len(points)
            if not points or offset is None:
                break
        rows = sorted(seen.values(), key=lambda r: r.get("ts") or "", reverse=True)
        return rows[:limit]

    # -- track picker -----------------------------------------------------
    def list_tracks(self, limit: int = 40, min_appearances: int = 4, scan_cap: int = 15000) -> list[dict]:
        agg: dict[tuple[str, int], dict] = {}
        offset = None
        scanned = 0
        while scanned < scan_cap:
            points, offset = self.client.scroll(
                self.collection,
                limit=1000,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            if not points:
                break
            for p in points:
                pl = p.payload or {}
                cam = pl.get("camera_id")
                tid = pl.get("track_id")
                ts = pl.get("ts")
                if cam is None or tid is None:
                    continue
                key = (cam, int(tid))
                a = agg.get(key)
                if a is None:
                    agg[key] = {
                        "camera_id": cam,
                        "track_id": int(tid),
                        "object_class": pl.get("object_class", "object"),
                        "appearances": 1,
                        "first_ts": ts,
                        "last_ts": ts,
                    }
                else:
                    a["appearances"] += 1
                    if ts and (a["first_ts"] is None or ts < a["first_ts"]):
                        a["first_ts"] = ts
                    if ts and (a["last_ts"] is None or ts > a["last_ts"]):
                        a["last_ts"] = ts
            scanned += len(points)
            if offset is None:
                break
        rows = [r for r in agg.values() if r["appearances"] >= min_appearances]
        rows.sort(key=lambda r: r["appearances"], reverse=True)
        return rows[:limit]

    # -- one track's appearances + centroid -------------------------------
    def _track_points(self, camera_id: str, track_id: int) -> tuple[list[dict], list[list[float]]]:
        flt = qm.Filter(
            must=[
                qm.FieldCondition(key="camera_id", match=qm.MatchValue(value=camera_id)),
                qm.FieldCondition(key="track_id", match=qm.MatchValue(value=track_id)),
            ]
        )
        payloads: list[dict] = []
        vectors: list[list[float]] = []
        offset = None
        while True:
            points, offset = self.client.scroll(
                self.collection,
                scroll_filter=flt,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            for p in points:
                payloads.append(p.payload or {})
                if p.vector is not None:
                    vectors.append(list(p.vector))
            if offset is None:
                break
        return payloads, vectors

    @staticmethod
    def _centroid(vectors: list[list[float]]) -> list[float]:
        n = len(vectors)
        dim = len(vectors[0])
        c = [0.0] * dim
        for v in vectors:
            for i in range(dim):
                c[i] += v[i]
        return [x / n for x in c]

    @staticmethod
    def _cohesion(vectors: list[list[float]], centroid: list[float]) -> float:
        # mean cosine of each appearance to the track centroid (single-cam continuity quality)
        import math

        cn = math.sqrt(sum(x * x for x in centroid)) or 1.0
        sims = []
        for v in vectors:
            vn = math.sqrt(sum(x * x for x in v)) or 1.0
            dot = sum(a * b for a, b in zip(v, centroid, strict=False))
            sims.append(dot / (vn * cn))
        return round(statistics.fmean(sims), 4) if sims else 0.0

    # -- cross-camera trajectory -----------------------------------------
    def trajectory(
        self,
        camera_id: str,
        track_id: int,
        other_camera_ids: list[str],
        per_camera: int = 25,
        min_score: float = 0.55,
    ) -> dict:
        payloads, vectors = self._track_points(camera_id, track_id)
        if not vectors:
            return {"found": False, "reason": "no appearances for that track"}

        centroid = self._centroid(vectors)
        cohesion = self._cohesion(vectors, centroid)
        own_ts = sorted(p.get("ts") for p in payloads if p.get("ts"))

        # One filtered nearest-neighbour search per OTHER camera, so a camera with
        # fewer appearances still gets its best candidate (a single global search
        # would be swamped by the busiest camera).
        others: dict[tuple[str, int], dict] = {}
        for cam in other_camera_ids:
            if cam == camera_id:
                continue
            hits = self.client.query_points(
                self.collection,
                query=centroid,
                query_filter=qm.Filter(
                    must=[qm.FieldCondition(key="camera_id", match=qm.MatchValue(value=cam))]
                ),
                limit=per_camera,
                with_payload=True,
            ).points
            for h in hits:
                pl = h.payload or {}
                tid = pl.get("track_id")
                if tid is None:
                    continue
                key = (cam, int(tid))
                g = others.get(key)
                if g is None:
                    others[key] = {
                        "camera_id": cam,
                        "track_id": int(tid),
                        "object_class": pl.get("object_class", "object"),
                        "match_score": round(float(h.score), 4),
                        "matched_ts": pl.get("ts"),
                        "hits": 1,
                    }
                else:
                    g["hits"] += 1
                    if float(h.score) > g["match_score"]:
                        g["match_score"] = round(float(h.score), 4)
                        g["matched_ts"] = pl.get("ts")

        candidates = [c for c in others.values() if c["match_score"] >= min_score]
        candidates.sort(key=lambda c: c["match_score"], reverse=True)

        # timeline: the query track's own span, then each cross-camera match by time
        timeline = [
            {
                "camera_id": camera_id,
                "track_id": track_id,
                "kind": "origin",
                "ts": own_ts[0] if own_ts else None,
                "last_ts": own_ts[-1] if own_ts else None,
                "appearances": len(payloads),
                "match_score": 1.0,
            }
        ]
        for c in candidates:
            timeline.append(
                {
                    "camera_id": c["camera_id"],
                    "track_id": c["track_id"],
                    "kind": "match",
                    "ts": c["matched_ts"],
                    "object_class": c["object_class"],
                    "match_score": c["match_score"],
                    "supporting_hits": c["hits"],
                }
            )
        timeline.sort(key=lambda e: (e.get("ts") or ""))

        return {
            "found": True,
            "query": {
                "camera_id": camera_id,
                "track_id": track_id,
                "object_class": payloads[0].get("object_class", "object"),
                "appearances": len(payloads),
                "continuity_cohesion": cohesion,
                "first_ts": own_ts[0] if own_ts else None,
                "last_ts": own_ts[-1] if own_ts else None,
            },
            "cross_camera_matches": candidates,
            "timeline": timeline,
        }
