"""The fleet rule engine.

`evaluate` turns a raw telemetry snapshot (cameras + services + host) into a flat
list of finding dicts. It is a pure function of its inputs (plus the configured
thresholds) and is defensive about missing keys so a half-populated health blob
never crashes a pass. Findings are dedup/reconciled downstream by the engine on
(kind, target_id).
"""

from __future__ import annotations

from datetime import UTC, datetime

from .config import settings

# Services whose absence is a critical outage (the perception pipeline core).
_CORE_SERVICES = {"api", "ingest", "perception", "context", "reason"}


def _parse_ts(value: object) -> datetime | None:
    """Parse an ISO timestamp to a tz-aware datetime (assume UTC if naive)."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _num(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _finding(
    kind: str,
    severity: str,
    target_type: str,
    target_id: str | None,
    target_name: str | None,
    detail: str,
    metric: dict | None = None,
    recommended_action: str | None = None,
    site_id: str | None = None,
) -> dict:
    return {
        "kind": kind,
        "severity": severity,
        "target_type": target_type,
        "target_id": target_id,
        "target_name": target_name,
        "detail": detail,
        "metric": metric or {},
        "recommended_action": recommended_action,
        "site_id": site_id,
    }


def _eval_cameras(cameras: list[dict], now: datetime) -> list[dict]:
    findings: list[dict] = []
    for cam in cameras:
        cam_id = cam.get("id")
        name = cam.get("name") or cam_id
        site_id = cam.get("site_id")
        status = cam.get("status") or "offline"
        health = cam.get("health") or {}
        target_fps = _num(cam.get("target_fps")) or 0.0
        last_seen = _parse_ts(cam.get("last_seen"))

        age = None if last_seen is None else (now - last_seen).total_seconds()
        stale = age is None or age > settings.camera_stale_seconds
        offline = status != "online" or stale

        if offline:
            if last_seen is None:
                reason = "never reported in"
            elif stale:
                reason = f"last seen {int(age)}s ago"
            else:
                reason = f"status is {status}"
            findings.append(
                _finding(
                    kind="camera_offline",
                    severity="high",
                    target_type="camera",
                    target_id=cam_id,
                    target_name=name,
                    detail=f"Camera '{name}' is offline ({reason}).",
                    metric={
                        "status": status,
                        "last_seen": cam.get("last_seen"),
                        "stale_seconds": None if age is None else round(age, 1),
                    },
                    recommended_action="Check power/network/RTSP for this camera.",
                    site_id=site_id,
                )
            )
            # An offline camera can't meaningfully be "low fps" or "unstable" — its
            # other findings are allowed to auto-resolve while it is down.
            continue

        # Online path.
        fps = _num(health.get("fps"))
        if target_fps > 0 and fps is not None and fps < settings.low_fps_ratio * target_fps:
            findings.append(
                _finding(
                    kind="low_fps",
                    severity="medium",
                    target_type="camera",
                    target_id=cam_id,
                    target_name=name,
                    detail=(
                        f"Camera '{name}' running at {fps:.1f} fps "
                        f"(target {target_fps:.0f})."
                    ),
                    metric={"fps": fps, "target": target_fps},
                    recommended_action="Stream underperforming; check bitrate/decoder/CPU.",
                    site_id=site_id,
                )
            )

        decode_errors = _num(health.get("decode_errors")) or 0.0
        reconnects = _num(health.get("reconnects")) or 0.0
        if decode_errors > 50 or reconnects > 5:
            findings.append(
                _finding(
                    kind="stream_unstable",
                    severity="medium",
                    target_type="camera",
                    target_id=cam_id,
                    target_name=name,
                    detail=(
                        f"Camera '{name}' stream unstable: "
                        f"{int(decode_errors)} decode errors, {int(reconnects)} reconnects."
                    ),
                    metric={"decode_errors": decode_errors, "reconnects": reconnects},
                    recommended_action="Investigate network loss or a flaky encoder.",
                    site_id=site_id,
                )
            )
    return findings


def _eval_services(services: list[dict]) -> list[dict]:
    findings: list[dict] = []
    for svc in services:
        name = svc.get("name")
        if svc.get("up"):
            continue
        core = name in _CORE_SERVICES
        findings.append(
            _finding(
                kind="service_down",
                severity="critical" if core else "high",
                target_type="service",
                target_id=name,
                target_name=name,
                detail=f"Service '{name}' is not responding ({svc.get('detail', 'down')}).",
                metric={"latency_ms": svc.get("latency_ms"), "core": core},
                recommended_action=f"Restart the service: systemctl --user restart sentigon-{name}",
            )
        )
    return findings


def _eval_perception(services: list[dict]) -> list[dict]:
    findings: list[dict] = []
    perception = next(
        (s for s in services if s.get("name") == "perception" and s.get("up")), None
    )
    if not perception:
        return findings
    stats = perception.get("stats")
    if not isinstance(stats, dict):
        return findings

    device = str(stats.get("device") or "").lower()
    if device == "cpu":
        findings.append(
            _finding(
                kind="perception_cpu",
                severity="medium",
                target_type="service",
                target_id="perception",
                target_name="perception",
                detail="Perception running on CPU (slow).",
                metric={"device": device},
                recommended_action="Enable the CUDA/TensorRT build so inference uses the GPU.",
            )
        )

    cam_stats = stats.get("cameras")
    worst = None
    if isinstance(cam_stats, list):
        for cam in cam_stats:
            if not isinstance(cam, dict):
                continue
            infer = _num(cam.get("inference_ms"))
            if infer is not None and (worst is None or infer > worst):
                worst = infer
    if worst is not None and worst > 500:
        findings.append(
            _finding(
                kind="high_inference_latency",
                severity="medium",
                target_type="service",
                target_id="perception",
                target_name="perception",
                detail=f"Perception inference latency is high ({worst:.0f} ms).",
                metric={"max_inference_ms": worst},
                recommended_action="Reduce model size/resolution or move perception to the GPU.",
            )
        )
    return findings


def _eval_host(host: dict) -> list[dict]:
    findings: list[dict] = []

    disk_pct = _num(host.get("disk_pct"))
    if disk_pct is not None and disk_pct >= settings.disk_pct_warn:
        findings.append(
            _finding(
                kind="disk_pressure",
                severity="critical" if disk_pct >= 95 else "high",
                target_type="host",
                target_id="host",
                target_name="host",
                detail=f"Disk usage at {disk_pct:.0f}%.",
                metric={"disk_pct": disk_pct},
                recommended_action="Free disk or prune recordings.",
            )
        )

    mem_pct = _num(host.get("mem_pct"))
    if mem_pct is not None and mem_pct >= settings.mem_pct_warn:
        findings.append(
            _finding(
                kind="mem_pressure",
                severity="medium",
                target_type="host",
                target_id="host",
                target_name="host",
                detail=f"Memory usage at {mem_pct:.0f}%.",
                metric={"mem_pct": mem_pct},
                recommended_action="Investigate memory use; consider restarting heavy services.",
            )
        )
    return findings


def evaluate(cameras: list[dict], services: list[dict], host: dict) -> list[dict]:
    """Run every rule over a telemetry snapshot and return the produced findings."""
    now = datetime.now(UTC)
    findings: list[dict] = []
    findings.extend(_eval_cameras(cameras or [], now))
    findings.extend(_eval_services(services or []))
    findings.extend(_eval_perception(services or []))
    findings.extend(_eval_host(host or {}))
    return findings
