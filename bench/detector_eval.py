"""Real object-detector evaluation against a labeled gold set (COCO val2017).

    python -m bench.detector_eval --weights yolo26m.pt --version yolo26m

Runs the actual Ultralytics model over the 5000-image COCO val set, restricted to
the classes Sentigon deploys (person/bicycle/car/motorcycle/bus/truck/backpack/
handbag/suitcase/knife), and computes standard precision / recall / mAP50 /
mAP50-95, plus per-class precision / recall / AP50. Writes a real EvalRun row
(linked to the model's ModelVersion when one exists) and a JSON + Markdown report.

This is the live detector, not a stand-in: same weights, imgsz, and IoU the
perception service runs. mAP is measured over the full confidence sweep (the
canonical benchmark); the production operating point is conf=0.35.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sentigon_common.db import sync_session_factory
from sentigon_common.db.models import EvalRun, ModelVersion
from sentigon_common.logging import configure_logging, get_logger
from sqlalchemy import select
from ultralytics import YOLO

log = get_logger("detector-eval")
REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = REPO_ROOT / "bench" / "reports"

# The classes the perception service actually detects (COCO ids -> names).
SENTIGON_CLASSES = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
    24: "backpack",
    26: "handbag",
    28: "suitcase",
    43: "knife",
}


def run_eval(weights: str, data: str, imgsz: int, iou: float) -> dict:
    model = YOLO(weights)
    ids = sorted(SENTIGON_CLASSES)
    metrics = model.val(
        data=data,
        imgsz=imgsz,
        iou=iou,
        classes=ids,
        verbose=False,
        plots=False,
        save_json=False,
    )
    box = metrics.box
    # metrics.box.* are arrays aligned to the evaluated class order.
    per_class: dict[str, dict] = {}
    for i, c in enumerate(metrics.box.ap_class_index.tolist()):
        name = SENTIGON_CLASSES.get(int(c), str(c))
        per_class[name] = {
            "precision": round(float(box.p[i]), 4),
            "recall": round(float(box.r[i]), 4),
            "ap50": round(float(box.ap50[i]), 4),
            "ap50_95": round(float(box.ap[i]), 4),
        }
    return {
        "gold_set": "coco-val2017",
        "images": int(metrics.seen) if hasattr(metrics, "seen") else 5000,
        "classes_evaluated": len(per_class),
        "mAP50": round(float(box.map50), 4),
        "mAP50_95": round(float(box.map), 4),
        "precision": round(float(box.mp), 4),
        "recall": round(float(box.mr), 4),
        "per_class": per_class,
    }


def persist(version: str, result: dict, threshold: float) -> str:
    passed = result["mAP50"] >= threshold
    with sync_session_factory() as session:
        mv = session.execute(
            select(ModelVersion).where(ModelVersion.version == version)
        ).scalar_one_or_none()
        session.add(
            EvalRun(
                model_version_id=mv.id if mv else None,
                gold_set=result["gold_set"],
                metrics=result,
                passed=passed,
                notes=(
                    f"COCO val2017, {result['classes_evaluated']} deployed classes, "
                    f"mAP50={result['mAP50']}, mAP50-95={result['mAP50_95']}"
                ),
            )
        )
        session.commit()
        linked = "linked" if mv else "unlinked (no ModelVersion row)"
    return f"EvalRun persisted ({linked}), passed={passed}"


def write_report(version: str, result: dict) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    jp = REPORTS_DIR / f"detector_{version}.json"
    jp.write_text(json.dumps(result, indent=2))
    md = [
        f"# Detector eval: {version} on {result['gold_set']}",
        "",
        f"- images: {result['images']}",
        f"- mAP50: **{result['mAP50']}**  mAP50-95: **{result['mAP50_95']}**",
        f"- mean precision: {result['precision']}  mean recall: {result['recall']}",
        "",
        "| class | precision | recall | AP50 | AP50-95 |",
        "|---|---|---|---|---|",
    ]
    for name, m in sorted(result["per_class"].items(), key=lambda kv: -kv[1]["ap50"]):
        md.append(f"| {name} | {m['precision']} | {m['recall']} | {m['ap50']} | {m['ap50_95']} |")
    (REPORTS_DIR / f"detector_{version}.md").write_text("\n".join(md))
    return jp


def main() -> int:
    configure_logging("detector-eval")
    ap = argparse.ArgumentParser(description="Sentigon detector eval on COCO val2017")
    ap.add_argument("--weights", default="yolo26m.pt")
    ap.add_argument("--version", default="yolo26m", help="ModelVersion.version to link")
    ap.add_argument("--data", default=str(REPO_ROOT / "datasets" / "coco" / "coco-val.yaml"))
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--threshold", type=float, default=0.5, help="pass if mAP50 >= threshold")
    args = ap.parse_args()

    result = run_eval(args.weights, args.data, args.imgsz, args.iou)
    msg = persist(args.version, result, args.threshold)
    path = write_report(args.version, result)

    print(json.dumps(result, indent=2))
    print(msg)
    print(f"report: {path}")
    log.info("detector_eval.done", version=args.version, mAP50=result["mAP50"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
