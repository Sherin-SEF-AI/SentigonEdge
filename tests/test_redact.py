"""URL credential redaction (RTSP/ONVIF URIs must never reach the browser/logs)."""
from __future__ import annotations

from sentigon_common.redact import redact_url_credentials
from sentigon_common.schemas.entities import CameraOut


def test_strips_userinfo():
    assert redact_url_credentials("rtsp://admin:s3cr3t@10.0.0.5:554/stream") == "rtsp://***@10.0.0.5:554/stream"
    assert redact_url_credentials("rtsp://user:pass@cam.local/h264") == "rtsp://***@cam.local/h264"


def test_leaves_credential_free_urls_untouched():
    assert redact_url_credentials("rtsp://10.0.0.5:554/stream") == "rtsp://10.0.0.5:554/stream"
    assert redact_url_credentials("http://mediamtx:8889/cam_1/whep") == "http://mediamtx:8889/cam_1/whep"


def test_safe_on_none_and_junk():
    assert redact_url_credentials(None) is None
    assert redact_url_credentials("") == ""
    assert redact_url_credentials("not a url") == "not a url"


def test_camera_out_schema_redacts_on_serialization():
    cam = CameraOut(
        id="11111111-1111-1111-1111-111111111111",
        name="Dock",
        rtsp_uri="rtsp://admin:hunter2@10.0.0.9:554/live",
        onvif_uri="http://admin:hunter2@10.0.0.9/onvif",
        fps=15,
        ptz_capable=False,
        status="online",
        is_active=True,
    )
    assert "hunter2" not in cam.rtsp_uri
    assert cam.rtsp_uri == "rtsp://***@10.0.0.9:554/live"
    assert "hunter2" not in (cam.onvif_uri or "")
