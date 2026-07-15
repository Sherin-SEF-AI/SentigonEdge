"""Scoring primitives for the gold-set evaluation.

false_alarm_rate is the fraction of raised alerts that were wrong (1 - precision).
The whole point of the Reason VLM stage is to drive this down, so it is reported
both before and after verification once that stage exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SignatureMetrics:
    signature: str
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def support(self) -> int:
        return self.tp + self.fn

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def false_alarm_rate(self) -> float:
        denom = self.tp + self.fp
        return self.fp / denom if denom else 0.0

    def to_dict(self) -> dict:
        return {
            "signature": self.signature,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "support": self.support,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "false_alarm_rate": round(self.false_alarm_rate, 4),
        }


@dataclass
class EvalReport:
    gold_set: str
    total_clips: int = 0
    per_signature: dict[str, SignatureMetrics] = field(default_factory=dict)
    notes: str = ""

    def metric_for(self, signature: str) -> SignatureMetrics:
        return self.per_signature.setdefault(signature, SignatureMetrics(signature))

    @property
    def totals(self) -> tuple[int, int, int]:
        tp = sum(m.tp for m in self.per_signature.values())
        fp = sum(m.fp for m in self.per_signature.values())
        fn = sum(m.fn for m in self.per_signature.values())
        return tp, fp, fn

    @property
    def micro_precision(self) -> float:
        tp, fp, _ = self.totals
        return tp / (tp + fp) if (tp + fp) else 0.0

    @property
    def micro_recall(self) -> float:
        tp, _, fn = self.totals
        return tp / (tp + fn) if (tp + fn) else 0.0

    @property
    def micro_false_alarm_rate(self) -> float:
        tp, fp, _ = self.totals
        return fp / (tp + fp) if (tp + fp) else 0.0

    def to_dict(self) -> dict:
        tp, fp, fn = self.totals
        return {
            "gold_set": self.gold_set,
            "total_clips": self.total_clips,
            "notes": self.notes,
            "aggregate": {
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "micro_precision": round(self.micro_precision, 4),
                "micro_recall": round(self.micro_recall, 4),
                "micro_false_alarm_rate": round(self.micro_false_alarm_rate, 4),
            },
            "per_signature": {k: v.to_dict() for k, v in sorted(self.per_signature.items())},
        }

    def summary(self) -> str:
        tp, fp, fn = self.totals
        lines = [
            f"gold set : {self.gold_set}",
            f"clips    : {self.total_clips}",
            f"signatures scored: {len(self.per_signature)}",
            f"tp/fp/fn : {tp}/{fp}/{fn}",
            f"precision: {self.micro_precision:.3f}  recall: {self.micro_recall:.3f}  "
            f"false-alarm: {self.micro_false_alarm_rate:.3f}",
        ]
        if self.notes:
            lines.append(f"notes    : {self.notes}")
        return "\n".join(lines)
