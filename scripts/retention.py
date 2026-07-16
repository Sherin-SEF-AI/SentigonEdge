#!/usr/bin/env python3
"""Data-retention / cleanup job (cron-friendly).

Prunes, past a retention window:
  - CLOSED incidents (resolved / false_positive) and their events
  - the MinIO snapshot objects those events referenced
  - orphan events (no incident) and old audit-log rows

Deliberately NOT pruned: open/ack/escalated incidents (active), and the
tamper-evident evidence ledger (compliance / chain integrity).

Dry-run by default; pass --apply to actually delete.

    uv run python scripts/retention.py --days 30            # preview
    uv run python scripts/retention.py --days 30 --apply    # execute
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from sentigon_common.db import sync_session_factory
from sentigon_common.db.models import AuditLogEntry, Event, Incident
from sentigon_common.logging import configure_logging, get_logger
from sentigon_common.schemas.enums import IncidentStatus
from sentigon_common.storage import get_store
from sqlalchemy import exists, select

log = get_logger("retention")
CLOSED = (IncidentStatus.RESOLVED, IncidentStatus.FALSE_POSITIVE)


def _remove_object(ref: str | None) -> bool:
    if not ref or "/" not in ref:
        return False
    bucket, key = ref.split("/", 1)
    try:
        get_store().remove(bucket, key)
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    configure_logging("retention")
    ap = argparse.ArgumentParser(description="Sentigon retention / cleanup")
    ap.add_argument("--days", type=int, default=30, help="retention window in days")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    args = ap.parse_args()

    cutoff = datetime.now(UTC) - timedelta(days=args.days)
    audit_cutoff = datetime.now(UTC) - timedelta(days=args.days * 2)
    counts = {"incidents": 0, "events": 0, "snapshots": 0, "audit": 0}

    with sync_session_factory() as session:
        # closed incidents older than the window
        closed = session.execute(
            select(Incident).where(Incident.status.in_(CLOSED), Incident.created_at < cutoff)
        ).scalars().all()
        for inc in closed:
            counts["incidents"] += 1
            if args.apply:
                session.delete(inc)

        # An event is safe to delete only when it is past the window AND no still-active
        # (open / ack / escalated) incident references it. This prunes events of the
        # closed incidents above and genuine orphans, but NEVER an event whose incident
        # is still open — the previous `OR created_at < cutoff` deleted those too,
        # silently destroying evidence for active investigations.
        referenced_by_active = exists().where(
            (Incident.event_id == Event.id) & Incident.status.not_in(CLOSED)
        )
        events = session.execute(
            select(Event).where(Event.created_at < cutoff, ~referenced_by_active)
        ).scalars().all()
        for ev in events:
            counts["events"] += 1
            if args.apply and _remove_object(ev.snapshot_ref):
                counts["snapshots"] += 1
            if args.apply:
                session.delete(ev)

        # old audit rows (kept twice as long as incidents)
        audit = session.execute(
            select(AuditLogEntry).where(AuditLogEntry.ts < audit_cutoff)
        ).scalars().all()
        for a in audit:
            counts["audit"] += 1
            if args.apply:
                session.delete(a)

        if args.apply:
            session.commit()

    mode = "DELETED" if args.apply else "would delete (dry-run)"
    print(f"retention window: {args.days} days (cutoff {cutoff.date()})")
    print(f"  {mode}: {counts['incidents']} closed incidents")
    print(f"  {mode}: {counts['events']} events")
    print(f"  {mode}: {counts['snapshots']} snapshot objects (MinIO)")
    print(f"  {mode}: {counts['audit']} audit rows (>{args.days * 2}d)")
    print("  preserved: open/ack/escalated incidents + evidence ledger")
    log.info("retention.done", apply=args.apply, **counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
