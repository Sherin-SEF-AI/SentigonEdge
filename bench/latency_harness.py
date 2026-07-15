"""Infrastructure latency benchmark.

    python -m bench.latency_harness [--n 100]

Measures the substrate latencies the pipeline is built on: a DB round-trip, a
Kafka publish-to-consume round-trip, and a MinIO put/get. These are the real
floor under the SLOs (stream-to-detection < 150 ms, event-to-operator < 500 ms,
VLM verify < 3 s p95); the per-stage pipeline latencies land as those stages ship.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
import uuid

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from sentigon_common.config import settings
from sentigon_common.db.session import get_async_engine
from sentigon_common.kafka import ensure_topics
from sentigon_common.logging import configure_logging, get_logger
from sentigon_common.storage import get_store
from sqlalchemy import text

log = get_logger("latency")
_BENCH_TOPIC = "bench.latency"


def _pctl(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def _report(name: str, samples: list[float]) -> dict:
    d = {
        "op": name,
        "n": len(samples),
        "p50_ms": round(statistics.median(samples), 2) if samples else 0.0,
        "p95_ms": round(_pctl(samples, 95), 2),
        "p99_ms": round(_pctl(samples, 99), 2),
        "max_ms": round(max(samples), 2) if samples else 0.0,
    }
    print(
        f"  {name:22s} n={d['n']:<4d} p50={d['p50_ms']:>7.2f}ms  p95={d['p95_ms']:>7.2f}ms  max={d['max_ms']:>7.2f}ms"
    )
    return d


async def bench_db(n: int) -> list[float]:
    samples: list[float] = []
    engine = get_async_engine()
    async with engine.connect() as conn:
        for _ in range(n):
            t0 = time.perf_counter()
            await conn.execute(text("SELECT 1"))
            samples.append((time.perf_counter() - t0) * 1000)
    return samples


async def bench_kafka(n: int) -> list[float]:
    await ensure_topics([_BENCH_TOPIC])
    producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap, acks="all")
    consumer = AIOKafkaConsumer(
        _BENCH_TOPIC,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=f"bench-{uuid.uuid4().hex[:8]}",
        auto_offset_reset="latest",
        enable_auto_commit=False,
    )
    await producer.start()
    await consumer.start()
    samples: list[float] = []
    try:
        # prime partition assignment so the first send is not counted as a cold start
        await consumer.seek_to_end()
        for i in range(n):
            t0 = time.perf_counter()
            await producer.send_and_wait(_BENCH_TOPIC, orjson.dumps({"i": i, "t": t0}))
            await consumer.getone()
            samples.append((time.perf_counter() - t0) * 1000)
    finally:
        await consumer.stop()
        await producer.stop()
    return samples


async def bench_minio(n: int) -> list[float]:
    store = get_store()
    store.ensure_buckets([settings.minio_bucket_snapshots])
    payload = b"x" * 4096
    samples: list[float] = []
    loop = asyncio.get_running_loop()
    for _ in range(n):
        key = f"bench/{uuid.uuid4().hex}.bin"
        t0 = time.perf_counter()
        await loop.run_in_executor(
            None, store.put_bytes, settings.minio_bucket_snapshots, key, payload
        )
        await loop.run_in_executor(None, store.get_bytes, settings.minio_bucket_snapshots, key)
        samples.append((time.perf_counter() - t0) * 1000)
        await loop.run_in_executor(None, store.remove, settings.minio_bucket_snapshots, key)
    return samples


async def main() -> int:
    configure_logging("latency")
    parser = argparse.ArgumentParser(description="Sentigon infra latency benchmark")
    parser.add_argument("--n", type=int, default=100)
    args = parser.parse_args()

    print(f"Sentigon infra latency benchmark (n={args.n} per op)\n")
    results = []
    results.append(_report("postgres SELECT 1", await bench_db(args.n)))
    results.append(_report("kafka pub->consume", await bench_kafka(args.n)))
    results.append(_report("minio put+get 4KB", await bench_minio(args.n)))

    print("\nNote: per-stage pipeline SLOs (stream->detection, event->operator, VLM)")
    print("are measured as Phases 2-4 land. These are the substrate floors.")
    log.info("latency.done", results=results)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
