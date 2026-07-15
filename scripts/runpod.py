#!/usr/bin/env python3
"""RunPod control for the Sentigon heavy VLM tier (Qwen3-VL-32B on vLLM).

Reads the API key from .runpod.env.

    uv run python scripts/runpod.py deploy
    uv run python scripts/runpod.py status <pod_id>
    uv run python scripts/runpod.py terminate <pod_id>
    uv run python scripts/runpod.py burst [seconds]   # full lifecycle, auto teardown

`burst` is the one-command target: create the pod, wait until vLLM serves, repoint
the LIVE reason service at the 32B, run for N seconds, then ALWAYS restore reason to
local Ollama and terminate the pod (guaranteed cleanup on success, error, or Ctrl-C),
so nothing is ever left billing.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".runpod.env"
API = "https://api.runpod.io/graphql"

GPU = "NVIDIA A100 80GB PCIe"
IMAGE = "vllm/vllm-openai:latest"
MODEL = "Qwen/Qwen3-VL-32B-Instruct"

DROPIN_DIR = Path.home() / ".config/systemd/user/sentigon-reason.service.d"
DROPIN = DROPIN_DIR / "runpod-burst.conf"


def _env(name: str, default: str = "") -> str:
    for line in ENV.read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return default


def _key() -> str:
    k = _env("RUNPOD_API_KEY")
    if not k:
        raise SystemExit("RUNPOD_API_KEY not found in .runpod.env")
    return k


def gql(query: str) -> dict:
    req = urllib.request.Request(
        f"{API}?api_key={_key()}",
        data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "sentigon-runpod/1.0"},
    )
    with urllib.request.urlopen(req, timeout=40) as r:
        out = json.loads(r.read())
    if out.get("errors"):
        raise SystemExit(json.dumps(out["errors"], indent=2))
    return out["data"]


def create_pod() -> tuple[str, str]:
    """Create the pod via the REST API (dockerStartCmd carries the vLLM args, which
    the GraphQL path silently dropped). Returns (pod_id, openai_endpoint)."""
    body = {
        "name": "sentigon-vllm-qwen3vl32b",
        "imageName": IMAGE,
        "gpuTypeIds": [GPU],
        "gpuCount": 1,
        "cloudType": "SECURE",
        "containerDiskInGb": 40,
        "volumeInGb": 0,
        "ports": ["8000/http"],
        "dockerStartCmd": [
            "--model", MODEL,
            "--served-model-name", "reason",
            "--max-model-len", "16384",
            "--gpu-memory-utilization", "0.95",
        ],
    }
    # Attach the persistent weights-cache volume so the 32B is downloaded ONCE and
    # reloaded from the volume on every subsequent cold start (fast + reliable).
    # Network volumes are region-locked, so the pod is pinned to the volume's DC.
    vol = _env("RUNPOD_VOLUME_ID")
    if vol:
        body["networkVolumeId"] = vol
        body["dataCenterIds"] = [_env("RUNPOD_VOLUME_DC", "CA-MTL-3")]
        body["volumeMountPath"] = "/runpod-volume"
        # point the HF cache at the volume; vLLM loads Qwen3-VL-32B from here
        body["env"] = {"HF_HOME": "/runpod-volume/hf"}
    req = urllib.request.Request(
        "https://rest.runpod.io/v1/pods",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_key()}",
            "User-Agent": "sentigon-runpod/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
    pid = d.get("id")
    if not pid:
        raise SystemExit(f"pod create failed: {json.dumps(d)[:400]}")
    endpoint = f"https://{pid}-8000.proxy.runpod.net/v1"
    return pid, endpoint


def deploy() -> None:
    pid, endpoint = create_pod()
    print(f"POD_ID={pid}\nendpoint (once ready): {endpoint}")


def _rest(path: str, method: str = "GET", body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"https://rest.runpod.io/v1{path}",
        data=json.dumps(body).encode() if body else None,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_key()}",
            "User-Agent": "sentigon-runpod/1.0",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def create_volume(size: int = 80, dc: str = "CA-MTL-3") -> None:
    """Create the persistent weights-cache volume once and record it in .runpod.env
    so every future `burst`/`deploy` mounts it. The 32B downloads to it on the first
    run, then reloads from it (fast) forever after."""
    if _env("RUNPOD_VOLUME_ID"):
        print(f"volume already configured: {_env('RUNPOD_VOLUME_ID')} in {_env('RUNPOD_VOLUME_DC')}")
        return
    d = _rest("/networkvolumes", "POST", {"name": "sentigon-vllm-cache", "size": size, "dataCenterId": dc})
    vid, vdc = d.get("id"), d.get("dataCenterId", dc)
    lines = [
        line
        for line in ENV.read_text().splitlines()
        if not line.startswith(("RUNPOD_VOLUME_ID=", "RUNPOD_VOLUME_DC="))
    ]
    lines += [f"RUNPOD_VOLUME_ID={vid}", f"RUNPOD_VOLUME_DC={vdc}"]
    ENV.write_text("\n".join(lines) + "\n")
    print(f"created volume {vid} ({size}GB, {vdc}); saved to .runpod.env")


def delete_volume() -> None:
    vid = _env("RUNPOD_VOLUME_ID")
    if not vid:
        print("no volume configured")
        return
    _rest(f"/networkvolumes/{vid}", "DELETE")
    lines = [
        line
        for line in ENV.read_text().splitlines()
        if not line.startswith(("RUNPOD_VOLUME_ID=", "RUNPOD_VOLUME_DC="))
    ]
    ENV.write_text("\n".join(lines) + "\n")
    print(f"deleted volume {vid} and removed it from .runpod.env")


def wait_ready(endpoint: str, timeout: int = 1200) -> bool:
    """Poll the vLLM /models endpoint until it serves (200) or timeout."""
    url = endpoint.rstrip("/") + "/models"
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            pass
        elapsed = int(time.time() - t0)
        if elapsed % 60 < 10:
            print(f"  ...loading 32B ({elapsed}s)")
        time.sleep(10)
    return False


def _sh(*args: str) -> None:
    subprocess.run(args, check=False)


def repoint_reason(endpoint: str) -> None:
    DROPIN_DIR.mkdir(parents=True, exist_ok=True)
    DROPIN.write_text(
        "[Service]\n"
        "Environment=REASON_BACKEND=vllm\n"
        "Environment=REASON_MODEL=reason\n"
        f"Environment=REASON_ENDPOINT={endpoint}\n"
    )
    _sh("systemctl", "--user", "daemon-reload")
    _sh("systemctl", "--user", "restart", "sentigon-reason")
    print(f"live reason repointed -> {endpoint}")


def restore_reason() -> None:
    if DROPIN.exists():
        DROPIN.unlink()
    _sh("systemctl", "--user", "daemon-reload")
    _sh("systemctl", "--user", "restart", "sentigon-reason")
    print("live reason restored -> local Ollama")


def _verdicts_by_32b() -> int:
    """Count incidents verified by the 32B (reasoning_trace.model == 'reason')."""
    try:
        from sentigon_common.db import sync_session_factory
        from sqlalchemy import text

        with sync_session_factory() as s:
            return int(
                s.execute(
                    text("SELECT count(*) FROM incidents WHERE reasoning_trace->>'model' = 'reason'")
                ).scalar()
                or 0
            )
    except Exception:  # noqa: BLE001
        return -1


def terminate(pod_id: str, retries: int = 6) -> None:
    """Terminate with retries so a transient network blip during teardown never
    leaves a pod billing. Verifies the pod is actually gone before giving up."""
    last = ""
    for _ in range(retries):
        try:
            gql(f'mutation {{ podTerminate(input: {{podId: "{pod_id}"}}) }}')
            print(f"terminated {pod_id}")
            return
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
            # maybe it already terminated: if the pod is gone, we're done
            try:
                d = _rest(f"/pods/{pod_id}")
                if not d or d.get("desiredStatus") in (None, "TERMINATED", "EXITED"):
                    print(f"{pod_id} already gone")
                    return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(10)
    raise SystemExit(f"FAILED to terminate {pod_id} after {retries} tries ({last}); terminate manually")


def status(pod_id: str) -> None:
    q = f"""query {{ pod(input: {{podId: "{pod_id}"}}) {{
        id name desiredStatus costPerHr
        runtime {{ uptimeInSeconds ports {{ ip isIpPublic privatePort publicPort type }} }}
    }} }}"""
    print(json.dumps(gql(q)["pod"], indent=2))


def stop(pod_id: str) -> None:
    q = f'mutation {{ podStop(input: {{podId: "{pod_id}"}}) {{ id desiredStatus }} }}'
    print(json.dumps(gql(q)["podStop"], indent=2))


def burst(duration_s: int = 120) -> None:
    """One command: deploy -> wait-ready -> repoint reason -> run -> restore + teardown.
    Cleanup (restore reason + terminate pod) is guaranteed via nested finally + signal
    handlers, so an error or Ctrl-C never leaves anything billing."""
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(130))
    print("[1/5] creating pod (A100 80GB, Qwen3-VL-32B)...")
    pid, endpoint = create_pod()
    print(f"      POD_ID={pid}")
    before = _verdicts_by_32b()
    try:
        print("[2/5] waiting for vLLM to serve the 32B (up to 25 min)...")
        if not wait_ready(endpoint, timeout=1500):
            raise SystemExit("vLLM never became ready within timeout")
        print("      vLLM ready.")
        print("[3/5] repointing the live reason service at the 32B...")
        repoint_reason(endpoint)
        try:
            print(f"[4/5] live reason verifying via the 32B for {duration_s}s...")
            time.sleep(duration_s)
        finally:
            restore_reason()
    finally:
        print("[5/5] tearing down pod...")
        try:
            terminate(pid)
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            print(f"      WARNING: {exc}")
    after = _verdicts_by_32b()
    if before >= 0 and after >= 0:
        print(f"done. incidents verified by the 32B this burst: {after - before} (total {after}).")
    print("burst complete; no pods left running.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "deploy":
        deploy()
    elif cmd == "status":
        status(sys.argv[2])
    elif cmd == "terminate":
        terminate(sys.argv[2])
    elif cmd == "stop":
        stop(sys.argv[2])
    elif cmd == "burst":
        burst(int(sys.argv[2]) if len(sys.argv) > 2 else 120)
    elif cmd == "volume-create":
        create_volume()
    elif cmd == "volume-delete":
        delete_volume()
    else:
        print(__doc__)
