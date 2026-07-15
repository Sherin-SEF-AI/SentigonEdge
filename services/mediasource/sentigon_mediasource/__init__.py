"""Sentigon media-source service.

Acquires real, publicly available, licensed footage from the internet (surveillance
datasets, live public HLS/RTSP, or YouTube via yt-dlp) and restreams it to MediaMTX
as live RTSP cameras. Verifies real liveness (codec/fps via ffprobe, path-ready via
the MediaMTX API) before use, registers each feed as a real Camera + Zone through
the API onboarding path, and auto-reconnects. This is the permitted hardware
substitution: a real feed over the real RTSP path. Everything downstream is real.
"""

__version__ = "0.1.0"
