"""Champion-challenger comparison and promotion.

Operates on EvalRun.metrics (produced by bench.eval_harness): a challenger wins if
it matches or beats the champion on precision and recall and strictly improves at
least one by `min_delta`. Promotion swaps stages atomically (old champion retired).
"""

from __future__ import annotations

from dataclasses import dataclass

from sentigon_common.db.models import EvalRun, ModelVersion
from sentigon_common.logging import get_logger
from sentigon_common.schemas.enums import ModelRole, ModelStage
from sqlalchemy import select
from sqlalchemy.orm import Session

log = get_logger("governance")


@dataclass
class PromotionDecision:
    role: str
    challenger_id: str | None
    champion_id: str | None
    promote: bool
    reason: str


def _aggregate(run: EvalRun | None) -> dict:
    if run is None or not run.metrics:
        return {"micro_precision": 0.0, "micro_recall": 0.0}
    return run.metrics.get("aggregate", {})


def latest_eval(session: Session, model_version_id) -> EvalRun | None:
    return session.execute(
        select(EvalRun)
        .where(EvalRun.model_version_id == model_version_id)
        .order_by(EvalRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def challenger_wins(champion: dict, challenger: dict, min_delta: float = 0.005) -> bool:
    cp, cr = champion.get("micro_precision", 0.0), champion.get("micro_recall", 0.0)
    xp, xr = challenger.get("micro_precision", 0.0), challenger.get("micro_recall", 0.0)
    no_regression = xp >= cp - 1e-9 and xr >= cr - 1e-9
    improves = (xp >= cp + min_delta) or (xr >= cr + min_delta)
    return no_regression and improves


def decide_promotion(session: Session, role: ModelRole) -> PromotionDecision:
    champion = session.execute(
        select(ModelVersion).where(
            ModelVersion.role == role, ModelVersion.stage == ModelStage.CHAMPION
        )
    ).scalar_one_or_none()
    challenger = session.execute(
        select(ModelVersion)
        .where(ModelVersion.role == role, ModelVersion.stage == ModelStage.CHALLENGER)
        .order_by(ModelVersion.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if challenger is None:
        return PromotionDecision(
            role.value, None, str(champion.id) if champion else None, False, "no challenger"
        )
    if champion is None:
        return PromotionDecision(
            role.value, str(challenger.id), None, True, "no incumbent champion"
        )

    win = challenger_wins(
        _aggregate(latest_eval(session, champion.id)),
        _aggregate(latest_eval(session, challenger.id)),
    )
    return PromotionDecision(
        role.value,
        str(challenger.id),
        str(champion.id),
        win,
        "challenger beats champion on the gold set" if win else "no measured improvement",
    )


def promote(session: Session, challenger_id, role: ModelRole) -> None:
    """Atomically retire the current champion and promote the challenger."""
    current = (
        session.execute(
            select(ModelVersion).where(
                ModelVersion.role == role, ModelVersion.stage == ModelStage.CHAMPION
            )
        )
        .scalars()
        .all()
    )
    for mv in current:
        mv.stage = ModelStage.RETIRED
    challenger = session.get(ModelVersion, challenger_id)
    if challenger is not None:
        challenger.stage = ModelStage.CHAMPION
    session.flush()
    log.info("model.promoted", role=role.value, challenger=str(challenger_id))
