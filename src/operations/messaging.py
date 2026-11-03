"""Central RabbitMQ commands and bounded, best-effort application log transport."""

from __future__ import annotations

from datetime import datetime, timezone
from collections import deque
import json
import os
import threading
import time
from uuid import uuid4

import pika


def connection() -> pika.BlockingConnection:
    params = pika.URLParameters(os.environ["BROKER_URL"])
    params.heartbeat = 30
    params.blocked_connection_timeout = 1
    params.socket_timeout = 1
    params.stack_timeout = 2
    params.connection_attempts = 1
    return pika.BlockingConnection(params)


def declare(channel: object) -> None:
    """Declare durable command queues and a separately bounded technical-log queue."""
    channel.exchange_declare(exchange="operations", exchange_type="direct", durable=True)
    channel.queue_declare(queue="dead", durable=True)
    for name in ("business", "ingest", "ml"):
        channel.queue_declare(queue=name, durable=True, arguments={"x-queue-type": "quorum", "x-consumer-timeout": 3600000,
                                                                 "x-dead-letter-exchange": "", "x-dead-letter-routing-key": "dead"})
        channel.queue_bind(queue=name, exchange="operations", routing_key=name)
    channel.queue_declare(queue="logs", durable=True, arguments={"x-max-length": 10000, "x-max-length-bytes": 67108864,
                                                                "x-message-ttl": 86400000, "x-overflow": "drop-head"})
    channel.queue_bind(queue="logs", exchange="operations", routing_key="logs")


class LogTransport:
    """Bound an in-memory producer buffer; own all log AMQP I/O on one daemon thread."""

    def __init__(self) -> None:
        self.buffer: deque[tuple[float, bytes, str]] = deque()
        self.bytes = 0
        self.dropped = 0
        self.condition = threading.Condition()
        self.in_flight = False
        self.thread = threading.Thread(target=self.publish, daemon=True, name="bounded-log-publisher")
        self.thread.start()

    def offer(self, encoded: bytes, level: str) -> None:
        with self.condition:
            while self.buffer and (len(self.buffer) >= 1000 or self.bytes+len(encoded) > 5*1024*1024):
                index = next((i for i, (_, _, priority) in enumerate(self.buffer) if priority in ("DEBUG", "INFO")), 0)
                _, removed, _ = self.buffer[index]
                del self.buffer[index]
                self.bytes -= len(removed)
                self.dropped += 1
            self.buffer.append((time.monotonic()+60, encoded, level))
            self.bytes += len(encoded)
            self.condition.notify_all()

    def publish(self) -> None:
        conn, channel = None, None
        while True:
            with self.condition:
                while not self.buffer:
                    self.condition.wait(timeout=1)
                deadline, encoded, level = self.buffer.popleft()
                self.bytes -= len(encoded)
                self.in_flight = True
            if time.monotonic() > deadline:
                with self.condition:
                    self.dropped += 1
                    self.in_flight = False
                    self.condition.notify_all()
                continue
            try:
                if conn is None or conn.is_closed:
                    conn = connection()
                    channel = conn.channel()
                    declare(channel)
                channel.basic_publish(exchange="operations", routing_key="logs", body=encoded,
                                      properties=pika.BasicProperties(content_type="application/json", delivery_mode=1))
                conn.process_data_events(time_limit=0)
                with self.condition:
                    dropped = self.dropped
                    self.dropped = 0
                if dropped:
                    summary = json.loads(encoded)
                    summary.update(event_id=str(uuid4()), code="LOGS_DROPPED", message="Bounded producer discarded technical logs", context={"count": dropped})
                    channel.basic_publish(exchange="operations", routing_key="logs", body=json.dumps(summary).encode())
            except Exception:
                try:
                    if conn is not None and conn.is_open:
                        conn.close()
                except Exception:
                    pass
                conn = None
                with self.condition:
                    if len(self.buffer) < 1000 and self.bytes+len(encoded) <= 5*1024*1024:
                        self.buffer.appendleft((deadline, encoded, level))
                        self.bytes += len(encoded)
                    else:
                        self.dropped += 1
                time.sleep(1)
            finally:
                with self.condition:
                    self.in_flight = False
                    self.condition.notify_all()

    def flush(self, timeout: float) -> None:
        deadline = time.monotonic()+timeout
        with self.condition:
            while (self.buffer or self.in_flight) and time.monotonic() < deadline:
                self.condition.wait(timeout=max(0, deadline-time.monotonic()))


_transport: LogTransport | None = None
_transport_lock = threading.Lock()


def flush_logs(timeout: float = 1) -> None:
    """Allow a short-lived worker at most one second to flush its volatile log buffer."""
    if _transport is not None:
        _transport.flush(timeout)


def log(scope: str, service: str, code: str, message: str, job: str | None = None, context: dict | None = None) -> None:
    """Offer a bounded log without broker I/O in the business thread or file/DB fallback."""
    global _transport
    payload = {"event_id": str(uuid4()), "scope_id": str(scope), "occurred_at": datetime.now(timezone.utc).isoformat(),
               "level": "ERROR" if code.endswith("FAILED") or code == "INTERNAL_ERROR" else "INFO", "service": service,
               "code": code, "message": message[:1000], "correlation_id": job, "job_id": job, "context": context or {}}
    encoded = json.dumps(payload).encode()
    if len(encoded) > 16384:
        return
    with _transport_lock:
        if _transport is None:
            _transport = LogTransport()
    _transport.offer(encoded, payload["level"])
