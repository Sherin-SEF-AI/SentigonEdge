"""Strip credentials from URLs before returning them to clients or logging them.

RTSP/ONVIF URIs are frequently ``rtsp://user:pass@host/path``; those credentials
must never reach the browser or the logs. The console only needs the host/path.
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def redact_url_credentials(url: str | None) -> str | None:
    """Return `url` with any userinfo (user:pass@) replaced by ``***@``. Safe on
    None / non-URL strings (returns them unchanged)."""
    if not url:
        return url
    try:
        parts = urlsplit(url)
        if not (parts.username or parts.password):
            return url
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        netloc = f"***@{host}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:  # noqa: BLE001
        return url
