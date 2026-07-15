"""Golden-path self-test: confirm the full pipeline is live end to end.

Samples the real pipeline over a short window and asserts every stage is
producing: streams online, perception inferring, context creating incidents,
and the VLM verifying. Exits 0 (pass) / 1 (fail) with real numbers, so it can
run on demand and on a schedule (a systemd timer) as a continuous self-check.

    uv run python -m bench.golden_path
"""

from __future__ import annotations

import sys
import time
import urllib.request

API = "http://localhost:8010"
PERCEPTION = "http://localhost:8030"
INGEST = "http://localhost:8020"
REASON = "http://localhost:8050"


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as r:
        import json

        return json.loads(r.read())


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    # 1. streams online
    try:
        h = _get(f"{INGEST}/health/summary")
        online = h["online"]
        checks.append(("streams online", online > 0, f"{online}/{h['cameras']} online, {h['aggregate_fps']} fps"))
    except Exception as e:  # noqa: BLE001
        checks.append(("streams online", False, str(e)))

    # 2. perception inferring
    try:
        s = _get(f"{PERCEPTION}/stats")
        active = sum(1 for c in s.get("cameras", []) if c["status"] == "online" and c["fps"] > 0)
        checks.append(("perception inferring", active > 0, f"{active} cameras inferring"))
    except Exception as e:  # noqa: BLE001
        checks.append(("perception inferring", False, str(e)))

    # 3. context creating incidents (count climbs over the window)
    try:
        before = _get(f"{API}/summary")["total_incidents"]
        time.sleep(20)
        after = _get(f"{API}/summary")["total_incidents"]
        checks.append(("context creating incidents", after > before, f"+{after - before} incidents in 20s"))
    except Exception as e:  # noqa: BLE001
        checks.append(("context creating incidents", False, str(e)))

    # 4. VLM verifying
    try:
        r = _get(f"{REASON}/stats")
        checks.append(("VLM verifying", r["verified"] > 0, f"{r['verified']} verified, backend {r['backend']}"))
    except Exception as e:  # noqa: BLE001
        checks.append(("VLM verifying", False, str(e)))

    print("=== GOLDEN-PATH SELF-TEST ===")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:28} {detail}")
    passed = all(ok for _, ok, _ in checks)
    print(f"\n  RESULT: {'PASS - full pipeline healthy end to end' if passed else 'FAIL - a stage is not producing'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
