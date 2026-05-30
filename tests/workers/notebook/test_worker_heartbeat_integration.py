"""Heartbeat-write coverage for the notebook execution path.

Two layers of tests live here:

1. **Unit-level** — drive ``TrackingExecutePreprocessor.preprocess_cell`` and
   ``process_message`` directly, with the real ``WorkerHeartbeatStore``
   pointing at a temp SQLite DB. This proves the wiring in the preprocessor
   updates the DB row with the right values without needing a live kernel.

2. **Integration-level (``slow`` marker)** — run a tiny 3-cell notebook
   through the real notebook worker pipeline (``NotebookProcessor`` +
   nbclient + a real ipykernel) and confirm heartbeats land in
   ``worker_heartbeats`` for the executing worker. This guards the public
   contract that a normal build emits per-cell beacons.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from nbformat.v4 import new_code_cell, new_notebook

from clm.infrastructure.database import worker_heartbeats as _wh
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.database.worker_heartbeats import WorkerHeartbeatStore
from clm.infrastructure.messaging.notebook_classes import NotebookPayload
from clm.workers.notebook.notebook_processor import (
    NotebookProcessor,
    TrackingExecutePreprocessor,
)
from clm.workers.notebook.output_spec import create_output_spec


@pytest.fixture(autouse=True)
def _relax_slow_write_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    # The production 50ms self-disable threshold legitimately trips under
    # xdist load (lock contention, WAL checkpoint, antivirus scan) and also
    # during the slow integration test below — a single SQLite write that
    # spikes past 50ms silently turns subsequent writes into no-ops and the
    # `expected at least one heartbeat row` assertion then fails with
    # `None is not None`. Lift the threshold for these tests; sibling
    # ``test_worker_heartbeats.py`` does the same.
    monkeypatch.setattr(_wh, "SLOW_WRITE_THRESHOLD_SECONDS", 30.0)


# -- shared helpers ----------------------------------------------------------


def _register_worker(db_path: Path, worker_id: int = 1) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO workers (id, worker_type, container_id, status) "
            "VALUES (?, 'notebook', ?, 'busy')",
            (worker_id, f"nb-test-{worker_id}"),
        )
        conn.commit()
    finally:
        conn.close()


def _read_heartbeat(db_path: Path, worker_id: int) -> dict | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT * FROM worker_heartbeats WHERE worker_id = ?",
            (worker_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "jobs.db"
    init_database(path)
    _register_worker(path, worker_id=1)
    return path


@pytest.fixture
def processor(db_path: Path) -> NotebookProcessor:
    """A NotebookProcessor with a real heartbeat store backed by ``db_path``."""
    store = WorkerHeartbeatStore(db_path, worker_id=1)
    spec = create_output_spec(
        kind="completed", prog_lang="python", language="en", format="notebook"
    )
    return NotebookProcessor(spec, heartbeat_store=store, heartbeat_job_id=999)


# -- unit-level wiring -------------------------------------------------------


class TestTrackingPreprocessorHeartbeatWiring:
    """``preprocess_cell`` must stamp a heartbeat row before the cell runs."""

    def test_preprocess_cell_writes_heartbeat_row(
        self, db_path: Path, processor: NotebookProcessor
    ) -> None:
        # Build a TrackingExecutePreprocessor and stub the super-call so we
        # don't spin up a kernel — we only care about the heartbeat side
        # effects of ``preprocess_cell`` itself.
        prep = TrackingExecutePreprocessor(processor, timeout=None)
        prep._total_cells = 5  # would be set by .preprocess() in real runs

        cell = {"cell_type": "code", "source": "x = 1\nprint(x)", "metadata": {}}
        resources: dict = {}

        # Patch the grandparent's preprocess_cell so we don't need a kernel.
        original = TrackingExecutePreprocessor.__mro__[1].preprocess_cell
        try:
            TrackingExecutePreprocessor.__mro__[1].preprocess_cell = (
                lambda self, cell, resources, cell_index: (cell, resources)
            )
            prep.preprocess_cell(cell, resources, cell_index=2)
        finally:
            TrackingExecutePreprocessor.__mro__[1].preprocess_cell = original

        row = _read_heartbeat(db_path, worker_id=1)
        assert row is not None
        assert row["current_cell_index"] == 2
        assert row["total_cells"] == 5
        assert row["job_id"] == 999
        assert row["current_cell_started_at"] is not None

    def test_process_message_records_stream_output(
        self, db_path: Path, processor: NotebookProcessor
    ) -> None:
        """A ``stream`` iopub message updates the last-output excerpt."""
        prep = TrackingExecutePreprocessor(processor, timeout=None)
        prep._total_cells = 3

        # Seed the row so process_message has an existing entry to UPDATE.
        processor.heartbeat_store.begin_cell(  # type: ignore[union-attr]
            job_id=999, cell_index=0, total_cells=3
        )

        # Bypass the nbclient super().process_message — we don't have a
        # notebook node to mutate. Replace it with a no-op for this test.
        original = TrackingExecutePreprocessor.__mro__[1].process_message
        try:
            TrackingExecutePreprocessor.__mro__[1].process_message = (
                lambda self, msg, cell, cell_index: None
            )
            msg = {
                "msg_type": "stream",
                "content": {"name": "stdout", "text": "training loss: 0.42\n"},
            }
            prep.process_message(msg, cell=MagicMock(), cell_index=0)
        finally:
            TrackingExecutePreprocessor.__mro__[1].process_message = original

        row = _read_heartbeat(db_path, worker_id=1)
        assert row is not None
        assert row["last_output_excerpt"] == "training loss: 0.42"
        assert row["last_output_at"] is not None

    def test_no_store_is_a_noop(self, db_path: Path) -> None:
        """A processor without a heartbeat store must not write rows."""
        spec = create_output_spec(
            kind="completed", prog_lang="python", language="en", format="notebook"
        )
        proc = NotebookProcessor(spec)  # no heartbeat_store
        prep = TrackingExecutePreprocessor(proc, timeout=None)
        prep._total_cells = 1

        cell = {"cell_type": "code", "source": "1", "metadata": {}}
        original = TrackingExecutePreprocessor.__mro__[1].preprocess_cell
        try:
            TrackingExecutePreprocessor.__mro__[1].preprocess_cell = (
                lambda self, cell, resources, cell_index: (cell, resources)
            )
            prep.preprocess_cell(cell, {}, cell_index=0)
        finally:
            TrackingExecutePreprocessor.__mro__[1].preprocess_cell = original

        # No worker registered; no row written, no exception.
        assert _read_heartbeat(db_path, worker_id=1) is None

    def test_preprocess_captures_total_cells(
        self, db_path: Path, processor: NotebookProcessor
    ) -> None:
        """The ``preprocess`` override snapshots ``len(nb.cells)`` once."""
        prep = TrackingExecutePreprocessor(processor, timeout=None)

        # Build a notebook with 4 cells and call ``preprocess`` with a
        # stubbed super so no kernel is launched.
        nb = new_notebook()
        nb["cells"] = [new_code_cell(source=str(i)) for i in range(4)]

        original = TrackingExecutePreprocessor.__mro__[1].preprocess
        try:
            TrackingExecutePreprocessor.__mro__[1].preprocess = (
                lambda self, nb, resources=None, km=None: (nb, resources or {})
            )
            prep.preprocess(nb)
        finally:
            TrackingExecutePreprocessor.__mro__[1].preprocess = original

        assert prep._total_cells == 4


class TestPerCellTimingInstrumentation:
    """Issue #143: per-cell timing logs around ``preprocess_cell`` so a
    stalling notebook is pinpointable in the build log."""

    def _run_cell(self, processor, *, cell_index=0, total=3):
        prep = TrackingExecutePreprocessor(processor, timeout=None)
        prep._total_cells = total
        processor._current_cid = "cid-143"
        cell = {"cell_type": "code", "source": "1", "metadata": {}}
        original = TrackingExecutePreprocessor.__mro__[1].preprocess_cell
        try:
            TrackingExecutePreprocessor.__mro__[1].preprocess_cell = (
                lambda self, cell, resources, cell_index: (cell, resources)
            )
            prep.preprocess_cell(cell, {}, cell_index=cell_index)
        finally:
            TrackingExecutePreprocessor.__mro__[1].preprocess_cell = original

    def test_emits_begin_and_done_debug_logs(
        self, db_path: Path, processor: NotebookProcessor, caplog
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="clm.workers.notebook.notebook_processor"):
            self._run_cell(processor, cell_index=7, total=12)

        messages = [r.getMessage() for r in caplog.records]
        assert any("cell 7/12 begin" in m for m in messages), messages
        assert any("cell 7/12 done in" in m for m in messages), messages
        # Correlation id is threaded through.
        assert any("cid-143" in m for m in messages), messages

    def test_slow_cell_logged_at_info(
        self, db_path: Path, processor: NotebookProcessor, monkeypatch, caplog
    ) -> None:
        import clm.workers.notebook.notebook_processor as npmod

        # Threshold of 0 forces the slow-cell INFO line for any cell.
        monkeypatch.setattr(npmod, "_SLOW_CELL_LOG_THRESHOLD_SECONDS", 0.0)
        with caplog.at_level(logging.INFO, logger="clm.workers.notebook.notebook_processor"):
            self._run_cell(processor)

        assert any("slow cell" in r.getMessage() for r in caplog.records), [
            r.getMessage() for r in caplog.records
        ]


def test_cell_execution_timeout_env_parsing(monkeypatch) -> None:
    """``CLM_CELL_TIMEOUT_SECONDS`` controls the opt-in per-cell timeout.

    Re-imports the module so the module-level constant is re-evaluated with
    the env var set, confirming the wiring (default None, positive int when
    set, None when invalid)."""
    import importlib

    import clm.workers.notebook.notebook_processor as npmod

    monkeypatch.setenv("CLM_CELL_TIMEOUT_SECONDS", "120")
    reloaded = importlib.reload(npmod)
    try:
        assert reloaded.CELL_EXECUTION_TIMEOUT == 120

        monkeypatch.delenv("CLM_CELL_TIMEOUT_SECONDS", raising=False)
        reloaded = importlib.reload(npmod)
        assert reloaded.CELL_EXECUTION_TIMEOUT is None
    finally:
        # Restore a clean module state for sibling tests.
        monkeypatch.delenv("CLM_CELL_TIMEOUT_SECONDS", raising=False)
        importlib.reload(npmod)


# -- integration: full notebook through real worker pipeline -----------------


@pytest.mark.slow
@pytest.mark.integration
def test_three_cell_notebook_emits_heartbeats(db_path: Path, tmp_path: Path) -> None:
    """A real 3-cell notebook executed via NotebookProcessor lands heartbeats.

    Marked ``slow`` + ``integration`` because it spins up a real ipykernel.
    The fast suite skips both markers, so this only runs with
    ``pytest -m "not docker"`` or equivalent.
    """
    pytest.importorskip("ipykernel")

    # Build a tiny percent-format Python "notebook" with three code cells.
    # Each cell prints a distinct marker so we can verify the last excerpt
    # corresponds to the final cell.
    nb_source = (
        "# %%\n"
        "print('cell 0 output')\n"
        "# %%\n"
        "x = 1 + 1\n"
        "print(f'cell 1 sees x = {x}')\n"
        "# %%\n"
        "print('cell 2 final')\n"
    )

    # Use ``format="html"``: only the HTML format goes through
    # ``_create_using_nbconvert`` which actually spawns a kernel and
    # invokes ``TrackingExecutePreprocessor`` (and therefore emits the
    # per-cell heartbeats we're testing here). ``format="notebook"``
    # goes through ``_create_using_jupytext`` and never executes any
    # cells, so no heartbeats fire.
    spec = create_output_spec(kind="completed", prog_lang="python", language="en", format="html")
    store = WorkerHeartbeatStore(db_path, worker_id=1)
    proc = NotebookProcessor(spec, heartbeat_store=store, heartbeat_job_id=4242)

    payload = NotebookPayload(
        data=nb_source,
        input_file=str(tmp_path / "tiny.py"),
        input_file_name="tiny.py",
        output_file=str(tmp_path / "tiny.html"),
        kind="completed",
        prog_lang="python",
        language="en",
        format="html",
        template_dir="",
        other_files={},
        correlation_id=f"hb-test-{uuid.uuid4().hex[:8]}",
    )

    import asyncio

    asyncio.run(proc.process_notebook(payload))

    row = _read_heartbeat(db_path, worker_id=1)
    assert row is not None, "expected at least one heartbeat row after execution"

    # The processor finished, so finish_job has NOT been called by the
    # worker (the integration test bypasses the worker class). We expect
    # the most recent state to point at the last cell.
    assert row["job_id"] == 4242
    assert row["total_cells"] == 3
    assert row["current_cell_index"] == 2  # 0-indexed, final cell

    # The last stream-output capture should reflect a print from the final
    # cell. We don't assert the exact text in case the kernel adds anything
    # — we only require it contains the final marker.
    assert row["last_output_excerpt"] is not None
    assert "cell 2 final" in row["last_output_excerpt"]
