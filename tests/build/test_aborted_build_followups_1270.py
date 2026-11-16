"""Aborted-build follow-ups from #1090 (issue #1270).

Item 2: a topic supporting file deleted while the build runs made every
notebook payload build fail with a bare ``FileNotFoundError``; the stage
aborted and the build summary reported it as an ``[Infrastructure]`` error
("This may be a bug in CLM"). A file that vanished from the course is a
content problem: the abort is now classified as a *user* error naming the
missing file.

Item 3: an aborted (or any) build logged both "Stopped 10 workers" and
"Stopped 0 worker(s)" at teardown. The engine's line read the length of the
worker list after the lifecycle manager had cleared it (the same list
object), so it always said 0. One accurate stop line is left.

Item 1 (no jobs submitted after the abort) is covered in
``tests/infrastructure/backends/test_abort_stops_job_submission.py``.
"""

import builtins
import logging
import shutil
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from clm.build.reporter import BuildReporter
from clm.core.backend import SourceFileVanishedError, SubmissionDiscardedError
from clm.core.course import Course, _execute_stage_operation
from tests.conftest import DATA_DIR
from tests.dummy_backend import DummyBackend

_group = cast(type, builtins.BaseExceptionGroup)  # type: ignore[attr-defined]


def _reporter() -> BuildReporter:
    return BuildReporter(MagicMock())


# ---------------------------------------------------------------------------
# Item 2: the vanished file is raised as SourceFileVanishedError ...
# ---------------------------------------------------------------------------


def _course_with_deleted_supporting_file(course_1_spec, tmp_path):
    data_dir = tmp_path / "data"
    shutil.copytree(DATA_DIR, data_dir)
    course = Course.from_spec(course_1_spec, data_dir, tmp_path / "out")
    topic_dir = data_dir / "slides/module_000_test_1/topic_100_some_topic_from_test_1"
    notebook = course.find_file(topic_dir / "slides_some_topic_from_test_1.py")
    assert notebook is not None
    supporting = topic_dir / "data/test.data"
    assert any(f.path == supporting for f in notebook.topic.files)
    supporting.unlink()  # the author/agent deletes it mid-build
    return course, notebook, supporting


async def test_deleted_supporting_file_raises_source_file_vanished(course_1_spec, tmp_path):
    """Regression test for #1270 item 2: a supporting file enumerated at load
    time and deleted before its notebook's payload is built aborts the stage
    with the typed ``SourceFileVanishedError`` (still a ``FileNotFoundError``)
    naming it, and stops the stage's pending submissions."""
    course, notebook, supporting = _course_with_deleted_supporting_file(course_1_spec, tmp_path)
    backend = DummyBackend()
    discards: list[int] = []

    process_op = await notebook.get_processing_operation(course.output_root)
    with (
        patch.object(DummyBackend, "discard_pending_submissions", lambda self: discards.append(1)),
        pytest.raises(SourceFileVanishedError) as excinfo,
    ):
        await _execute_stage_operation(process_op, backend, course.course_root)
    assert isinstance(excinfo.value, FileNotFoundError)
    assert Path(excinfo.value.filename) == supporting
    assert discards, "the failing operation must discard pending submissions at once"


async def test_missing_file_outside_the_course_stays_untyped(tmp_path):
    """A missing file outside the course root (e.g. a clm package template)
    may be a clm bug: it is not relabelled as a vanished course file."""

    class _Op:
        async def execute(self, backend):
            raise FileNotFoundError(2, "No such file", str(tmp_path / "clm" / "macros.j2"))

    course_root = tmp_path / "course"
    course_root.mkdir()
    with pytest.raises(FileNotFoundError) as excinfo:
        await _execute_stage_operation(_Op(), DummyBackend(), course_root)  # type: ignore[arg-type]
    assert not isinstance(excinfo.value, SourceFileVanishedError)


