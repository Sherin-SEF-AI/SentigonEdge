"""Text-to-speech for Live Audio Talk-Down.

Renders a warning message to a real speech WAV with Piper (offline neural TTS). The
voice model is loaded once, lazily. Used to speak deterrence messages through a
site speaker (or the speaker stand-in in dev).
"""

from __future__ import annotations

import io
import threading
import wave
from pathlib import Path

from .config import settings

_REPO_ROOT = Path(__file__).resolve().parents[3]
_voice = None
_lock = threading.Lock()

# canned deterrence messages an operator can trigger by preset
PRESETS = {
    "warning": "Attention. This area is under active security monitoring. Please leave immediately.",
    "trespass": "You are trespassing on private property. Security has been notified and is responding.",
    "leave": "This is a security announcement. You must leave this restricted area now.",
    "police": "Security has contacted local law enforcement. Officers are on their way. Remain calm.",
}


def _get_voice():
    global _voice
    if _voice is None:
        with _lock:
            if _voice is None:
                from piper import PiperVoice

                _voice = PiperVoice.load(str(_REPO_ROOT / settings.talkdown_voice))
    return _voice


def synth_wav(text: str) -> tuple[bytes, float]:
    """Render `text` to a WAV. Returns (wav_bytes, duration_seconds)."""
    buf = io.BytesIO()
    voice = _get_voice()
    with _lock, wave.open(buf, "wb") as w:
        voice.synthesize_wav(text, w)
    data = buf.getvalue()
    with wave.open(io.BytesIO(data)) as r:
        dur = r.getnframes() / r.getframerate()
    return data, round(dur, 2)
