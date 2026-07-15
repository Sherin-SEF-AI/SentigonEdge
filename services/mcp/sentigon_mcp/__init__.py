"""Sentigon MCP server.

Exposes Sentigon's incident triage, semantic evidence search, and evidence-chain
verification as Model Context Protocol tools, so an external agent can query the
security platform: list incidents, search video by natural language, pull an
incident's VLM verdict + SITREP, and verify the tamper-evident evidence chain.
"""

__version__ = "0.1.0"
