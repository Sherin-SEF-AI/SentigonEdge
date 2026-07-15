"""Chaos test: prove at-least-once event durability across a consumer kill.

Produces N numbered messages to a Kafka topic, then consumes them with the real
run_consumer helper. The orchestrator kills the consumer mid-batch and restarts
it. Because offsets commit only after a handler succeeds, the in-flight and
uncommitted messages replay on restart, so the UNION of processed ids must equal
the full set {0..N-1}: zero loss.

    python -m bench.chaos_durability produce 100
    python -m bench.chaos_durability consume        # run under systemd/kill

Orchestrated by scripts/chaos_test.sh.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import orjson
from aiokafka import AIOKafkaProducer
from sentigon_common.config import settings
from sentigon_common.kafka import ensure_topics, run_consumer

TOPIC = "chaos.durability"
LOG = Path("/tmp/chaos_processed.log")


async def produce(n: int) -> None:
    await ensure_topics([TOPIC])
    p = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap)
    await p.start()
    try:
        for i in range(n):
            await p.send_and_wait(TOPIC, orjson.dumps({"id": i}))
    finally:
        await p.stop()
    print(f"produced {n} messages to {TOPIC}")


async def consume() -> None:
    async def handler(payload: dict, _cid: str | None) -> None:
        # slow enough that an external kill lands mid-batch; record id as processed
        await asyncio.sleep(0.05)
        with LOG.open("a") as f:
            f.write(f"{payload['id']}\n")

    # runs until the orchestrator kills it (simulating a crash / a systemd stop)
    await run_consumer([TOPIC], "chaos-durability", handler, auto_offset_reset="earliest")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "produce":
        LOG.unlink(missing_ok=True)
        asyncio.run(produce(int(sys.argv[2]) if len(sys.argv) > 2 else 100))
    elif mode == "consume":
        asyncio.run(consume())
    else:
        print(__doc__)


if __name__ == "__main__":
    _ = time
    main()
