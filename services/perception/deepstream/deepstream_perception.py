"""DeepStream 8 perception pipeline (RunPod / dGPU target).

This is the GPU-accelerated, multi-stream production path. It mirrors the exact
Kafka contract of the local PyTorch worker (sentigon_common.schemas.bus.
ObjectDetectionMsg on `perception.objects`), so the Context service downstream is
unchanged whichever perception backend runs.

Pipeline:
  nvurisrcbin(s) -> nvstreammux -> nvinfer(YOLO26 primary GIE)
                 -> nvtracker(NvDCF) -> nvdsanalytics(ROI/line/overcrowding)
                 -> probe(extract NvDsBatchMeta -> Kafka) -> fakesink

Not executed on the local 16 GB dev box (no NVIDIA Container Toolkit / DeepStream
runtime here). Build and run it on the RunPod host via the Dockerfile in this
directory. Dynamic source add/remove is supported through nvstreammux request pads.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import UTC, datetime

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

import pyds  # noqa: E402  (DeepStream Python bindings; present in the DS container)
from confluent_kafka import Producer  # noqa: E402

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "redpanda:9092")
TOPIC_OBJECTS = "perception.objects"
MUXER_W = int(os.environ.get("DS_MUX_WIDTH", "1280"))
MUXER_H = int(os.environ.get("DS_MUX_HEIGHT", "720"))

_producer = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP, "linger.ms": 5})
# camera index -> uuid mapping, provided at launch (matches the DB camera ids)
_SOURCE_IDS: list[str] = json.loads(os.environ.get("DS_SOURCE_IDS", "[]"))


def _osd_probe(pad, info, _u):
    """Extract per-object metadata from NvDsBatchMeta and publish to Kafka."""
    buf = info.get_buffer()
    if not buf:
        return Gst.PadProbeReturn.OK
    batch = pyds.gst_buffer_get_nvds_batch_meta(hash(buf))
    l_frame = batch.frame_meta_list
    while l_frame is not None:
        frame = pyds.NvDsFrameMeta.cast(l_frame.data)
        objects = []
        l_obj = frame.obj_meta_list
        while l_obj is not None:
            obj = pyds.NvDsObjectMeta.cast(l_obj.data)
            r = obj.rect_params
            zone_hits = []
            # nvdsanalytics attaches ROI labels as user meta on the object
            l_user = obj.obj_user_meta_list
            while l_user is not None:
                um = pyds.NvDsUserMeta.cast(l_user.data)
                if um.base_meta.meta_type == pyds.nvds_get_user_meta_type(
                    "NVIDIA.DSANALYTICSOBJ.USER_META"
                ):
                    ana = pyds.NvDsAnalyticsObjInfo.cast(um.user_meta_data)
                    zone_hits = list(ana.roiStatus)
                l_user = l_user.next
            objects.append(
                {
                    "track_id": int(obj.object_id),
                    "object_class": obj.obj_label,
                    "confidence": round(float(obj.confidence), 3),
                    "bbox": [round(r.left, 1), round(r.top, 1), round(r.width, 1), round(r.height, 1)],
                    "zone_hits": zone_hits,
                    "keypoints": None,
                    "attributes": {},
                }
            )
            l_obj = l_obj.next

        src = frame.source_id
        camera_id = _SOURCE_IDS[src] if src < len(_SOURCE_IDS) else str(src)
        # frame_ts is REQUIRED by ObjectDetectionMsg and the context consumer drops
        # any message missing it (silently) — without this the entire DeepStream/GPU
        # path produced zero downstream events. Also stamp the envelope fields.
        _now_iso = datetime.now(UTC).isoformat()
        msg = {
            "message_id": str(uuid.uuid4()),
            "producer": "perception-deepstream",
            "ts": _now_iso,
            "camera_id": camera_id,
            "seq": int(frame.frame_num),
            "frame_ts": _now_iso,
            "frame_width": MUXER_W,
            "frame_height": MUXER_H,
            "objects": objects,
            "inference_ms": 0.0,
        }
        _producer.produce(TOPIC_OBJECTS, key=camera_id, value=json.dumps(msg))
        l_frame = l_frame.next
    _producer.poll(0)
    return Gst.PadProbeReturn.OK


def _make_source(uri: str, index: int) -> Gst.Element:
    bin_ = Gst.ElementFactory.make("nvurisrcbin", f"src-{index}")
    bin_.set_property("uri", uri)
    bin_.set_property("rtsp-reconnect-interval", 5)
    return bin_


def build_pipeline(uris: list[str]) -> tuple[Gst.Pipeline, Gst.Element]:
    Gst.init(None)
    pipeline = Gst.Pipeline.new("sentigon-perception")

    mux = Gst.ElementFactory.make("nvstreammux", "mux")
    mux.set_property("batch-size", max(1, len(uris)))
    mux.set_property("width", MUXER_W)
    mux.set_property("height", MUXER_H)
    mux.set_property("live-source", 1)
    mux.set_property("batched-push-timeout", 40000)
    pipeline.add(mux)

    for i, uri in enumerate(uris):
        src = _make_source(uri, i)
        pipeline.add(src)
        sinkpad = mux.request_pad_simple(f"sink_{i}")

        def _link(src_bin, pad, target=sinkpad):
            if pad.get_name().startswith("vsrc") or "src" in pad.get_name():
                pad.link(target)

        src.connect("pad-added", _link)

    pgie = Gst.ElementFactory.make("nvinfer", "primary-yolo26")
    pgie.set_property("config-file-path", "config_infer_primary_yolo26.txt")

    tracker = Gst.ElementFactory.make("nvtracker", "tracker")
    tracker.set_property("ll-lib-file", "/opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so")
    tracker.set_property("ll-config-file", "config_tracker_nvdcf.yml")

    analytics = Gst.ElementFactory.make("nvdsanalytics", "analytics")
    analytics.set_property("config-file", "config_nvdsanalytics.txt")

    sink = Gst.ElementFactory.make("fakesink", "sink")
    sink.set_property("sync", 0)

    for el in (pgie, tracker, analytics, sink):
        pipeline.add(el)
    mux.link(pgie)
    pgie.link(tracker)
    tracker.link(analytics)
    analytics.link(sink)

    analytics.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, _osd_probe, None)
    return pipeline, mux


def main() -> int:
    uris = json.loads(os.environ.get("DS_URIS", "[]")) or sys.argv[1:]
    if not uris:
        print("no source URIs (set DS_URIS or pass on argv)", file=sys.stderr)
        return 2
    pipeline, _mux = build_pipeline(uris)
    loop = GLib.MainLoop()
    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.set_state(Gst.State.NULL)
        _producer.flush(5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
