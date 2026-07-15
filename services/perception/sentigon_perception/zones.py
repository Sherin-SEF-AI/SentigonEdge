"""Zone analytics: point-in-polygon tests of tracked-object centroids against a
camera's zones. Polygons are stored normalized (0..1) so they are resolution
independent; the perception frame and the console overlay both scale to pixels.
"""

from __future__ import annotations

import time
import uuid

from sentigon_common.db import sync_session_factory
from sentigon_common.db.models import Zone
from sqlalchemy import select


def _point_in_poly(x: float, y: float, poly: list[list[float]]) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside


class ZoneIndex:
    """Per-camera cache of zone polygons, refreshed from the DB periodically."""

    def __init__(self, refresh_seconds: float = 15.0) -> None:
        self._refresh = refresh_seconds
        self._cache: dict[uuid.UUID, list[dict]] = {}
        self._loaded_at: dict[uuid.UUID, float] = {}

    def _load(self, camera_id: uuid.UUID) -> list[dict]:
        with sync_session_factory() as session:
            zones = session.execute(select(Zone).where(Zone.camera_id == camera_id)).scalars().all()
        out: list[dict] = []
        for z in zones:
            poly = (z.polygon_image or {}).get("points") if z.polygon_image else None
            if poly:
                out.append(
                    {"id": str(z.id), "name": z.name, "type": z.zone_type.value, "poly": poly}
                )
        return out

    def zones_for(self, camera_id: uuid.UUID) -> list[dict]:
        now = time.monotonic()
        if camera_id not in self._cache or now - self._loaded_at.get(camera_id, 0) > self._refresh:
            self._cache[camera_id] = self._load(camera_id)
            self._loaded_at[camera_id] = now
        return self._cache[camera_id]

    def hits(self, camera_id: uuid.UUID, nx: float, ny: float) -> list[str]:
        """Return ids of zones whose polygon contains the normalized point (nx, ny)."""
        return [z["id"] for z in self.zones_for(camera_id) if _point_in_poly(nx, ny, z["poly"])]

    def hits_mask(self, camera_id: uuid.UUID, mask: list[list[float]]) -> list[str]:
        """Mask-precise zone intrusion: a zone is hit if ANY point of the object's
        segmentation boundary falls inside it. Fires on true silhouette crossing,
        not just the bbox centroid, so a person whose body enters a zone while their
        center is still outside is correctly detected."""
        out = []
        for z in self.zones_for(camera_id):
            if any(_point_in_poly(px, py, z["poly"]) for px, py in mask):
                out.append(z["id"])
        return out

    def overlay(self, camera_id: uuid.UUID) -> list[dict]:
        """Zone polygons for the console overlay (normalized coords)."""
        return self.zones_for(camera_id)
