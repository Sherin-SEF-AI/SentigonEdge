"""Champion-challenger shadow inference on LIVE traffic.

Pulls the current live frame from each running camera, runs both the champion
and a challenger detector on it, and logs the comparison (detections, agreement)
WITHOUT affecting the live pipeline or operators. This is how a promotion
decision gets real production evidence before the challenger is ever acted on.

    uv run python -m bench.shadow_infer --challenger yolo26x.pt

Writes a shadow EvalRun (gold_set='shadow-live') for the challenger.
"""

from __future__ import annotations

import argparse
import json

import cv2
from sentigon_common.config import settings as common
from sentigon_common.db import sync_session_factory
from sentigon_common.db.models import EvalRun, ModelVersion
from sentigon_common.logging import configure_logging, get_logger
from sentigon_perception.detector import Detector
from sqlalchemy import select

log = get_logger("shadow")
STREAMS = {
    "cam_entrance": "rtsp://localhost:8554/cam_entrance",
    "cam_warehouse": "rtsp://localhost:8554/cam_warehouse",
    "cam_street": "rtsp://localhost:8554/cam_street",
    "cam_retail": "rtsp://localhost:8554/cam_retail",
}


def _grab(uri: str):
    cap = cv2.VideoCapture(uri, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    frame = None
    for _ in range(6):
        ok, f = cap.read()
        if ok:
            frame = f
    cap.release()
    return frame


def main() -> int:
    configure_logging("shadow")
    ap = argparse.ArgumentParser()
    ap.add_argument("--champion", default=common.__dict__.get("model", "yolo26m.pt") or "yolo26m.pt")
    ap.add_argument("--challenger", default="yolo26x.pt")
    ap.add_argument("--version", default="yolo26x", help="challenger ModelVersion.version to attach the shadow run")
    args = ap.parse_args()

    champ = Detector(args.champion, "cuda")
    chall = Detector(args.challenger, "cuda")

    per_cam = []
    tot_c = tot_x = agree = 0
    for name, uri in STREAMS.items():
        frame = _grab(uri)
        if frame is None:
            continue
        c = champ.track(frame)
        x = chall.track(frame)
        cc, xc = len(c), len(x)
        tot_c += cc
        tot_x += xc
        agree += min(cc, xc)
        per_cam.append({"camera": name, "champion_dets": cc, "challenger_dets": xc})
        log.info("shadow.compared", camera=name, champion=cc, challenger=xc)

    agreement = round(agree / max(1, max(tot_c, tot_x)), 3)
    metrics = {
        "champion_total_dets": tot_c,
        "challenger_total_dets": tot_x,
        "detection_agreement": agreement,
        "cameras": per_cam,
        "note": "shadow inference on live frames; challenger verdicts logged, not acted on",
    }
    with sync_session_factory() as s:
        mv = s.execute(select(ModelVersion).where(ModelVersion.version == args.version)).scalar_one_or_none()
        s.add(EvalRun(model_version_id=mv.id if mv else None, gold_set="shadow-live", metrics=metrics, passed=None, notes="champion-challenger shadow on live traffic"))
        s.commit()

    print(json.dumps(metrics, indent=2))
    print(f"\nchampion({args.champion}) {tot_c} dets vs challenger({args.challenger}) {tot_x} dets, agreement {agreement}")
    print("challenger ran in SHADOW: verdicts logged, operators unaffected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
