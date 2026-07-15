"""Sentigon context service.

A stateful stream processor over `perception.objects`. It maintains per-track and
per-zone temporal windows (dwell, occupancy, entry counts, speed) and evaluates the
signature library as pure functions over that state, never on a single frame. It
emits candidate events to `events.candidate`, persists Event + Incident rows, and
attaches a snapshot pulled from the ingest ring buffer. Signatures hot-reload from
Postgres.
"""

__version__ = "0.1.0"
