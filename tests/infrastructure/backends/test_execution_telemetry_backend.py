"""Host-side telemetry handling in SqliteBackend (issue #330).

The notebook worker ships telemetry over two channels: an
``execution_telemetry`` ProcessingWarning on completed jobs and an
``execution_telemetry`` key in the structured error JSON on failed jobs.
These tests pin the backend's handling of the completed-job channel —
persist to the store, report flakes to the build reporter, and never leak
the record as a user-facing warning — plus the persistence helper both
channels share.
"""

import json
from unittest.mock import MagicMock

import pytest

from clm.infrastructure.backends.sqlite_backend import SqliteBackend
from clm.infrastructure.database.execution_telemetry import ExecutionTelemetryStore


@pytest.fixture
def backend(tmp_path):
    store = ExecutionTelemetryStore(tmp_path / "telemetry.db")
    backend = SqliteBackend(
        db_path=tmp_path / "jobs.db",
        workspace_path=tmp_path,
        enable_progress_tracking=False,
        skip_worker_check=True,
        telemetry_store=store,
    )
    backend.build_reporter = MagicMock()
    return backend


def _telemetry_details(outcome: str = "passed_after_retry") -> dict:
    return {
        "schema": 1,
        "attempts": 2,
        "outcome": outcome,
        "classification": "flaky" if outcome == "passed_after_retry" else "deterministic",
        "failure_type": "dead_kernel",
        "failing_cell_index": 4,
        "error_message": "Kernel died",
        "attempts_detail": [
            {
                "attempt": 1,
                "failure_type": "dead_kernel",
                "error_class": "DeadKernelError",
                "failing_cell_index": 4,
                "message": "Kernel died",
            }
        ],
    }


def _wire_job_row(backend, warnings: list[dict], payload: dict) -> None:
    """Stub the jobs-table read inside _extract_and_report_job_warnings."""
    result_json = json.dumps({"warnings": warnings})
    payload_json = json.dumps(payload)
    backend.job_queue = MagicMock()
    backend.job_queue._get_conn.return_value.execute.return_value.fetchone.return_value = (
        result_json,
        payload_json,
        "contenthash",
    )


JOB_INFO = {
    "input_file": "C:/course/slides_flaky.py",
    "output_file": "out/slides_flaky.html",
    "job_type": "notebook",
}

PAYLOAD = {
    "kind": "completed",
    "prog_lang": "cpp",
    "language": "de",
    "format": "html",
    "worker_image_identity": "docker:clm-cpp:1.2",
}


class TestCompletedJobTelemetry:
    def test_flake_is_persisted_and_reported(self, backend):
        _wire_job_row(
            backend,
            [
                {
                    "category": "execution_telemetry",
                    "message": "passed only after 2 attempts",
                    "severity": "low",
                    "file_path": JOB_INFO["input_file"],
                    "details": _telemetry_details(),
                }
            ],
            PAYLOAD,
        )

        backend._extract_and_report_job_warnings(1, JOB_INFO)

        events = backend.telemetry_store.events()
        assert len(events) == 1
        event = events[0]
        assert event.input_file == JOB_INFO["input_file"]
        assert event.outcome == "passed_after_retry"
        assert event.prog_lang == "cpp"
        assert event.language == "de"
        assert event.content_hash == "contenthash"
        assert event.worker_image_identity == "docker:clm-cpp:1.2"

        backend.build_reporter.report_flaky_file.assert_called_once_with(
            file_path=JOB_INFO["input_file"],
            attempts=2,
            failure_types=["dead_kernel"],
            language="de",
        )
        # Telemetry is not a user-facing warning.
        backend.build_reporter.report_warning.assert_not_called()

    def test_suppressed_failure_is_persisted_but_not_in_flake_list(self, backend):
        _wire_job_row(
            backend,
            [
                {
                    "category": "execution_telemetry",
                    "message": "suppressed",
                    "severity": "low",
                    "file_path": JOB_INFO["input_file"],
                    "details": _telemetry_details(outcome="suppressed_failure"),
                }
            ],
            PAYLOAD,
        )

        backend._extract_and_report_job_warnings(1, JOB_INFO)

        assert backend.telemetry_store.events()[0].outcome == "suppressed_failure"
        backend.build_reporter.report_flaky_file.assert_not_called()

    def test_regular_warnings_still_flow_through(self, backend):
        _wire_job_row(
            backend,
            [
                {
                    "category": "skip_errors_cell_failed",
                    "message": "outputs cleared",
                    "severity": "low",
                    "file_path": JOB_INFO["input_file"],
                }
            ],
            PAYLOAD,
        )

        backend._extract_and_report_job_warnings(1, JOB_INFO)

        assert backend.telemetry_store.events() == []
        backend.build_reporter.report_warning.assert_called_once()


class TestPersistHelper:
    def test_failed_job_telemetry_shape(self, backend):
        """The failure channel feeds the same helper with the error JSON's
        execution_telemetry dict."""
        telemetry = _telemetry_details(outcome="failed")
        backend._persist_execution_telemetry(
            JOB_INFO["input_file"], PAYLOAD, "contenthash", telemetry
        )

        event = backend.telemetry_store.events()[0]
        assert event.outcome == "failed"
        assert event.classification == "deterministic"
        assert event.failing_cell_index == 4
        assert event.attempts == 2

    def test_no_store_is_a_noop(self, tmp_path):
        backend = SqliteBackend(
            db_path=tmp_path / "jobs.db",
            workspace_path=tmp_path,
            enable_progress_tracking=False,
            skip_worker_check=True,
        )
        backend._persist_execution_telemetry("f.py", {}, "h", _telemetry_details())
