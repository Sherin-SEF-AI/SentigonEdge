"""Plate normalization + keyed-HMAC hashing (privacy-sensitive: plates are PII)."""
from __future__ import annotations

import hashlib
import hmac

from sentigon_common.config import settings
from sentigon_common.plates import normalize_plate, plate_hash


def test_normalize_strips_and_uppercases():
    assert normalize_plate("ab-12 34.") == "AB1234"
    assert normalize_plate("  xyz_789  ") == "XYZ789"


def test_hash_is_deterministic_across_ocr_noise():
    assert plate_hash("AB12 34") == plate_hash("ab-1234") == plate_hash("A.B 1234")


def test_hash_is_128_bit_hex():
    h = plate_hash("XYZ789")
    assert len(h) == 32 and all(c in "0123456789abcdef" for c in h)


def test_distinct_plates_distinct_hashes():
    assert plate_hash("AAA111") != plate_hash("BBB222")


def test_hash_is_keyed_hmac_not_plain_sha256():
    # It must be HMAC keyed by anpr_salt, not sha256(salt:plate) — the whole point
    # of the fix. Recompute the HMAC and a plain-prefix sha256 and assert which one.
    expected_hmac = hmac.new(settings.anpr_salt.encode(), b"AB1234", hashlib.sha256).hexdigest()[:32]
    plain_prefix = hashlib.sha256(f"{settings.anpr_salt}:AB1234".encode()).hexdigest()[:32]
    assert plate_hash("AB-1234") == expected_hmac
    assert plate_hash("AB-1234") != plain_prefix
