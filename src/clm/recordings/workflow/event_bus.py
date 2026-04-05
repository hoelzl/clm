"""Simple in-process event bus for job lifecycle events.

The :class:`JobManager` publishes ``job`` events through this bus whenever
a job is created, polled, or transitions state. The web layer subscribes
and forwards each event onto its SSE queue so the dashboard sees live
updates; tests subscribe with a list collector.

The bus is intentionally minimal: synchronous, thread-safe, no retries,
no delivery guarantees. It exists so backends and the :class:`JobManager`
don't directly depend on FastAPI or ``asyncio.Queue``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from loguru import logger

EventHandler = Callable[[str, Any], None]
"""Callback signature: ``handler(topic, payload) -> None``.

``payload`` is whatever the publisher passes (typically a Pydantic model
instance; subscribers serialize as needed).
"""


class EventBus:
    """Thread-safe synchronous pub/sub.

    Publishers call :meth:`publish` from any thread; subscribers register
    via :meth:`subscribe` and receive every subsequent event that matches
    their topic filter.

    Handlers are invoked serially on the publisher's thread. Exceptions
    raised by a handler are caught and logged — a misbehaving subscriber
    cannot break publishing for others.
    """

    def __init__(self) -> None:
        self._handlers: list[tuple[str | None, EventHandler]] = []
        self._lock = threading.RLock()

    def subscribe(
        self,
        handler: EventHandler,
        *,
        topic: str | None = None,
    ) -> Callable[[], None]:
        """Register *handler* for *topic* (or all topics if ``topic`` is None).

        Returns an unsubscribe function that removes this handler when
        called; safe to call more than once.
        """
        entry = (topic, handler)
        with self._lock:
            self._handlers.append(entry)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._handlers.remove(entry)
                except ValueError:
                    pass

        return unsubscribe

    def publish(self, topic: str, payload: Any) -> None:
        """Deliver *payload* to every subscriber whose filter matches *topic*."""
        with self._lock:
            targets = [
                handler for (filt, handler) in self._handlers if filt is None or filt == topic
            ]

        for handler in targets:
            try:
                handler(topic, payload)
            except Exception as exc:  # pragma: no cover — defensive
                logger.exception("EventBus handler for {} raised: {}", topic, exc)
