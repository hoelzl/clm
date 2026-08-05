"""Phase 7 item 3 (#801, T3): real, unmocked build-pipeline tests, fast suite.

The review's T3: the only fast-suite coverage of the build pipeline stubbed
``Course``, both backends, the reporter, and made ``execution_stages()``
return ``[]`` — so "the pipeline works" was asserted by tests that never ran
any of it. These tests run the REAL pipeline end to end with **zero mocks**:
a real parsed :class:`Course`, the real ``SqliteBackend`` against a temp DB,
real stage sequencing via ``Course.process_all``, and — in the worker test —
real job submission, claiming and completion through the queue by a real
direct-mode PlantUML worker process.

Deliberately kernel-free (no notebook execution) so the fast suite stays
fast; the notebook path's unmocked coverage is the e2e tier (the golden
double-build suite and the #681 replay gates). Worker startup and the JVM
render cost ~8s, isolated to one test and serialized in the ``subproc``
resource class.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from clm.core.course import Course
from clm.core.course_spec import CourseSpec

_TEST_DATA = Path(__file__).resolve().parent.parent / "test-data"

_DATA_ONLY_SPEC = """<course>
    <name><de>Datenkurs</de><en>Data Course</en></name>
    <prog-lang>python</prog-lang>
    <description><de>Nur Daten.</de><en>Data only.</en></description>
    <certificate><de>Kein.</de><en>None.</en></certificate>
    <project-slug>data-only</project-slug>
    <sections>
        <section>
            <name><de>Daten</de><en>Data</en></name>
            <topics><topic>just_data</topic></topics>
        </section>
    </sections>
</course>
"""


@pytest.mark.asyncio
async def test_data_only_course_flows_through_the_real_stages(tmp_path: Path) -> None:
    """Real Course, real SqliteBackend, real stage sequencing, real file I/O —
    no queue jobs needed (copies execute host-side), no mocks anywhere."""
    from clm.infrastructure.backends.sqlite_backend import SqliteBackend

    course_root = tmp_path / "course"
    topic = course_root / "slides" / "module_100_data" / "topic_100_just_data"
    topic.mkdir(parents=True)
    (topic / "payload.data").write_text("bytes that must arrive verbatim\n", encoding="utf-8")
    spec_path = course_root / "course-specs" / "spec.xml"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text(_DATA_ONLY_SPEC, encoding="utf-8")

    output_root = tmp_path / "out"
    spec = CourseSpec.from_file(spec_path)
    course = Course.from_spec(spec, course_root, output_root)

    backend = SqliteBackend(
        db_path=tmp_path / "jobs.db",
        workspace_path=tmp_path / "workspace",
        ignore_db=True,
        max_wait_for_completion_duration=60,
    )
    async with backend:
        await course.process_all(backend)

    copies = list(output_root.rglob("payload.data"))
    assert copies, "the data file must reach the output tree"
    for copy in copies:
        assert copy.read_text(encoding="utf-8") == "bytes that must arrive verbatim\n"
    # The temp DB is real and was really opened — the point of T3 is that
    # nothing on this path is a stub.
    assert (tmp_path / "jobs.db").is_file()


@pytest.mark.asyncio
@pytest.mark.serial("subproc")
async def test_plantuml_job_round_trips_through_the_real_queue(tmp_path: Path) -> None:
    """Real job submission against a temp DB, claimed and completed by a real
    direct-mode PlantUML worker process, output rendered — the exact seam the
    mocked tests never exercised (T3)."""
    from clm.infrastructure.backends.sqlite_backend import SqliteBackend
    from clm.infrastructure.workers.pool_manager import WorkerConfig, WorkerPoolManager

    course_root = tmp_path / "course"
    shutil.copytree(_TEST_DATA, course_root)
    output_root = tmp_path / "out"
    spec = CourseSpec.from_file(course_root / "course-specs" / "test-spec-4.xml")
    course = Course.from_spec(spec, course_root, output_root)

    db_path = tmp_path / "jobs.db"
    workspace = tmp_path / "workspace"
    backend = SqliteBackend(
        db_path=db_path,
        workspace_path=workspace,
        ignore_db=True,
        max_wait_for_completion_duration=120,
    )
    manager = WorkerPoolManager(
        db_path=db_path,
        workspace_path=workspace,
        worker_configs=[WorkerConfig(worker_type="plantuml", count=1, execution_mode="direct")],
    )
    manager.start_pools()
    try:
        deadline = time.monotonic() + 30
        while True:
            conn = manager.job_queue._get_conn()
            active = conn.execute(
                "SELECT COUNT(*) FROM workers WHERE status IN ('idle', 'busy')"
            ).fetchone()[0]
            if active >= 1:
                break
            assert time.monotonic() < deadline, "plantuml worker never became active"
            time.sleep(0.1)

        async with backend:
            await course.process_all(backend)
    finally:
        manager.stop_pools()
        manager.close()

    rendered = list(output_root.rglob("simple_diagram.png"))
    assert rendered, "the PlantUML render must reach the output tree"
    assert rendered[0].stat().st_size > 0

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()
    by_status = dict(rows)
    assert by_status.get("completed", 0) >= 1, (
        f"at least one job must round-trip the real queue; saw {by_status}"
    )
    assert not (set(by_status) - {"completed"}), f"non-terminal/failed jobs: {by_status}"
