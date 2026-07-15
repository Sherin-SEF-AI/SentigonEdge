#!/usr/bin/env python3
"""Site-speaker stand-in for Live Audio Talk-Down.

A real HTTP audio sink: it receives a WAV over POST /play (as a real ONVIF/RTSP
two-way-audio speaker would), writes it to disk, measures its duration, and records
the "playback". GET /played returns the recent playbacks. The only thing simulated
is the physical loudspeaker; the audio and delivery path are real.

    uv run python scripts/speaker_sink.py            # listens on :8099
"""

from __future__ import annotations

import io
import json
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OUT = Path("/tmp/sentigon-talkdowns")
OUT.mkdir(parents=True, exist_ok=True)
_played: list[dict] = []
_seq = 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: ANN002
        pass

    def do_GET(self) -> None:
        if self.path.startswith("/played"):
            self._json({"count": len(_played), "played": _played[-20:]})
        else:
            self._json({"ok": True, "playbacks": len(_played)})

    def do_POST(self) -> None:
        global _seq
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        camera = self.headers.get("X-Camera", "unknown")
        message = self.headers.get("X-Message", "")
        dur = 0.0
        try:
            with wave.open(io.BytesIO(body)) as w:
                dur = round(w.getnframes() / w.getframerate(), 2)
        except Exception:  # noqa: BLE001
            dur = 0.0
        _seq += 1
        fn = OUT / f"talkdown_{_seq}.wav"
        fn.write_bytes(body)
        rec = {"seq": _seq, "camera": camera, "message": message,
               "duration_s": dur, "bytes": len(body), "file": str(fn)}
        _played.append(rec)
        print(f"PLAYED on {camera}: {dur}s, {len(body)} bytes -> {fn}  msg={message!r}", flush=True)
        self._json({"played": True, **rec})

    def _json(self, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    print("speaker sink listening on :8099 (site-speaker stand-in)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8099), Handler).serve_forever()
