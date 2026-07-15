"""Sentigon evaluation and benchmark harness.

- eval_harness: precision/recall/false-alarm-rate per signature over a labeled gold
  set. First-class per the spec: no signature ships without a gold-set measurement.
- latency_harness: end-to-end and per-stage latency against the SLOs.
- metrics: the scoring primitives shared by both.
"""
