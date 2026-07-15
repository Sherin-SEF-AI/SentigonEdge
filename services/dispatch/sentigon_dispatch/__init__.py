"""Sentigon dispatch service.

Consumes VLM-confirmed incidents from incidents.verified and, for high/critical
severity, opens a responder Dispatch: resolves the on-call responder from the
roster, notifies them through the real notify transports (email/webhook/webpush),
and tracks acknowledge/resolve SLAs. A background sweeper escalates ack-SLA
breaches up the on-call tier and expires resolve-SLA breaches. Operators drive the
lifecycle (ack/resolve/assign) and manage responders, on-call shifts, and their own
SOC monitoring shifts through the HTTP API.
"""

__version__ = "0.1.0"
