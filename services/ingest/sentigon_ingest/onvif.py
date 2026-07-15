"""ONVIF device discovery via WS-Discovery multicast.

Real multicast probe on the LAN. On a dev box with no ONVIF cameras this returns
an empty list, which is the honest result: nothing is fabricated. When real
cameras are present it returns their device service endpoints for onboarding.
"""

from __future__ import annotations

import asyncio

from sentigon_common.logging import get_logger

log = get_logger("onvif")

_ONVIF_TYPE = "tdn:NetworkVideoTransmitter"


def _discover_blocking(timeout: float) -> list[dict]:
    from wsdiscovery.discovery import ThreadedWSDiscovery
    from wsdiscovery.scope import Scope

    wsd = ThreadedWSDiscovery()
    wsd.start()
    try:
        services = wsd.searchServices(
            types=[Scope("http://www.onvif.org/ver10/network/wsdl")],
            timeout=int(max(1, timeout)),
        )
        out: list[dict] = []
        for svc in services:
            out.append(
                {
                    "epr": svc.getEPR(),
                    "xaddrs": list(svc.getXAddrs()),
                    "scopes": [s.getValue() for s in svc.getScopes()],
                    "types": [str(t) for t in svc.getTypes()],
                }
            )
        return out
    finally:
        wsd.stop()


async def discover(timeout: float = 4.0) -> list[dict]:
    """Run the blocking WS-Discovery probe off the event loop."""
    try:
        devices = await asyncio.to_thread(_discover_blocking, timeout)
        log.info("onvif.discovered", count=len(devices))
        return devices
    except Exception as exc:  # noqa: BLE001
        log.warning("onvif.discover_failed", error=str(exc))
        return []
