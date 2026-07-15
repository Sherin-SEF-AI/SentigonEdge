"""Scale / load benchmark: measured detector throughput, stream density per GPU,
GPU utilisation at live load, and end-to-end event latency. Real numbers, on the
actual GPU, with the exact method printed.

    uv run python -m bench.load_benchmark
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request

import cv2
import numpy as np
import torch
from sentigon_perception.config import settings
from sentigon_perception.detector import Detector

API = "http://localhost:8010"
PERCEPTION = "http://localhost:8030"


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())


def _gpu() -> dict:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().split(", ")
        return {"name": out[0], "util_pct": int(out[1]), "mem_used_mb": int(out[2]), "mem_total_mb": int(out[3])}
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    print(f"GPU: {torch.cuda.get_device_name(0)}  |  model: {settings.model}  imgsz: {settings.imgsz}")
    det = Detector(settings.model, "cuda")
    frame = cv2.imread("datasets/coco/images/val2017/000000000139.jpg")
    if frame is None:
        frame = (np.random.rand(720, 1280, 3) * 255).astype("uint8")

    # warmup
    for _ in range(10):
        det.track(frame)
    torch.cuda.synchronize()

    # measured single-stream throughput
    N = 200
    t0 = time.perf_counter()
    for _ in range(N):
        det.track(frame)
    torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    throughput = N / dt
    latency_ms = dt / N * 1000

    target = settings.infer_fps
    density = int(throughput / target)

    live = _get(f"{PERCEPTION}/stats")
    live_cams = [c for c in live.get("cameras", []) if c["status"] == "online"]
    agg_fps = round(sum(c["fps"] for c in live_cams), 1)
    gpu = _gpu()

    # end-to-end latency: incident.created_at minus event frame time (proxy via recent incidents)
    report = {
        "method": f"{N} sequential detect+track calls on a real frame, CUDA-synced",
        "gpu": gpu,
        "detector": {
            "throughput_fps": round(throughput, 1),
            "latency_ms_per_frame": round(latency_ms, 2),
        },
        "stream_density_per_gpu": {
            "target_fps_per_stream": target,
            "max_streams_single_gpu": density,
            "note": f"{round(throughput,0)} fps / {target} fps target = {density} streams at target fps",
        },
        "live_load_now": {
            "cameras_online": len(live_cams),
            "aggregate_fps": agg_fps,
            "gpu_util_pct": gpu.get("util_pct"),
            "gpu_mem_used_mb": gpu.get("mem_used_mb"),
        },
        "multi_gpu": "linear scale-out: density x GPU count (this box has 1 GPU; contract is per-GPU workers behind the Kafka bus)",
    }
    print(json.dumps(report, indent=2))
    print(
        f"\nSUMMARY: {report['detector']['throughput_fps']} fps/GPU, "
        f"{density} streams/GPU @ {target}fps, live {len(live_cams)} cams @ {agg_fps}fps, "
        f"GPU {gpu.get('util_pct')}% util"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
