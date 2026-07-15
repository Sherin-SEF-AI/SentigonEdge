"""Perception manager: owns the per-camera workers, the shared embedder / ReID
index / zone index, the Kafka producer, and the latest-detections cache that feeds
the console overlay WebSocket.
"""

from __future__ import annotations

import asyncio
import uuid

import torch
from sentigon_common.db import async_session_factory
from sentigon_common.db.models import Camera
from sentigon_common.kafka import BusProducer, ensure_topics
from sentigon_common.logging import get_logger
from sentigon_common.schemas.bus import Topics
from sqlalchemy import select

from .config import settings
from .embedder import Embedder
from .reid_index import ReidIndex
from .worker import PerceptionWorker
from .zones import ZoneIndex

log = get_logger("perception.manager")


def _resolve_device() -> str:
    if settings.device == "cpu":
        return "cpu"
    if settings.device == "cuda":
        return "cuda"
    return "cuda" if torch.cuda.is_available() else "cpu"


class PerceptionManager:
    def __init__(self) -> None:
        self.device = _resolve_device()
        self.producer = BusProducer("perception")
        self.embedder: Embedder | None = None
        self.reid: ReidIndex | None = None
        self.plate_reader = None  # lazily loaded PlateReader when a camera opts into ANPR
        self.fall_detector = None  # lazily loaded PoseFallDetector when a camera opts in
        self.zones = ZoneIndex()
        self.workers: dict[uuid.UUID, PerceptionWorker] = {}
        self.meta: dict[uuid.UUID, dict] = {}
        self.latest: dict[uuid.UUID, dict] = {}
        self.loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        await ensure_topics([Topics.PERCEPTION_OBJECTS, Topics.PERCEPTION_EMBEDDINGS])
        await self.producer.start()
        # heavy model init off the event loop
        self.embedder = await asyncio.to_thread(Embedder, self.device)
        self.reid = await asyncio.to_thread(ReidIndex, settings.reid_collection, self.embedder.dim)

        async with async_session_factory() as session:
            cameras = (
                (await session.execute(select(Camera).where(Camera.is_active.is_(True))))
                .scalars()
                .all()
            )
        # one shared plate reader, loaded only if a camera opts into ANPR (meta.anpr)
        if any((c.meta or {}).get("anpr") for c in cameras):
            from .anpr import PlateReader

            self.plate_reader = await asyncio.to_thread(PlateReader, self.device)
            log.info("perception.anpr_loaded", model=settings.anpr_model)
        # one shared pose fall detector, loaded only if a camera opts in (meta.fall_detection)
        if any((c.meta or {}).get("fall_detection") for c in cameras):
            from .fall import PoseFallDetector

            self.fall_detector = await asyncio.to_thread(PoseFallDetector, self.device)
            log.info("perception.fall_loaded", model=settings.fall_pose_model)
        for cam in cameras:
            meta = cam.meta or {}
            self._start(cam.id, cam.name, cam.rtsp_uri, bool(meta.get("anpr")), bool(meta.get("fall_detection")))
        log.info(
            "perception.started",
            device=self.device,
            workers=len(self.workers),
            model=settings.model,
        )

    def _publish(self, topic: str, msg: object, key: str | None) -> None:
        if self.loop is None:
            return
        fut = asyncio.run_coroutine_threadsafe(
            self.producer.publish(topic, msg, key=key), self.loop
        )

        def _cb(f: object) -> None:
            try:
                f.result()  # type: ignore[attr-defined]
            except Exception:
                log.debug("perception.publish_failed", topic=topic)

        fut.add_done_callback(_cb)

    def _set_latest(self, camera_id: uuid.UUID, payload: dict) -> None:
        self.latest[camera_id] = payload

    def _start(
        self, camera_id: uuid.UUID, name: str, rtsp_uri: str,
        anpr: bool = False, fall: bool = False,
    ) -> None:
        if camera_id in self.workers:
            return
        assert self.embedder is not None and self.reid is not None
        worker = PerceptionWorker(
            camera_id=camera_id,
            name=name,
            rtsp_uri=rtsp_uri,
            device=self.device,
            embedder=self.embedder,
            reid=self.reid,
            zones=self.zones,
            publish=self._publish,
            set_latest=self._set_latest,
            plate_reader=self.plate_reader if anpr else None,
            fall_detector=self.fall_detector if fall else None,
        )
        self.workers[camera_id] = worker
        self.meta[camera_id] = {"name": name, "rtsp": rtsp_uri}
        worker.start()

    async def add_camera(self, camera_id: uuid.UUID, name: str, rtsp_uri: str) -> None:
        self._start(camera_id, name, rtsp_uri)

    def stop_worker(self, camera_id: uuid.UUID) -> bool:
        worker = self.workers.pop(camera_id, None)
        self.meta.pop(camera_id, None)
        self.latest.pop(camera_id, None)
        if worker is None:
            return False
        worker.stop()
        return True

    async def stop(self) -> None:
        for worker in self.workers.values():
            worker.stop()
        for worker in self.workers.values():
            worker.join(timeout=5.0)
        await self.producer.stop()

    def stats(self) -> list[dict]:
        return [
            {"camera_id": str(cid), "name": self.meta[cid]["name"], **worker.stats}
            for cid, worker in self.workers.items()
        ]

    def reid_count(self) -> int:
        try:
            return self.reid.count() if self.reid else 0
        except Exception:
            return 0

    def swap_model(self, model_path: str) -> list[str]:
        """Hot-swap the detector model on every camera worker with no stream drop."""
        swapped: list[str] = []
        for worker in self.workers.values():
            try:
                worker.swap_detector(model_path)
                swapped.append(worker.name_)
            except Exception:  # noqa: BLE001
                log.exception("perception.swap_failed", camera=worker.name_)
        return swapped

    def current_model(self) -> str:
        for worker in self.workers.values():
            return worker.model_name
        return ""
