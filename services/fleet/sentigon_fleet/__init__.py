"""Sentigon fleet service.

Infrastructure and camera-health diagnostics. A periodic engine collects raw
telemetry (camera health from the DB, service /healthz + /stats probes, and host
metrics read straight from the kernel with no extra deps), runs a rule engine
over it, and reconciles the resulting findings into the fleet_findings table
(dedup on kind+target, auto-resolve when a condition clears). Each pass also
persists a fleet_snapshots rollup for history. Read-only endpoints expose the
latest overview, per-target detail, and active findings.
"""

__version__ = "0.1.0"
