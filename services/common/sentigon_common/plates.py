"""Number-plate normalization + salted hashing.

Plate text is personal data. Both the reader (perception) and the enroller (api)
normalize and hash identically here, so a watchlisted plate matches a live read
without either side ever needing to store the raw string (DPDP-style handling).
"""

from __future__ import annotations

import hashlib
import hmac
import re

from .config import settings

_ALNUM = re.compile(r"[^A-Z0-9]")


def normalize_plate(raw: str) -> str:
    """Uppercase and strip to A-Z0-9 (OCR often adds spaces/dashes/dots)."""
    return _ALNUM.sub("", raw.upper())


def plate_hash(text: str) -> str:
    """Keyed HMAC-SHA256 (truncated) of a normalized plate, for storage/matching.

    ``anpr_salt`` is the secret HMAC KEY, not a public prefix: with a plain
    ``sha256(salt:plate)`` an attacker who learns the (committed-by-default) salt
    reverses the whole small plate keyspace in seconds. As an HMAC key the salt must
    stay secret and non-default — the production settings guard rejects the default.
    Both perception (reader) and api (enroller) call this, so hashes match."""
    return hmac.new(
        settings.anpr_salt.encode(), normalize_plate(text).encode(), hashlib.sha256
    ).hexdigest()[:32]
