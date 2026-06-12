"""Tests for per-deck execution-telemetry capture in the retry loop (issue #330).

Covers the failure classification, the per-attempt summarization, and the
retry loop's three emission paths: passed-after-retry (warning channel),
final failure (telemetry attached to the enhanced error), and the
skip-errors suppressed-failure path.
"""

import asyncio
import uuid
from pathlib import Path

import pytest
from nbclient.exceptions import CellExecutionError, CellTimeoutError, DeadKernelError
from nbformat import NotebookNode

import clm.workers.notebook.notebook_processor as np_module
from clm.infrastructure.messaging.notebook_classes import NotebookPayload
from clm.workers.notebook.notebook_processor import (
    CellContext,
    NotebookProcessor,
    classify_execution_failure,
    summarize_execution_attempts,
)
from clm.workers.notebook.output_spec import create_output_spec

# =============================================================================
# classify_execution_failure
# =============================================================================


class TestClassifyExecutionFailure:
    def test_cell_execution_error(self):
        error = CellExecutionError("traceback", "NameError", "name 'x' is not defined")
        assert classify_execution_failure(error) == (
            "cell_execution_error",
            "CellExecutionError",
        )

    def test_dead_kernel_error(self):
        assert classify_execution_failure(DeadKernelError("Kernel died")) == (
            "dead_kernel",
            "DeadKernelError",
        )

    def test_cell_timeout_error(self):
        failure_type, _ = classify_execution_failure(CellTimeoutError("timed out"))
        assert failure_type == "cell_timeout"

    def test_startup_timeout_runtime_error(self):
        error = RuntimeError("Kernel didn't respond in 300 seconds")
        assert classify_execution_failure(error) == ("startup_timeout", "RuntimeError")

    def test_kernel_died_runtime_error(self):
        error = RuntimeError("Kernel died before replying to kernel_info")
        assert classify_execution_failure(error) == ("dead_kernel", "RuntimeError")

    def test_plain_timeout(self):
        assert classify_execution_failure(TimeoutError("slow")) == (
            "cell_timeout",
            "TimeoutError",
        )

    def test_no_such_kernel_by_class_name(self):
        from jupyter_client.kernelspec import NoSuchKernel

        failure_type, error_class = classify_execution_failure(NoSuchKernel("xcpp20"))
        assert failure_type == "missing_kernel"
        assert error_class == "NoSuchKernel"

    def test_no_such_kernel_by_message(self):
        # Wrapped/re-raised forms keep only the message.
        error = RuntimeError("NoSuchKernel: No such kernel named xcpp20")
        assert classify_execution_failure(error) == ("missing_kernel", "RuntimeError")

    def test_other(self):
        assert classify_execution_failure(ValueError("boom")) == ("other", "ValueError")


# =============================================================================
# summarize_execution_attempts
# =============================================================================


def _attempt(n: int, failure_type: str = "dead_kernel", cell: int | None = 3) -> dict:
    return {
        "attempt": n,
        "failure_type": failure_type,
        "error_class": "DeadKernelError",
        "failing_cell_index": cell,
        "message": "Kernel died",
    }


class TestSummarizeExecutionAttempts:
    def test_flaky_pass(self):
        telemetry = summarize_execution_attempts([_attempt(1)], attempts_made=2, succeeded=True)
        assert telemetry["outcome"] == "passed_after_retry"
        assert telemetry["classification"] == "flaky"
        assert telemetry["attempts"] == 2
        assert telemetry["failure_type"] == "dead_kernel"
        assert telemetry["failing_cell_index"] == 3

    def test_deterministic_failure(self):
        telemetry = summarize_execution_attempts(
            [_attempt(1), _attempt(2)], attempts_made=2, succeeded=False
        )
        assert telemetry["outcome"] == "failed"
        assert telemetry["classification"] == "deterministic"

    def test_mixed_failure_by_type(self):
        telemetry = summarize_execution_attempts(
            [_attempt(1), _attempt(2, failure_type="startup_timeout", cell=None)],
            attempts_made=2,
            succeeded=False,
        )
        assert telemetry["classification"] == "mixed"

    def test_mixed_failure_by_cell(self):
        telemetry = summarize_execution_attempts(
            [_attempt(1, cell=3), _attempt(2, cell=7)], attempts_made=2, succeeded=False
        )
        assert telemetry["classification"] == "mixed"


# =============================================================================
# Retry-loop integration
# =============================================================================


def _make_payload(**overrides) -> NotebookPayload:
    base = {
        "input_file": "C:/course/slides_flaky.py",
        "input_file_name": "slides_flaky.py",
        "output_file": "slides_flaky.html",
        "data": "",
        "correlation_id": f"test-{uuid.uuid4().hex[:8]}",
        "kind": "completed",
        "prog_lang": "python",
        "language": "en",
        "format": "html",
    }
    base.update(overrides)
    return NotebookPayload(**base)