async def test_vanished_file_aborts_the_stage_as_a_user_error(course_1_spec, tmp_path):
    """End to end through the stage runner and the reporter: the #1090
    scenario now records a user error naming the deleted file."""
    from clm.core.utils.execution_utils import execution_stages

    course, _notebook, supporting = _course_with_deleted_supporting_file(course_1_spec, tmp_path)
    backend = DummyBackend()
    reporter = _reporter()
    with pytest.raises(BaseException) as excinfo:
        for stage in execution_stages():
            await course.process_stage(stage, backend)
    reporter.mark_aborted(excinfo.value)

    [error] = reporter.finish_build().errors
    assert error.error_type == "user"
    assert Path(error.file_path) == supporting


# ---------------------------------------------------------------------------
# ... and the reporter classifies such an abort as a user error.
# ---------------------------------------------------------------------------


def _vanished(name: str = "temperature_utils.py") -> SourceFileVanishedError:
    return SourceFileVanishedError(2, "No such file or directory", f"C:/course/topic/{name}")


class TestVanishedSourceAbortClassification:
    def test_abort_from_vanished_files_is_a_user_error(self):
        """The #1090 shape: the TaskGroup wraps several vanished-file errors
        (one per output variant of the deck)."""
        exc = _group(
            "unhandled errors in a TaskGroup",
            [_group("unhandled errors in a TaskGroup", [_vanished(), _vanished()])],
        )
        reporter = _reporter()
        reporter.mark_aborted(exc)

        summary = reporter.finish_build()
        [error] = summary.errors
        assert error.error_type == "user"
        # The category stays build_aborted: consumers key on it (JSON report,
        # sweep skip), only the classification changes.
        assert error.category == "build_aborted"
        assert error.severity == "fatal"
        assert summary.aborted is True
        assert error.file_path == "C:/course/topic/temperature_utils.py"
        assert "temperature_utils.py" in error.message
        assert "deleted or moved" in error.actionable_guidance

    def test_discarded_sibling_submissions_do_not_change_the_label(self):
        """Siblings whose submissions were discarded by the abort are
        collateral, like cancellations."""
        exc = _group(
            "unhandled errors in a TaskGroup",
            [_vanished(), SubmissionDiscardedError("discarded: the build stage aborted")],
        )
        reporter = _reporter()
        reporter.mark_aborted(exc)

        [error] = reporter.finish_build().errors
        assert error.error_type == "user"

    def test_mixed_failure_stays_infrastructure(self):
        """A real infrastructure failure alongside a vanished file keeps the
        abort an infrastructure error — the classification must not hide it."""
        exc = _group(
            "unhandled errors in a TaskGroup",
            [_vanished(), RuntimeError("No workers available to process 'notebook' jobs")],
        )
        reporter = _reporter()
        reporter.mark_aborted(exc)

        [error] = reporter.finish_build().errors
        assert error.error_type == "infrastructure"

    def test_untyped_file_not_found_stays_infrastructure(self):
        """Only the typed signal counts: a FileNotFoundError from anywhere
        else (e.g. a clm-internal temp file) may well be a clm bug."""
        reporter = _reporter()
        reporter.mark_aborted(FileNotFoundError(2, "No such file", "C:/tmp/x"))

        [error] = reporter.finish_build().errors
        assert error.error_type == "infrastructure"

    def test_sweep_still_skips_after_a_user_abort(self):
        """The stale-output sweep keys on the abort, not the error type."""
        from types import SimpleNamespace

        from clm.build.engine import _maybe_run_sweep

        reporter = _reporter()
        reporter.mark_aborted(_vanished())
        config = SimpleNamespace(sweep=True, clean=False, watch=False)
        with patch("clm.build.output_sweep.sweep_stray_files") as sweep:
            sweep.return_value = MagicMock(skipped=True, skip_reason="errors")
            _maybe_run_sweep(
                config=config,
                root_dirs=[],
                backend=MagicMock(),
                build_reporter=reporter,
                only_sections_mode=False,
            )
        skip_reason = sweep.call_args.kwargs["skip_reason"]
        assert skip_reason is not None


# ---------------------------------------------------------------------------
# Item 3: one accurate worker-stop line.
# ---------------------------------------------------------------------------


