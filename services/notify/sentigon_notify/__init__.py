"""Sentigon notify service.

Consumes VLM-confirmed incidents from incidents.verified and delivers them through
real transports: SMTP email (to a real inbox) and webhooks (to a real receiver).
SMS and web-push are real adapters that stay unconfigured (and never send) until
real provider credentials are supplied, per the reality directive: a real message
really reaches its destination, or the channel is not implemented.
"""

__version__ = "0.1.0"
