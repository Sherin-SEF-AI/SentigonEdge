"""Per-signature accuracy, adjudicated by the VLM verifier.

Every incident a signature raises is independently reviewed by the reasoning VLM
(confirmed = real threat, rejected = false alarm). Aggregating those real verdicts
per signature gives a data-driven precision proxy and false-alarm rate for each
signature, with zero manual labeling:

    precision_proxy = confirmed / (confirmed + rejected)
    false_alarm_rate = rejected / (confirmed + rejected)

    uv run python -m bench.signature_eval

This measures precision (are a signature's firings real?). True recall needs a
human-labeled gold set of clips with known ground truth (see bench/gold_set) and
is reported separately by bench.eval_harness; that labeling is the remaining gap.
"""

from __future__ import annotations

import json
from pathlib import Path

from sentigon_common.db import sync_session_factory
from sentigon_common.db.models import Incident, Signature
from sentigon_common.logging import configure_logging, get_logger
from sentigon_common.schemas.enums import Verdict
from sqlalchemy import func, select

log = get_logger("signature-eval")
REPORTS = Path(__file__).resolve().parents[1] / "bench" / "reports"


def main() -> int:
    configure_logging("signature-eval")
    rows = []
    with sync_session_factory() as session:
        agg = session.execute(
            select(
                Signature.name,
                Signature.severity,
                func.count(Incident.id),
                func.count(Incident.id).filter(Incident.verdict == Verdict.CONFIRMED),
                func.count(Incident.id).filter(Incident.verdict == Verdict.REJECTED),
            )
            .join(Incident, Incident.signature_id == Signature.id)
            .group_by(Signature.name, Signature.severity)
        ).all()
        for name, severity, total, confirmed, rejected in agg:
            adjudicated = confirmed + rejected
            rows.append(
                {
                    "signature": name,
                    "severity": severity.value,
                    "fired": int(total),
                    "confirmed": int(confirmed),
                    "rejected": int(rejected),
                    "adjudicated": int(adjudicated),
                    "precision_proxy": round(confirmed / adjudicated, 4) if adjudicated else None,
                    "false_alarm_rate": round(rejected / adjudicated, 4) if adjudicated else None,
                }
            )

    rows.sort(key=lambda r: r["fired"], reverse=True)
    total_adj = sum(r["adjudicated"] for r in rows)
    total_conf = sum(r["confirmed"] for r in rows)
    report = {
        "method": "vlm-adjudicated",
        "signatures_evaluated": len([r for r in rows if r["adjudicated"] > 0]),
        "micro_precision_proxy": round(total_conf / total_adj, 4) if total_adj else None,
        "per_signature": rows,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "signature_eval.json").write_text(json.dumps(report, indent=2))

    print(f"per-signature accuracy (VLM-adjudicated), micro precision {report['micro_precision_proxy']}")
    print(f"{'signature':32} {'sev':9} {'fired':>7} {'conf':>6} {'rej':>6} {'prec':>7} {'FAR':>7}")
    for r in rows:
        if r["adjudicated"] == 0:
            continue
        print(
            f"{r['signature'][:32]:32} {r['severity']:9} {r['fired']:>7} {r['confirmed']:>6} "
            f"{r['rejected']:>6} {str(r['precision_proxy']):>7} {str(r['false_alarm_rate']):>7}"
        )
    log.info("signature_eval.done", signatures=report["signatures_evaluated"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
