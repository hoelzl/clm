"""Tests for the execution-telemetry store (issue #330)."""

from pathlib import Path

from clm.infrastructure.database.execution_telemetry import (
    DEFAULT_TELEMETRY_DB_NAME,
    ExecutionTelemetryStore,
    TelemetryEvent,
    default_telemetry_db_path,
)


def _event(**overrides) -> TelemetryEvent:
    base = {
        "input_file": "C:/course/slides_flaky.py",
        "outcome": "passed_after_retry",
        "classification": "flaky",
        "attempts": 3,
        "failure_type": "dead_kernel",
        "failing_cell_index": 17,
        "error_message": "Kernel died",
        "prog_lang": "cpp",
        "language": "de",
        "content_hash": "abc123",
        "worker_image_identity": "docker:clm-cpp:1.2",
        "attempts_detail": [{"attempt": 1, "failure_type": "dead_kernel"}],
    }
    base.update(overrides)
    return TelemetryEvent(**base)


class TestDefaultPath:
    def test_default_path_is_sibling_of_cache_db(self):
        cache = Path("some/dir/clm_cache.db")
        assert default_telemetry_db_path(cache) == Path("some/dir") / DEFAULT_TELEMETRY_DB_NAME


class TestRecordAndQuery:
    def test_roundtrip(self, tmp_path):
        store = ExecutionTelemetryStore(tmp_path / "telemetry.db")
        store.record_event(_event())

        events = store.events()
        assert len(events) == 1
        event = events[0]
        assert event.input_file == "C:/course/slides_flaky.py"
        assert event.outcome == "passed_after_retry"
        assert event.classification == "flaky"
        assert event.attempts == 3
        assert event.failure_type == "dead_kernel"
        assert event.failing_cell_index == 17
        assert event.prog_lang == "cpp"
        assert event.language == "de"
        assert event.worker_image_identity == "docker:clm-cpp:1.2"
        assert event.attempts_detail == [{"attempt": 1, "failure_type": "dead_kernel"}]
        assert event.created_at  # stamped by SQLite

    def test_filter_by_input_file(self, tmp_path):
        store = ExecutionTelemetryStore(tmp_path / "telemetry.db")
        store.record_event(_event())
        store.record_event(_event(input_file="C:/course/slides_other.py"))

        events = store.events(input_file="C:/course/slides_other.py")
        assert len(events) == 1
        assert events[0].input_file == "C:/course/slides_other.py"

    def test_since_filter_excludes_old_events(self, tmp_path):
        store = ExecutionTelemetryStore(tmp_path / "telemetry.db")
        store.record_event(_event())
        assert store.events(since="2000-01-01T00:00:00") != []
        assert store.events(since="2999-01-01T00:00:00") == []

    def test_limit(self, tmp_path):
        store = ExecutionTelemetryStore(tmp_path / "telemetry.db")
        for _ in range(5):
            store.record_event(_event())
        assert len(store.events(limit=2)) == 2

    def test_problem_files_groups_by_deck(self, tmp_path):
        store = ExecutionTelemetryStore(tmp_path / "telemetry.db")
        store.record_event(_event())
        store.record_event(_event(outcome="failed", classification="deterministic"))
        store.record_event(_event(input_file="C:/course/slides_other.py"))

        problems = store.problem_files()
        assert set(problems) == {"C:/course/slides_flaky.py", "C:/course/slides_other.py"}
        assert len(problems["C:/course/slides_flaky.py"]) == 2

    def test_missing_db_reads_as_empty(self, tmp_path):
        store = ExecutionTelemetryStore(tmp_path / "never_created.db")
        assert store.events() == []
        assert store.problem_files() == {}
        assert not (tmp_path / "never_created.db").exists()

    def test_record_never_raises(self, tmp_path, monkeypatch):
        store = ExecutionTelemetryStore(tmp_path / "telemetry.db")

        def explode():
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(store, "_connect", explode)
        store.record_event(_event())  # must not raise
