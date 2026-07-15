"""Gold-set evaluation harness.

    python -m bench.eval_harness [--gold-set bench/gold_set/manifest.yaml]

Loads a labeled gold set, runs each clip through a prediction function, scores
precision/recall/false-alarm-rate per signature, persists an EvalRun row, and
writes a JSON + Markdown report. Runs clean (exit 0) on an empty gold set.

The prediction function is pluggable. Until the perception/context/reason pipeline
exists (Phases 2-4), a clip may carry a `predictions` list in the manifest so the
scoring path is exercisable now; the default predictor otherwise returns nothing.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sentigon_common.db import sync_session_factory
from sentigon_common.db.models import EvalRun
from sentigon_common.logging import configure_logging, get_logger

from bench.metrics import EvalReport

log = get_logger("eval")
REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = REPO_ROOT / "bench" / "reports"


@dataclass
class GoldClip:
    id: str
    label: str  # positive | negative
    signatures: list[str] = field(default_factory=list)  # ground truth present
    predictions: list[str] = field(default_factory=list)  # optional precomputed
    path: str | None = None
    camera: str | None = None


Predictor = Callable[[GoldClip], set[str]]


def default_predictor(clip: GoldClip) -> set[str]:
    """Placeholder until the live pipeline is wired in. Honors precomputed
    predictions if the manifest supplies them, else predicts nothing."""
    return set(clip.predictions)


def load_gold_set(path: Path) -> tuple[str, list[GoldClip]]:
    if not path.exists():
        return (path.stem, [])
    data = yaml.safe_load(path.read_text()) or {}
    name = data.get("gold_set", path.stem)
    clips: list[GoldClip] = []
    for c in data.get("clips") or []:
        clips.append(
            GoldClip(
                id=str(c["id"]),
                label=c.get("label", "positive"),
                signatures=list(c.get("signatures") or []),
                predictions=list(c.get("predictions") or []),
                path=c.get("path"),
                camera=c.get("camera"),
            )
        )
    return (name, clips)


def evaluate(gold_set: str, clips: list[GoldClip], predict: Predictor) -> EvalReport:
    report = EvalReport(gold_set=gold_set, total_clips=len(clips))
    for clip in clips:
        expected = set(clip.signatures)
        predicted = predict(clip)
        for sig in expected & predicted:
            report.metric_for(sig).tp += 1
        for sig in expected - predicted:
            report.metric_for(sig).fn += 1
        for sig in predicted - expected:
            report.metric_for(sig).fp += 1
    if not clips:
        report.notes = "empty gold set: add clips to bench/gold_set/ to measure signatures"
    return report


def persist_eval_run(report: EvalReport) -> None:
    try:
        with sync_session_factory() as session:
            session.add(
                EvalRun(
                    gold_set=report.gold_set,
                    metrics=report.to_dict(),
                    passed=None if report.total_clips == 0 else report.micro_precision >= 0.9,
                    notes=report.notes,
                )
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("eval.persist_skipped", error=str(exc))


def write_report(report: EvalReport) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    json_path = REPORTS_DIR / f"eval_{report.gold_set}.json"
    json_path.write_text(json.dumps(payload, indent=2))
    md = ["# Sentigon eval report", "", "```", report.summary(), "```", ""]
    if report.per_signature:
        md += [
            "| signature | tp | fp | fn | precision | recall | false-alarm |",
            "|---|---|---|---|---|---|---|",
        ]
        for name, m in sorted(report.per_signature.items()):
            d = m.to_dict()
            md.append(
                f"| {name} | {d['tp']} | {d['fp']} | {d['fn']} | "
                f"{d['precision']} | {d['recall']} | {d['false_alarm_rate']} |"
            )
    (REPORTS_DIR / f"eval_{report.gold_set}.md").write_text("\n".join(md))
    return json_path


def main() -> int:
    configure_logging("eval")
    parser = argparse.ArgumentParser(description="Sentigon gold-set evaluation")
    parser.add_argument(
        "--gold-set", default=str(REPO_ROOT / "bench" / "gold_set" / "manifest.yaml")
    )
    args = parser.parse_args()

    name, clips = load_gold_set(Path(args.gold_set))
    report = evaluate(name, clips, default_predictor)
    persist_eval_run(report)
    path = write_report(report)

    print(report.summary())
    print(f"report written: {path}")
    log.info("eval.done", gold_set=name, clips=len(clips))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
