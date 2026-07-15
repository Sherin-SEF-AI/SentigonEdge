"""Sentigon search service.

Natural-language and visual search over the real captured evidence. Incident
snapshots are embedded with CLIP into a shared image/text space (Qdrant); a text
query ("person in a red jacket at the loading dock") is embedded with the CLIP text
encoder and matched by cosine similarity. Results are real incidents with real
snapshots. No synthetic content.
"""

__version__ = "0.1.0"
