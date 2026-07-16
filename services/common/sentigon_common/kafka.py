"""Kafka (Redpanda) bus helpers: an idempotent producer and an at-least-once
consumer loop with explicit offset commits and correlation-ID propagation.

Messages are JSON-encoded pydantic models. The correlation_id travels in a Kafka
header so downstream stages log under the same trace.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError
from aiokafka.structs import OffsetAndMetadata, TopicPartition
from pydantic import BaseModel

from .config import settings
from .logging import get_logger, set_correlation_id

log = get_logger("kafka")

_HEADER_CORRELATION = "correlation_id"


def _encode(model: BaseModel) -> bytes:
    return orjson.dumps(model.model_dump(mode="json"))


class BusProducer:
    """Idempotent JSON producer. One per service, started at boot."""

    def __init__(self, client_id: str | None = None) -> None:
        self._client_id = client_id or settings.kafka_client_id
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        if self._producer is not None:
            return
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap,
            client_id=self._client_id,
            enable_idempotence=True,
            acks="all",
            linger_ms=5,
        )
        await self._producer.start()
        log.info("producer.started", bootstrap=settings.kafka_bootstrap)

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(
        self,
        topic: str,
        message: BaseModel,
        *,
        key: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        if self._producer is None:
            raise RuntimeError("BusProducer not started")
        headers = []
        if correlation_id is None:
            correlation_id = getattr(message, "correlation_id", None)
        if correlation_id:
            headers.append((_HEADER_CORRELATION, str(correlation_id).encode()))
        await self._producer.send_and_wait(
            topic,
            value=_encode(message),
            key=key.encode() if key else None,
            headers=headers or None,
        )


async def ensure_topics(
    topics: Sequence[str], *, partitions: int = 1, replication: int = 1
) -> None:
    """Create topics if they do not exist. Safe to call repeatedly."""
    admin = AIOKafkaAdminClient(
        bootstrap_servers=settings.kafka_bootstrap, client_id=settings.kafka_client_id
    )
    await admin.start()
    try:
        new = [
            NewTopic(name=t, num_partitions=partitions, replication_factor=replication)
            for t in topics
        ]
        try:
            await admin.create_topics(new)
            log.info("topics.created", topics=list(topics))
        except TopicAlreadyExistsError:
            pass
        except Exception as exc:  # already-exists races surface as generic errors on some brokers
            log.info("topics.ensure", note=str(exc))
    finally:
        await admin.close()


Handler = Callable[[dict, str | None], Awaitable[None]]


async def _to_dead_letter(producer: AIOKafkaProducer | None, msg) -> bool:
    """Publish a poison message verbatim to `<topic>.dlq`. Returns success."""
    if producer is None:
        return False
    try:
        await producer.send_and_wait(
            f"{msg.topic}.dlq", value=msg.value, key=msg.key, headers=msg.headers
        )
        return True
    except Exception:
        log.exception("consumer.dlq_publish_failed", topic=msg.topic, offset=msg.offset)
        return False


async def run_consumer(
    topics: Sequence[str],
    group_id: str,
    handler: Handler,
    *,
    stop_event: asyncio.Event | None = None,
    auto_offset_reset: str = "earliest",
    max_retries: int = 3,
    dead_letter: bool = True,
) -> None:
    """At-least-once consumer loop with explicit per-message offset commits.

    The handler is invoked with (decoded_dict, correlation_id). Only the *specific*
    offset of a successfully-handled message is committed (never the partition
    high-water mark), so a failing message can never be skipped by a later success.
    On failure the message is retried with backoff up to ``max_retries``; if it
    still fails it is routed to ``<topic>.dlq`` and committed past (so one poison
    message cannot wedge the partition). If the DLQ itself is unreachable the offset
    is left uncommitted and the message replays — no event is ever silently lost.
    Handlers must still be idempotent (a redelivery after a crash mid-handling).
    """
    consumer = AIOKafkaConsumer(
        *topics,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=group_id,
        client_id=f"{settings.kafka_client_id}-{group_id}",
        enable_auto_commit=False,
        auto_offset_reset=auto_offset_reset,
    )
    await consumer.start()
    dlq_producer: AIOKafkaProducer | None = None
    if dead_letter:
        dlq_producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap,
            client_id=f"{settings.kafka_client_id}-{group_id}-dlq",
            enable_idempotence=True,
            acks="all",
        )
        await dlq_producer.start()
    log.info("consumer.started", topics=list(topics), group=group_id)
    try:
        async for msg in consumer:
            cid: str | None = None
            for k, v in msg.headers or ():
                if k == _HEADER_CORRELATION and v:
                    cid = v.decode()
            set_correlation_id(cid)

            tp = TopicPartition(msg.topic, msg.partition)
            # commit offset+1: the next offset to consume after this message
            commit = {tp: OffsetAndMetadata(msg.offset + 1, "")}

            # 1. decode — an unparseable body is a poison pill, never retryable
            try:
                payload = orjson.loads(msg.value)
            except Exception:
                log.exception("consumer.decode_error", topic=msg.topic, offset=msg.offset)
                if await _to_dead_letter(dlq_producer, msg):
                    await consumer.commit(commit)
                else:
                    consumer.seek(tp, msg.offset)
                    await asyncio.sleep(5)
                if stop_event is not None and stop_event.is_set():
                    break
                continue

            # 2. handle with bounded retry + backoff
            handled = False
            for attempt in range(1, max_retries + 1):
                try:
                    await handler(payload, cid)
                    handled = True
                    break
                except Exception:
                    log.exception(
                        "consumer.handler_error",
                        topic=msg.topic,
                        offset=msg.offset,
                        attempt=attempt,
                        max_retries=max_retries,
                    )
                    if stop_event is not None and stop_event.is_set():
                        break
                    if attempt < max_retries:
                        await asyncio.sleep(min(2**attempt, 30))

            # 3. commit the exact offset on success; DLQ-or-replay on exhaustion
            if handled:
                await consumer.commit(commit)
            elif await _to_dead_letter(dlq_producer, msg):
                await consumer.commit(commit)
                log.error("consumer.dead_lettered", topic=msg.topic, offset=msg.offset)
            else:
                # cannot advance without losing the message: replay it, don't commit
                consumer.seek(tp, msg.offset)
                log.error("consumer.dlq_unavailable_replaying", topic=msg.topic, offset=msg.offset)
                await asyncio.sleep(5)

            if stop_event is not None and stop_event.is_set():
                break
    finally:
        await consumer.stop()
        if dlq_producer is not None:
            await dlq_producer.stop()
        log.info("consumer.stopped", group=group_id)