def _make_notebook() -> NotebookNode:
    return NotebookNode(
        {
            "cells": [],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )


class _ScriptedPreprocessor:
    """Stands in for TrackingExecutePreprocessor; raises per a script.

    ``script`` holds one entry per expected attempt: an exception to raise
    or ``None`` for success. Class-level state because the retry loop
    constructs a fresh instance per attempt.
    """

    script: list[BaseException | None] = []
    calls: int = 0
    failing_cell_index: int = 5

    def __init__(self, processor, **_kwargs):
        self.processor = processor

    def preprocess(self, nb, resources=None):
        cls = type(self)
        action = cls.script[cls.calls]
        cls.calls += 1
        if action is not None:
            self.processor._current_cell = CellContext(
                cell_index=cls.failing_cell_index, cell_source="1/0", cell_type="code"
            )
            raise action
        return nb, {}

    @classmethod
    def reset(cls, script: list[BaseException | None]):
        cls.script = script
        cls.calls = 0


@pytest.fixture
def scripted_execution(monkeypatch):
    """Patch the preprocessor and keep retries/backoff test-sized."""
    monkeypatch.setattr(np_module, "TrackingExecutePreprocessor", _ScriptedPreprocessor)
    monkeypatch.setattr(np_module, "NUM_RETRIES_FOR_HTML", 2)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(np_module.asyncio, "sleep", no_sleep)
    return _ScriptedPreprocessor


def _run_execution(processor: NotebookProcessor, payload: NotebookPayload, tmp_path: Path):
    nb = _make_notebook()

    async def run():
        loop = asyncio.get_running_loop()
        await processor._execute_notebook_with_path("cid", tmp_path, nb, payload, loop, None)

    asyncio.run(run())


class TestRetryLoopTelemetry:
    def _processor(self) -> NotebookProcessor:
        return NotebookProcessor(create_output_spec("completed", "python", "en", "html"))

    def test_clean_pass_records_no_telemetry(self, scripted_execution, tmp_path):
        scripted_execution.reset([None])
        processor = self._processor()
        _run_execution(processor, _make_payload(), tmp_path)
        assert processor.get_warnings() == []

    def test_pass_after_retry_emits_telemetry_warning(self, scripted_execution, tmp_path):
        scripted_execution.reset([DeadKernelError("Kernel died"), None])
        processor = self._processor()
        _run_execution(processor, _make_payload(), tmp_path)

        warnings = processor.get_warnings()
        assert len(warnings) == 1
        warning = warnings[0]
        assert warning.category == "execution_telemetry"
        assert warning.severity == "low"
        details = warning.details
        assert details["outcome"] == "passed_after_retry"
        assert details["classification"] == "flaky"
        assert details["attempts"] == 2
        assert details["failure_type"] == "dead_kernel"
        assert details["failing_cell_index"] == _ScriptedPreprocessor.failing_cell_index
        assert details["attempts_detail"][0]["error_class"] == "DeadKernelError"

    def test_deterministic_failure_attaches_telemetry_to_error(self, scripted_execution, tmp_path):
        scripted_execution.reset([DeadKernelError("Kernel died"), DeadKernelError("Kernel died")])
        processor = self._processor()

        with pytest.raises(RuntimeError) as exc_info:
            _run_execution(processor, _make_payload(), tmp_path)

        telemetry = exc_info.value.execution_telemetry
        assert telemetry["outcome"] == "failed"
        assert telemetry["classification"] == "deterministic"
        assert telemetry["attempts"] == 2
        assert telemetry["failure_type"] == "dead_kernel"
        # No telemetry warning on the failure path — the error carries it.
        assert processor.get_warnings() == []

    def test_startup_timeout_has_no_failing_cell(self, scripted_execution, tmp_path):
        """A failure before any cell ran must not be blamed on a stale cell
        context from the previous attempt."""

        class _StartupTimeoutPreprocessor(_ScriptedPreprocessor):
            def preprocess(self, nb, resources=None):
                cls = type(self)
                action = cls.script[cls.calls]
                cls.calls += 1
                if action is not None:
                    # Simulate a startup failure: no cell context is set.
                    raise action
                return nb, {}

        _StartupTimeoutPreprocessor.reset(
            [
                RuntimeError("Kernel didn't respond in 300 seconds"),
                RuntimeError("Kernel didn't respond in 300 seconds"),
            ]
        )
        import clm.workers.notebook.notebook_processor as np_mod

        np_mod.TrackingExecutePreprocessor = _StartupTimeoutPreprocessor

        processor = self._processor()
        # Stale context from a hypothetical earlier run.
        processor._current_cell = CellContext(cell_index=99, cell_source="", cell_type="code")

        with pytest.raises(RuntimeError) as exc_info:
            _run_execution(processor, _make_payload(), tmp_path)

        telemetry = exc_info.value.execution_telemetry
        assert telemetry["failure_type"] == "startup_timeout"
        assert telemetry["failing_cell_index"] is None
        assert telemetry["classification"] == "deterministic"

    def test_missing_kernel_fails_fast_without_retries(self, scripted_execution, tmp_path):
        """Issue #348: a missing kernelspec is permanent for the build —
        retrying it only wastes kernel-startup and backoff time per job."""
        from jupyter_client.kernelspec import NoSuchKernel

        scripted_execution.reset([NoSuchKernel("xcpp20"), None])
        processor = self._processor()

        with pytest.raises(RuntimeError) as exc_info:
            _run_execution(processor, _make_payload(), tmp_path)

        # Only one attempt was made; the scripted success on attempt 2 was
        # never reached.
        assert scripted_execution.calls == 1
        telemetry = exc_info.value.execution_telemetry
        assert telemetry["attempts"] == 1
        assert telemetry["failure_type"] == "missing_kernel"
        # The enhanced error points at the remedy.
        assert "--no-html" in str(exc_info.value)

    def test_skip_errors_suppression_emits_suppressed_failure(self, scripted_execution, tmp_path):
        error = CellExecutionError("tb", "ValueError", "boom")
        scripted_execution.reset([error, error])
        processor = self._processor()
        payload = _make_payload(skip_errors=True)

        # Suppressed: must not raise.
        _run_execution(processor, payload, tmp_path)

        warnings = [w for w in processor.get_warnings() if w.category == "execution_telemetry"]
        assert len(warnings) == 1
        assert warnings[0].details["outcome"] == "suppressed_failure"
        assert warnings[0].details["classification"] == "deterministic"
