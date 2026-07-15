"""Real camera onboarding.

Registers the MediaMTX sample streams as cameras through the ingest API, then
creates each camera's zone through the core /zones API. This exercises the real
onboarding path (validation, worker start, ROI creation): no DB fixtures, no
seeded runtime data. The stream itself is the only permitted substitution (a real
video file over the real RTSP relay standing in for camera hardware). Idempotent.

    python scripts/register_cameras.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parents[1]
INGEST = os.environ.get("INGEST_URL", "http://localhost:8020")
API = os.environ.get("API_URL", "http://localhost:8010")


def _load(rel: str) -> dict:
    with open(REPO / rel) as fh:
        return yaml.safe_load(fh)


def main() -> int:
    cams_cfg = _load("configs/cameras/samples.yaml")["cameras"]
    zones_cfg = {z["name"]: z for z in _load("configs/ontology/zones.yaml")["zones"]}

    token = os.environ.get("SERVICE_TOKEN", "dev_service_token_change_me")
    with httpx.Client(timeout=20.0, headers={"X-Service-Token": token}) as c:
        sites = c.get(f"{API}/sites").json()
        if not sites:
            print("no site found; run `make seed` first", file=sys.stderr)
            return 1
        site_id = sites[0]["id"]
        existing_cams = {cam["name"]: cam for cam in c.get(f"{API}/cameras").json()}
        zoned_cams = {z["camera_id"] for z in c.get(f"{API}/zones").json() if z.get("camera_id")}

        for cam in cams_cfg:
            name = cam["name"]
            if name in existing_cams:
                cid = existing_cams[name]["id"]
                print(f"  camera exists: {name} ({cid})")
            else:
                r = c.post(
                    f"{INGEST}/cameras",
                    json={
                        "name": name,
                        "rtsp_uri": cam["rtsp_uri"],
                        "site_id": site_id,
                        "fps": cam.get("fps", 15),
                        "resolution": cam.get("resolution"),
                    },
                )
                r.raise_for_status()
                cid = r.json()["id"]
                print(f"  registered camera: {name} -> {cid}")

            zname = cam.get("zone")
            if zname and zname in zones_cfg and cid not in zoned_cams:
                z = zones_cfg[zname]
                zr = c.post(
                    f"{API}/zones",
                    json={
                        "name": z["name"],
                        "zone_type": z["zone_type"],
                        "camera_id": cid,
                        "site_id": site_id,
                        "polygon": z.get("polygon", []),
                        "max_occupancy": z.get("max_occupancy"),
                    },
                )
                zr.raise_for_status()
                print(f"    created zone: {z['name']} ({z['zone_type']}) on {name}")
    print("onboarding complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
