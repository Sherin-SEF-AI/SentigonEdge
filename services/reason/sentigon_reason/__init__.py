"""Sentigon Reason service (the Pulsar analog).

Escalates high-severity candidate events to a Vision-Language Model. It pulls the
event-time frame plus a follow-up frame, builds a structured prompt (signature,
zone semantics, time, object context), and asks the VLM for a verdict
(confirmed / rejected / unverified), a natural-language SITREP, a reasoning trace,
and structured attributes. The verdict updates the Incident and publishes to
incidents.verified. This is the primary false-alarm-reduction mechanism.

Local backend: qwen2.5vl:7b via Ollama (fits the 16 GB dev GPU). RunPod backend:
Qwen3-VL-32B via vLLM. Same OpenAI-compatible contract, config-switched.
"""

__version__ = "0.1.0"
