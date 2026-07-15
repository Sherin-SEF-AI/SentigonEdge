"""Model governance: champion-challenger evaluation and promotion.

A challenger is promoted to champion only on a measured win over the current
champion on the gold set. Shadow serving on live traffic lands in Phase 6; the
evaluation and promotion mechanics live here and are backed by the
ModelVersion / EvalRun tables in the shared schema.
"""

__version__ = "0.1.0"
