"""Sentigon core API.

REST over the governed state: incidents (triage), zones (ROI CRUD for the editor),
signatures (library + toggles + open-vocab), events, and summary analytics. The
console reads and writes here; ingest and perception own the live stream/detection
surfaces.
"""

__version__ = "0.1.0"
