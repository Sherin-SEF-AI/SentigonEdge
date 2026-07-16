"""Prompt-building + response-parsing for the proactive VLM signature sweep.

Pure-logic tests (no VLM, no DB): they guard the batched prompt and the defensive
JSON parsing that turns a VLM reply into fired signatures.
"""
from __future__ import annotations

from sentigon_common.db.models import Signature
from sentigon_common.schemas.enums import DetectionMethod, Severity
from sentigon_reason.signature_sweep import _build_prompt, _parse_matches


def _sig(name: str, desc: str, kws=None) -> Signature:
    return Signature(
        name=name,
        category="violence",
        description=desc,
        severity=Severity.HIGH,
        detection_method=DetectionMethod.VLM,
        params={"keywords": kws or []},
    )


SIGS = [
    _sig("Weapon Brandished", "a person holding a firearm", ["gun", "rifle"]),
    _sig("Fire or Smoke", "visible flames or smoke", ["fire", "smoke"]),
]


def test_prompt_lists_signatures_and_fences_them():
    p = _build_prompt(SIGS)
    assert "1. Weapon Brandished" in p and "2. Fire or Smoke" in p
    assert "cues: gun, rifle" in p
    assert "untrusted" in p.lower()  # injection fencing present
    assert '"matches"' in p  # strict JSON contract


def test_parse_valid_matches_maps_ids_to_signatures():
    text = 'Sure: {"matches": [{"id": 2, "reason": "flames visible on the left"}]}'
    got = _parse_matches(text, SIGS)
    assert len(got) == 1
    sig, reason = got[0]
    assert sig.name == "Fire or Smoke"
    assert reason == "flames visible on the left"


def test_parse_empty_and_malformed_are_safe():
    assert _parse_matches('{"matches": []}', SIGS) == []
    assert _parse_matches("no json here", SIGS) == []
    assert _parse_matches("", SIGS) == []
    assert _parse_matches('{"matches": "not-a-list"}', SIGS) == []


def test_parse_ignores_out_of_range_and_bad_ids():
    text = '{"matches": [{"id": 99}, {"id": "x"}, {"id": 1, "reason": "gun"}]}'
    got = _parse_matches(text, SIGS)
    assert [s.name for s, _ in got] == ["Weapon Brandished"]
