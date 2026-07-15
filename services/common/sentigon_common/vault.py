"""Tamper-evident evidence vault: an append-only, hash-chained ledger.

Each record stores a content digest (sha256 of the clip/snapshot) and a record hash
that chains content_digest with the previous record hash (blockchain style). The
record hash is what is stored in `content_hash` (unique) and referenced by the next
record's `prev_hash`. This lets identical content appear more than once (the same
frame captured by two events) while still detecting any reordering, deletion, or
mutation of stored evidence. `verify_chain` recomputes every link.
"""
from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db.models import EvidenceRecord
from .logging import get_logger

log = get_logger("vault")

GENESIS_HASH = "0" * 64


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_hash(content_digest: str, prev_hash: str) -> str:
    return hashlib.sha256(f"{content_digest}:{prev_hash}".encode()).hexdigest()


async def append_evidence(
    session: AsyncSession,
    *,
    kind: str,
    data: bytes | None = None,
    content_digest: str | None = None,
    bucket: str | None = None,
    object_key: str | None = None,
    reference_id: uuid.UUID | None = None,
    meta: dict | None = None,
) -> EvidenceRecord:
    """Append one record. Provide raw `data` (digested here) or a precomputed
    `content_digest` (e.g. hashed while streaming a large clip)."""
    if content_digest is None:
        if data is None:
            raise ValueError("append_evidence requires either data or content_digest")
        content_digest = sha256_hex(data)

    last = (
        await session.execute(select(EvidenceRecord).order_by(EvidenceRecord.seq.desc()).limit(1))
    ).scalar_one_or_none()
    prev = last.content_hash if last else GENESIS_HASH
    rhash = record_hash(content_digest, prev)

    record = EvidenceRecord(
        content_hash=rhash,
        prev_hash=prev,
        kind=kind,
        bucket=bucket,
        object_key=object_key,
        reference_id=reference_id,
        meta={**(meta or {}), "content_digest": content_digest},
    )
    session.add(record)
    await session.flush()
    log.info("evidence.appended", kind=kind, record=rhash[:12], prev=prev[:12])
    return record


async def verify_chain(session: AsyncSession) -> tuple[bool, list[dict]]:
    """Walk the chain in seq order; confirm each prev link and recompute each record
    hash from its content digest. Returns (ok, breaks)."""
    rows = (
        (await session.execute(select(EvidenceRecord).order_by(EvidenceRecord.seq.asc())))
        .scalars()
        .all()
    )
    breaks: list[dict] = []
    expected_prev = GENESIS_HASH
    for r in rows:
        digest = (r.meta or {}).get("content_digest")
        if r.prev_hash != expected_prev:
            breaks.append({"seq": r.seq, "reason": "prev_hash_mismatch", "expected_prev": expected_prev, "got_prev": r.prev_hash})
        elif digest and r.content_hash != record_hash(digest, r.prev_hash):
            breaks.append({"seq": r.seq, "reason": "content_tampered", "record": r.content_hash})
        expected_prev = r.content_hash
    return (len(breaks) == 0, breaks)
