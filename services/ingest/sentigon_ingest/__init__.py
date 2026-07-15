"""Sentigon ingest service.

ONVIF discovery, resilient RTSP capture (auto-reconnect with backoff), MediaMTX
restream for browser WebRTC/HLS, continuous segmented recording to MinIO, an
in-memory pre-roll ring buffer, and per-stream health published to Redis and the
`ingest.health` Kafka topic.
"""

__version__ = "0.1.0"
