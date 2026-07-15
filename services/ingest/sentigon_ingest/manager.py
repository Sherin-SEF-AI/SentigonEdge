"""Ingest manager: owns the per-camera workers, uploads recorded segments to MinIO,
and publishes per-stream health to Redis and the ingest.health Kafka topic.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import orjson
import redis.asyncio as aioredis
from sentigon_common.config import settings
from sentigon_common.db import async_session_factory, sync_session_factory
from sentigon_common.db.models import Camera, Recording
from sentigon_common.kafka import BusProducer, ensure_topics
from sentigon_common.logging import get_logger
from sentigon_common.schemas.bus import StreamHealthMsg, Topics
from sentigon_common.schemas.enums import CameraStatus, RecordingType
from sentigon_common.storage import get_store
from sqlalchemy import func, select

from .capture import CameraWorker, SegmentInfo
from .config import ingest_settings
from .mediamtx import MediaMTXClient

log = get_logger("ingest.manager")


class IngestManager:
    def __init__(self) -> None:
        self.producer = BusProducer()
        self.store = get_store()
        self.mtx = MediaMTXClient()
        self.redis = aioredis.from_url(settings.redis_url)
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="ingest-io")
        self._workers: dict[uuid.UUID, CameraWorker] = {}
        self._meta: dict[uuid.UUID, dict] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._health_task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        os.makedirs(ingest_settings.work_dir, exist_ok=True)
        await ensure_topics([Topics.INGEST_HEALTH])
        self.store.ensure_buckets(
            [
                settings.minio_bucket_recordings,
                settings.minio_bucket_snapshots,
                settings.minio_bucket_clips,
            ]
        )
        await self.producer.start()

        async with async_session_factory() as session:
            cameras = (
                (await session.execute(select(Camera).where(Camera.is_active.is_(True))))
                .scalars()
                .all()
            )
        for cam in cameras:
            self._start_worker(cam.id, cam.name, cam.rtsp_uri)
        self._health_task = asyncio.create_task(self._health_pump())
        log.info("ingest.started", cameras=len(self._workers))

    async def stop(self) -> None:
        self._stopping = True
        if self._health_task:
            self._health_task.cancel()
        for w in self._workers.values():
            w.stop()
        for w in self._workers.values():
            w.join(timeout=5.0)
        self._pool.shutdown(wait=True, cancel_futures=True)
        await self.producer.stop()
        await self.redis.aclose()
        log.info("ingest.stopped")

    # ── worker lifecycle ──────────────────────────────────────
    def _start_worker(self, camera_id: uuid.UUID, name: str, rtsp_uri: str) -> None:
        if camera_id in self._workers:
            return
        worker = CameraWorker(
            camera_id=camera_id,
            name=name,
            rtsp_uri=rtsp_uri,
            settings=ingest_settings,
            on_segment=self._on_segment,
        )
        self._workers[camera_id] = worker
        self._meta[camera_id] = {"name": name, "rtsp_uri": rtsp_uri}
        worker.start()

    def stop_worker(self, camera_id: uuid.UUID) -> bool:
        worker = self._workers.pop(camera_id, None)
        self._meta.pop(camera_id, None)
        if worker is None:
            return False
        worker.stop()
        return True

    async def add_camera(self, camera_id: uuid.UUID, name: str, rtsp_uri: str) -> None:
        self._start_worker(camera_id, name, rtsp_uri)

    # ── segment upload (runs in the io pool) ──────────────────
    def _on_segment(self, seg: SegmentInfo) -> None:
        # called from the capture thread; hand off to the io pool
        self._pool.submit(self._upload_segment, seg)

    def _upload_segment(self, seg: SegmentInfo) -> None:
        try:
            day = datetime.fromtimestamp(seg.start_ts, tz=UTC).strftime("%Y/%m/%d")
            key = f"{seg.camera_id}/{day}/{os.path.basename(seg.path)}"
            self.store.put_file(
                settings.minio_bucket_recordings, key, seg.path, content_type="video/mp4"
            )
            size = os.path.getsize(seg.path)
            with sync_session_factory() as session:
                session.add(
                    Recording(
                        camera_id=seg.camera_id,
                        recording_type=RecordingType.CONTINUOUS,
                        bucket=settings.minio_bucket_recordings,
                        object_key=key,
                        start_time=datetime.fromtimestamp(seg.start_ts, tz=UTC),
                        end_time=datetime.fromtimestamp(seg.end_ts, tz=UTC),
                        duration_seconds=max(0.0, seg.end_ts - seg.start_ts),
                        size_bytes=size,
                        meta={"frames": seg.frames, "width": seg.width, "height": seg.height},
                    )
                )
                session.commit()
            self._enforce_retention(seg.camera_id)
            log.info("segment.uploaded", camera=str(seg.camera_id), key=key, bytes=size)
        except Exception:  # noqa: BLE001
            log.exception("segment.upload_failed", camera=str(seg.camera_id))
        finally:
            with contextlib.suppress(OSError):
                os.remove(seg.path)

    def _enforce_retention(self, camera_id: uuid.UUID) -> None:
        limit = ingest_settings.retention_segments
        with sync_session_factory() as session:
            count = session.scalar(
                select(func.count()).select_from(Recording).where(Recording.camera_id == camera_id)
            )
            if not count or count <= limit:
                return
            old = (
                session.execute(
                    select(Recording)
                    .where(Recording.camera_id == camera_id)
                    .order_by(Recording.start_time.asc())
                    .limit(count - limit)
                )
                .scalars()
                .all()
            )
            for rec in old:
                with contextlib.suppress(Exception):
                    self.store.remove(rec.bucket, rec.object_key)
                session.delete(rec)
            session.commit()

    # ── health ────────────────────────────────────────────────
    async def _health_pump(self) -> None:
        while not self._stopping:
            try:
                await self._publish_health_once()
            except Exception:  # noqa: BLE001
                log.exception("health.pump_error")
            await asyncio.sleep(ingest_settings.health_interval_seconds)

    async def _publish_health_once(self) -> None:
        for camera_id, worker in list(self._workers.items()):
            snap = worker.snapshot()
            name = self._meta.get(camera_id, {}).get("name", "camera")
            msg = StreamHealthMsg(
                producer="ingest",
                camera_id=camera_id,
                name=name,
                status=snap["status"],
                fps=snap["fps"],
                target_fps=float(ingest_settings.record_fps),
                jitter_ms=snap["jitter_ms"],
                decode_errors=snap["decode_errors"],
                reconnects=snap["reconnects"],
                resolution=snap["resolution"],
            )
            await self.producer.publish(Topics.INGEST_HEALTH, msg, key=str(camera_id))
            await self.redis.set(f"camera:{camera_id}:health", orjson.dumps(snap), ex=30)
            await self._update_camera_row(camera_id, snap)

    async def _update_camera_row(self, camera_id: uuid.UUID, snap: dict) -> None:
        status_map = {
            "online": CameraStatus.ONLINE,
            "offline": CameraStatus.OFFLINE,
            "connecting": CameraStatus.OFFLINE,
        }
        async with async_session_factory() as session:
            cam = await session.get(Camera, camera_id)
            if cam is None:
                return
            cam.health = snap
            cam.status = status_map.get(snap["status"], CameraStatus.ERROR)
            if snap["status"] == "online":
                cam.last_seen = datetime.now(UTC)
            await session.commit()

    # ── on-demand evidence (snapshot / pre-roll clip) ────────
    def snapshot(self, camera_id: uuid.UUID) -> dict | None:
        import cv2  # local import: only needed on demand

        worker = self._workers.get(camera_id)
        if worker is None:
            return None
        frame = worker.latest_frame()
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return None
        day = datetime.now(UTC).strftime("%Y/%m/%d")
        key = f"{camera_id}/{day}/snap_{uuid.uuid4().hex[:10]}.jpg"
        self.store.put_bytes(
            settings.minio_bucket_snapshots, key, buf.tobytes(), content_type="image/jpeg"
        )
        return {
            "bucket": settings.minio_bucket_snapshots,
            "key": key,
            "ref": f"{settings.minio_bucket_snapshots}/{key}",
            "url": self.store.presigned_get(settings.minio_bucket_snapshots, key, 3600),
        }

    def clip(self, camera_id: uuid.UUID) -> dict | None:
        worker = self._workers.get(camera_id)
        if worker is None:
            return None
        tmp = os.path.join(ingest_settings.work_dir, f"clip_{uuid.uuid4().hex[:10]}.mp4")
        seg = worker.dump_preroll(tmp)
        if seg is None:
            return None
        day = datetime.now(UTC).strftime("%Y/%m/%d")
        key = f"{camera_id}/{day}/clip_{uuid.uuid4().hex[:10]}.mp4"
        try:
            self.store.put_file(settings.minio_bucket_clips, key, tmp, content_type="video/mp4")
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp)
        return {
            "bucket": settings.minio_bucket_clips,
            "key": key,
            "ref": f"{settings.minio_bucket_clips}/{key}",
            "url": self.store.presigned_get(settings.minio_bucket_clips, key, 3600),
        }

    # ── views ─────────────────────────────────────────────────
    def list_streams(self) -> list[dict]:
        out = []
        for camera_id, worker in self._workers.items():
            meta = self._meta.get(camera_id, {})
            rtsp = meta.get("rtsp_uri", "")
            out.append(
                {
                    "camera_id": str(camera_id),
                    "name": meta.get("name"),
                    "rtsp_uri": rtsp,
                    "whep_url": self.mtx.whep_url(rtsp),
                    "hls_url": self.mtx.hls_url(rtsp),
                    "health": worker.snapshot(),
                }
            )
        return out
