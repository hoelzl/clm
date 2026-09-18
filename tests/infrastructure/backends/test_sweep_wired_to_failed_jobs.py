"""The wired #923 path: backend failure → stamped reporter → orchestrator
gate → real stray-file sweep on disk.

The unit tests around this feature exercise the halves in isolation (the
gate with a spied sweep, the sweeper with hand-built errors). This test is
the one that executes the whole chain, so the claim "a failed job's output
directory is protected while other strays are swept" is proven end to end
with the backend's real ``details["output_file"]`` stamp and real paths.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from attrs import frozen

from clm.build.config import BuildConfig
from clm.build.engine import _maybe_run_sweep
from clm.build.output_formatter import QuietOutputFormatter
from clm.build.reporter import BuildReporter
from clm.core.messaging.base_classes import Payload
from clm.core.operation import Operation
from clm.infrastructure.backends.sqlite_backend import SqliteBackend
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database


@frozen
class _Op(Operation):
    @property
    def service_name(self) -> str:
        return "notebook-processor"

    async def execute(self, backend, *args, **kwargs):
        pass


class _Payload(Payload):
    correlation_id: str = "wired-923"
    input_file: str = "slides_020_failing.py"
    input_file_name: str = "slides_020_failing.py"
    output_file: str
    data: str = "x"


def _config(output_dir: Path) -> BuildConfig:
    return BuildConfig(
        spec_file=Path("spec.xml"),
        data_dir=Path("data"),
        output_dir=output_dir,
        log_level="INFO",
        cache_db_path=Path("cache.db"),
        jobs_db_path=Path("jobs.db"),
        ignore_cache=False,
        clear_cache=False,
        watch=False,
        print_correlation_ids=False,
        workers=None,
        notebook_workers=None,
        plantuml_workers=None,
        drawio_workers=None,
        notebook_image=None,
        plantuml_image=None,
        drawio_image=None,
        sweep=True,
    )


@pytest.mark.asyncio
async def test_failed_job_directory_is_protected_and_other_strays_are_swept(tmp_path: Path):
    """Regression test for #923, wired end to end."""
    jobs_db = tmp_path / "jobs.db"
    init_database(jobs_db)
    root = tmp_path / "out"
    failed_output = root / "week2" / "slides_020_failing.ipynb"
    companion = root / "week2" / "slides_020_failing_files" / "plot.png"
    stray = root / "week1" / "old_deck.ipynb"
    for p in (companion, stray):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("stale", encoding="utf-8")

    reporter = BuildReporter(QuietOutputFormatter())
    backend = SqliteBackend(
        db_path=jobs_db,
        workspace_path=tmp_path,
        skip_worker_check=True,
        build_reporter=reporter,
    )
    try:
        await backend.execute_operation(_Op(), _Payload(output_file=str(failed_output)))
        job_id = next(iter(backend.active_jobs))

        async def fail_job() -> None:
            await asyncio.sleep(0.1)
            queue = JobQueue(jobs_db)
            try:
                queue.update_job_status(job_id, "failed", error="NameError: boom")
            finally:
                queue.close()

        task = asyncio.create_task(fail_job())
        assert await backend.wait_for_completion() is False
        await task

        # The backend stamped the unwritten output on the reported error …
        (error,) = reporter.errors
        assert Path(error.details["output_file"]) == failed_output

        # … so the orchestrator runs the sweep scoped around it.
        _maybe_run_sweep(
            config=_config(root),
            root_dirs=[root],
            backend=backend,
            build_reporter=reporter,
            only_sections_mode=False,
        )
    finally:
        await backend.shutdown()

    assert companion.exists(), "failed job's directory tree must survive"
    assert not stray.exists(), "stray outside the protected tree must be swept"
    assert not (root / "week1").exists()
