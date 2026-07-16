"""Guards the signature catalog against the class of bug where the context/reason
engines emit a candidate whose signature name is not seeded, so context._fire()
silently drops it (no event, no incident, no VLM).

Self-contained: loads the catalog by path exactly like sentigon_common.seed does,
so it needs neither the DB nor an installed package.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_CATALOG = REPO_ROOT / "configs" / "signatures" / "catalog.py"

# Valid domains, mirrored from schemas/enums.py and seed._METHOD so a typo in a
# new catalog entry is caught here rather than silently defaulting at seed time.
_VALID_SEVERITY = {"critical", "high", "medium", "low", "info"}
_VALID_METHOD = {"yolo", "gemini", "hybrid", "pose", "audio"}

# Signature names the context/reason engines emit directly at runtime. Each MUST
# exist in the catalog or the corresponding feature is dead. Keep in sync with
# the Candidate(...)/_SIGNATURE names in services/context and services/reason.
RUNTIME_EMITTED_NAMES = {
    "Watchlist Hit",
    "Plate Watchlist Hit",
    "Person Fall",
    "Anomalous Activity",
    "Cross-Camera Handoff",
    "Verified Forced Door",
    "Invalid Badge Followed By Tailgating",
    "Invalid Badge with Loitering",
    "Running/Fleeing",
    "Custom Activity Alert",
    # sanity anchors that were always seeded
    "Perimeter Breach",
    "Loitering",
    "Tailgating",
    "Speeding Vehicle",
}


def _load_catalog() -> list:
    import sys

    spec = importlib.util.spec_from_file_location("sentigon_signature_catalog", _CATALOG)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: py3.12 dataclass field resolution looks the module up
    # in sys.modules (matches sentigon_common.seed._load_catalog).
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return list(mod.THREAT_SIGNATURES)


def test_catalog_loads():
    assert len(_load_catalog()) > 0


def test_no_duplicate_signature_names():
    names = [d.name for d in _load_catalog()]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate signature names in catalog: {sorted(dupes)}"


def test_every_runtime_emitted_signature_is_seeded():
    names = {d.name for d in _load_catalog()}
    missing = RUNTIME_EMITTED_NAMES - names
    assert not missing, (
        "these signature names are emitted by the engine but not in the catalog, "
        f"so context._fire() drops them silently: {sorted(missing)}"
    )


def test_all_severities_and_methods_valid():
    bad = [
        d.name
        for d in _load_catalog()
        if d.severity not in _VALID_SEVERITY or d.detection_method not in _VALID_METHOD
    ]
    assert not bad, f"catalog entries with invalid severity/method: {bad}"
