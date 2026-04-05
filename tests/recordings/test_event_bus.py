"""Tests for :class:`EventBus`."""

from __future__ import annotations

import threading

from clm.recordings.workflow.event_bus import EventBus


class TestEventBusBasics:
    def test_subscriber_receives_published_event(self):
        bus = EventBus()
        received: list[tuple[str, object]] = []
        bus.subscribe(lambda t, p: received.append((t, p)))
        bus.publish("job", {"id": "1"})
        assert received == [("job", {"id": "1"})]

    def test_topic_filter(self):
        bus = EventBus()
        job_events: list[object] = []
        other_events: list[object] = []

        bus.subscribe(lambda t, p: job_events.append(p), topic="job")
        bus.subscribe(lambda t, p: other_events.append(p), topic="other")

        bus.publish("job", "j1")
        bus.publish("other", "o1")
        bus.publish("job", "j2")

        assert job_events == ["j1", "j2"]
        assert other_events == ["o1"]

    def test_wildcard_subscriber_receives_every_topic(self):
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe(lambda t, p: seen.append(t))
        bus.publish("job", 1)
        bus.publish("pairs", 2)
        bus.publish("job", 3)
        assert seen == ["job", "pairs", "job"]

    def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        received: list[object] = []
        cancel = bus.subscribe(lambda t, p: received.append(p))

        bus.publish("job", 1)
        cancel()
        bus.publish("job", 2)

        assert received == [1]

    def test_unsubscribe_twice_is_safe(self):
        bus = EventBus()
        cancel = bus.subscribe(lambda t, p: None)
        cancel()
        cancel()  # must not raise

    def test_handler_exception_does_not_break_publishing(self):
        bus = EventBus()
        received: list[object] = []

        def broken(topic, payload):
            raise RuntimeError("handler boom")

        bus.subscribe(broken)
        bus.subscribe(lambda t, p: received.append(p))

        # Should not raise; the good subscriber still gets the event.
        bus.publish("job", "hi")
        assert received == ["hi"]


class TestEventBusThreadSafety:
    def test_concurrent_publish_is_safe(self):
        bus = EventBus()
        received: list[int] = []
        lock = threading.Lock()

        def handler(topic, payload):
            with lock:
                received.append(payload)

        bus.subscribe(handler)

        def producer(start: int) -> None:
            for i in range(100):
                bus.publish("job", start + i)

        threads = [threading.Thread(target=producer, args=(n * 1000,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(received) == 400