def _worker_infos(n: int):
    from clm.infrastructure.workers.lifecycle_manager import WorkerInfo

    return [
        WorkerInfo(
            worker_type="notebook",
            execution_mode="direct",
            executor_id=f"w-{i}",
            db_worker_id=i + 1,
            started_at="2026-01-01T00:00:00",
            config={},
        )
        for i in range(n)
    ]


def _stop_records(caplog) -> list[str]:
    return [
        rec.getMessage()
        for rec in caplog.records
        if rec.levelno >= logging.INFO and "stopped" in rec.getMessage().lower()
    ]


def test_build_teardown_logs_one_accurate_stop_line(tmp_path, caplog):
    """Regression test for #1270 item 3: stopping a build's 10 workers logs
    exactly one "stopped" line, and it says 10 — never "Stopped 0"."""
    from clm.build.engine import stop_build_workers
    from clm.infrastructure.config import WorkersManagementConfig
    from clm.infrastructure.database.schema import init_database
    from clm.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager

    db_path = tmp_path / "jobs.db"
    init_database(db_path)
    config = MagicMock(spec=WorkersManagementConfig)
    config.auto_stop = True
    with patch("clm.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
        manager = WorkerLifecycleManager(config=config, db_path=db_path, workspace_path=tmp_path)
    manager.pool_manager = MagicMock()
    # As in a real build: start_managed_workers returns the manager's own
    # list, which stop_managed_workers clears.
    manager.managed_workers = _worker_infos(10)
    started_workers = manager.managed_workers

    with caplog.at_level(logging.INFO, logger="clm"):
        orphans = stop_build_workers(manager, started_workers)

    assert orphans == []
    lines = _stop_records(caplog)
    assert not any("Stopped 0" in line for line in lines), lines
    assert len(lines) == 1, lines
    assert "10 worker(s)" in lines[0]


def test_pool_manager_does_not_repeat_the_stop_summary(tmp_path, caplog):
    """The pool manager's own "Stopped N workers" line duplicated the
    lifecycle manager's pool_stopped event; it is debug-level now unless
    some workers failed to stop."""
    from clm.infrastructure.database.schema import init_database
    from clm.infrastructure.workers.pool_manager import WorkerPoolManager

    db_path = tmp_path / "jobs.db"
    init_database(db_path)
    with patch("clm.infrastructure.api.server.start_worker_api_server"):
        manager = WorkerPoolManager(db_path=db_path, workspace_path=tmp_path, worker_configs=[])
    executor = MagicMock()
    executor.stop_worker.return_value = True
    manager.workers = {
        "notebook": [
            {"executor": executor, "executor_id": f"w-{i}", "db_worker_id": i + 1} for i in range(3)
        ]
    }

    with (
        patch.object(manager, "_log_unhealthy_docker_worker_output"),
        caplog.at_level(logging.INFO, logger="clm.infrastructure.workers.pool_manager"),
    ):
        stopped = manager.stop_pools()

    assert stopped == 3
    assert _stop_records(caplog) == []


def test_pool_manager_reports_workers_that_failed_to_stop(tmp_path, caplog):
    from clm.infrastructure.database.schema import init_database
    from clm.infrastructure.workers.pool_manager import WorkerPoolManager

    db_path = tmp_path / "jobs.db"
    init_database(db_path)
    with patch("clm.infrastructure.api.server.start_worker_api_server"):
        manager = WorkerPoolManager(db_path=db_path, workspace_path=tmp_path, worker_configs=[])
    executor = MagicMock()
    executor.stop_worker.side_effect = [True, False, True]
    manager.workers = {
        "notebook": [
            {"executor": executor, "executor_id": f"w-{i}", "db_worker_id": i + 1} for i in range(3)
        ]
    }

    with (
        patch.object(manager, "_log_unhealthy_docker_worker_output"),
        caplog.at_level(logging.INFO, logger="clm.infrastructure.workers.pool_manager"),
    ):
        stopped = manager.stop_pools()

    assert stopped == 2
    assert _stop_records(caplog) == ["Stopped 2 of 3 workers"]
