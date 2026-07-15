"""Composite threat / risk scoring.

Turns an incident's dimensions into a single explainable 0..100 risk score so the
console can rank threats by real danger instead of raw recency, cutting alert
fatigue (the operator sees the few that matter first). Pure and deterministic:
context scores at creation, reason re-scores when the VLM verdict lands, and the
API ranks the live threat queue with the same function.
"""

from __future__ import annotations

# base points by severity
_SEVERITY = {"CRITICAL": 90, "HIGH": 72, "MEDIUM": 48, "LOW": 22, "INFO": 8}

# category adds/subtracts: life-safety categories float to the top, pure-tracking
# and informational categories sink.
_CATEGORY = {
    "active_shooter": 15,
    "violence": 14,
    "terrorism": 14,
    "child_safety": 12,
    "medical_biohazard": 10,
    "watchlist": 10,
    "insider_threat": 8,
    "intrusion": 7,
    "theft": 6,
    "escape_evasion": 6,
    "suspicious": 5,
    "tamper": 5,
    "cyber_physical": 5,
    "safety": 5,
    "social_unrest": 5,
    "vehicle": 2,
    "occupancy": 0,
    "operational": -4,
    "compliance": -4,
    "tracking": -12,  # cross-camera handoff etc. are context, not threats
}

# zone criticality: an event in a restricted/exclusion area is worse than in a
# general/public one.
_ZONE = {
    "RESTRICTED": 8,
    "EXCLUSION": 8,
    "PRODUCTION_FLOOR": 6,
    "LOADING_DOCK": 5,
    "ENTRY": 4,
    "PERIMETER": 4,
    "PARKING": 2,
    "GENERAL": 0,
}


def compute_risk_score(
    *,
    severity: str,
    category: str | None = None,
    confidence: float = 0.5,
    verdict: str | None = None,
    zone_type: str | None = None,
    correlated_signals: int = 0,
) -> tuple[int, dict]:
    """Return (score 0..100, breakdown). `correlated_signals` is the count of
    corroborating signals (a bound access-control event, repeat incidents on the
    same subject/camera): corroboration raises confidence a real threat."""
    base = _SEVERITY.get((severity or "").upper(), 40)
    cat = _CATEGORY.get((category or "").lower(), 0)
    zone = _ZONE.get((zone_type or "").upper(), 0)
    # confidence nudges +-10 around the 0.5 midpoint
    conf = round((max(0.0, min(1.0, confidence)) - 0.5) * 20)
    # VLM adjudication is the strongest single signal
    v = (verdict or "").upper()
    verd = 12 if v == "CONFIRMED" else (-45 if v == "REJECTED" else 0)
    corr = min(correlated_signals * 6, 18)

    raw = base + cat + zone + conf + verd + corr
    score = max(0, min(100, raw))
    return score, {
        "base_severity": base,
        "category": cat,
        "zone": zone,
        "confidence": conf,
        "verdict": verd,
        "corroboration": corr,
    }


def priority_band(score: int) -> str:
    if score >= 80:
        return "P1"
    if score >= 60:
        return "P2"
    if score >= 40:
        return "P3"
    return "P4"
