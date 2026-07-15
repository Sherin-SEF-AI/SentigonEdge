"""Number-plate normalization + salted hashing.

Plate text is personal data. Both the reader (perception) and the enroller (api)
normalize and hash identically here, so a watchlisted plate matches a live read
without either side ever needing to store the raw string (DPDP-style handling).
"""

from __future__ import annotations

import hashlib
import re

from .config import settings

_ALNUM = re.compile(r"[^A-Z0-9]")


def normalize_plate(raw: str) -> str:
    """Uppercase and strip to A-Z0-9 (OCR often adds spaces/dashes/dots)."""
    return _ALNUM.sub("", raw.upper())


def plate_hash(text: str) -> str:
    """Salted SHA-256 (truncated) of a normalized plate, for storage/matching."""
    return hashlib.sha256(f"{settings.anpr_salt}:{normalize_plate(text)}".encode()).hexdigest()[:32]
