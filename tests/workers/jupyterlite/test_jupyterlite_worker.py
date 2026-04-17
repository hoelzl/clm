"""Unit tests for the JupyterLite worker lifecycle.

These tests cover payload unpacking, cancellation handling, cache
writes, the sync ``process_job`` wrapper, and ``main()``'s
SQLite-vs-API branching.  Heavy dependencies (``build_site``,
``init_database``, ``Worker.run``) are monkeypatched so the tests
stay in the fast suite.
"""

from __future__ import annotations

import gc
import json
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from clm.infrastructure.database.job_queue import Job, JobQueue
from clm.infrastructure.database.schema import init_database
from clm.workers.jupyterlite import jupyterlite_worker as worker_module
from clm.workers.jupyterlite.builder import BuildResult
from clm.workers.jupyterlite.jupyterlite_worker import JupyterLiteWorker


@pytest.fixture
def db_path(tmp_path: Path):
    """Create an initialized jobs DB in a tmp dir and clean up WAL files."""
    path = tmp_path / "jobs.db"
    init_database(path)
    yield path

    # Windows-friendly teardown.
    gc.collect()
    try:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass
    for suffix in ["", "-wal", "-shm"]:
        try:
            (Path(str(path) + suffix)).unlink(missing_ok=True)
        except PermissionError:
            time.sleep(0.1)
            try:
                (Path(str(path) + suffix)).unlink(missing_ok=True)
            except Exception:
                pass


@pytest.fixture
def worker_id(db_path: Path) -> int:
    """Register a jupyterlite worker row and return its id."""
    with JobQueue(db_path) as queue:
        conn = queue._get_conn()
        cursor = conn.execute(
            "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
            ("jupyterlite", "test-container-jlw", "idle"),
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]


def _make_job(
    *,
    job_id: int = 1,
    payload: dict[str, Any] | None = None,
    output_file: str = "/out/site",
    content_hash: str = "hash-abc",
) -> Job:
    return Job(
        id=job_id,
        job_type="jupyterlite",
        status="processing",
        input_file="course-spec.xml",
        output_file=output_file,
        content_hash=content_hash,
        payload=payload or {},
        created_at=datetime.now(),
    )


def _fake_build_result(site_dir: Path) -> BuildResult:
    manifest = site_dir / "_output" / "manifest.json"
    return BuildResult(
        site_dir=site_dir / "_output",
        manifest_path=manifest,
        cache_key="deadbeef" * 8,  # 64-char hex
        files_count=42,
    )


class TestJupyterLiteWorkerInit:
    """Worker identifies itself correctly in both modes."""

    def test_sqlite_mode_initializes(self, worker_id: int, db_path: Path) -> None:
        worker = JupyterLiteWorker(worker_id, db_path=db_path)
        assert worker.worker_type == "jupyterlite"
        assert worker.db_path == db_path
        assert worker.api_url is None
        assert worker._api_mode is False

    def test_api_mode_initializes(self, monkeypatch: pytest.MonkeyPatch, worker_id: int) -> None:
        # ApiJobQueue is constructed in __init__; stub it out so we don't
        # need a live HTTP server.
        class _StubApi:
            def __init__(self, url: str, wid: int) -> None:  # noqa: D401
                self.url = url
                self.wid = wid

        monkeypatch.setattr("clm.infrastructure.api.job_queue_adapter.ApiJobQueue", _StubApi)
        worker = JupyterLiteWorker(worker_id, api_url="http://localhost:1234")
        assert worker._api_mode is True
        assert worker.api_url == "http://localhost:1234"


class TestProcessJobAsync:
    """_process_job_async should unpack the payload and call build_site."""

    async def test_unpacks_payload_and_caches_result(
        self,
        worker_id: int,
        db_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        site_dir = tmp_path / "site"
        site_dir.mkdir()

        captured: dict[str, Any] = {}

        def fake_build_site(args):
            captured["args"] = args
            return _fake_build_result(site_dir)

        monkeypatch.setattr("clm.workers.jupyterlite.builder.build_site", fake_build_site)

        wheels_dir = tmp_path / "wheels"
        wheels_dir.mkdir()
        wheel1 = wheels_dir / "pkg-1.0-py3-none-any.whl"
        wheel1.write_bytes(b"")

        env_yml = tmp_path / "environment.yml"
        env_yml.write_text("name: base\n", encoding="utf-8")

        nb_tree_dir = tmp_path / "notebooks"
        nb_tree_dir.mkdir()

        payload: dict[str, Any] = {
            "input_file_name": "python-best-practice/en/completed",
            "wheels": [str(wheel1)],
            "environment_yml": str(env_yml),
            "notebook_trees": {"code-along": str(nb_tree_dir)},
            "output_dir": str(site_dir),
            "kernel": "pyodide",
            "app_archive": "offline",
            "launcher": "python",
            "jupyterlite_core_version": "0.7.4",
            "branding_theme": "clm",
            "branding_logo": "",
            "branding_site_name": "CLM",
        }

        job = _make_job(
            job_id=7,
            payload=payload,
            output_file="/out/py-best-practice-site",
            content_hash="hash-7",
        )

        worker = JupyterLiteWorker(worker_id, db_path=db_path)
        await worker._process_job_async(job)

        args = captured["args"]
        # Primitive fields forward verbatim.
        assert args.kernel == "pyodide"
        assert args.app_archive == "offline"
        assert args.launcher == "python"
        assert args.jupyterlite_core_version == "0.7.4"
        assert args.branding_theme == "clm"
        assert args.branding_site_name == "CLM"
        # String paths get coerced to Path.
        assert args.output_dir == site_dir
        assert args.environment_yml == env_yml
        assert args.wheels == [wheel1]
        assert args.notebook_trees == {"code-along": nb_tree_dir}
        # target_label falls back to input_file_name when set.
        assert args.target_label == payload["input_file_name"]

        # Cache was written with summary JSON.
        conn = worker.job_queue._get_conn()
        row = conn.execute(
            "SELECT result_metadata FROM results_cache WHERE output_file = ?",
            ("/out/py-best-practice-site",),
        ).fetchone()
        assert row is not None
        cached = json.loads(row[0])
        assert cached["cache_key"].startswith("deadbeef")
        assert cached["files_count"] == 42
        assert json.loads(cached["summary"])["files_count"] == 42

    async def test_missing_input_file_name_uses_job_id(
        self,
        worker_id: int,
        db_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        site_dir = tmp_path / "site"
        site_dir.mkdir()
        captured: dict[str, Any] = {}

        def fake_build_site(args):
            captured["args"] = args
            return _fake_build_result(site_dir)

        monkeypatch.setattr("clm.workers.jupyterlite.builder.build_site", fake_build_site)

        job = _make_job(
            job_id=99,
            payload={
                "wheels": [],
                "notebook_trees": {},
                "output_dir": str(site_dir),
                "kernel": "python",
            },
        )
        worker = JupyterLiteWorker(worker_id, db_path=db_path)
        await worker._process_job_async(job)
        assert captured["args"].target_label == "99"

    async def test_empty_environment_yml_becomes_none(
        self,
        worker_id: int,
        db_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        site_dir = tmp_path / "site"
        site_dir.mkdir()
        captured: dict[str, Any] = {}

        def fake_build_site(args):
            captured["args"] = args
            return _fake_build_result(site_dir)

        monkeypatch.setattr("clm.workers.jupyterlite.builder.build_site", fake_build_site)

        job = _make_job(
            payload={
                "wheels": [],
                "notebook_trees": {},
                "output_dir": str(site_dir),
                "kernel": "pyodide",
                "environment_yml": "",  # empty string
            },
        )
        worker = JupyterLiteWorker(worker_id, db_path=db_path)
        await worker._process_job_async(job)
        assert captured["args"].environment_yml is None

    async def test_defaults_for_optional_fields(
        self,
        worker_id: int,
        db_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        site_dir = tmp_path / "site"
        site_dir.mkdir()
        captured: dict[str, Any] = {}

        def fake_build_site(args):
            captured["args"] = args
            return _fake_build_result(site_dir)

        monkeypatch.setattr("clm.workers.jupyterlite.builder.build_site", fake_build_site)

        # Payload with only required fields.
        job = _make_job(
            payload={
                "output_dir": str(site_dir),
                "kernel": "pyodide",
            },
        )
        worker = JupyterLiteWorker(worker_id, db_path=db_path)
        await worker._process_job_async(job)
        args = captured["args"]
        assert args.wheels == []
        assert args.notebook_trees == {}
        assert args.environment_yml is None
        assert args.app_archive == "offline"
        assert args.launcher == "python"
        assert args.jupyterlite_core_version == ""
        assert args.branding_theme == ""
        assert args.branding_logo == ""
        assert args.branding_site_name == ""

    async def test_cancelled_job_skips_build(
        self,
        worker_id: int,
        db_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        build_calls: list[Any] = []

        def fake_build_site(args):
            build_calls.append(args)
            return _fake_build_result(tmp_path)

        monkeypatch.setattr("clm.workers.jupyterlite.builder.build_site", fake_build_site)

        worker = JupyterLiteWorker(worker_id, db_path=db_path)
        monkeypatch.setattr(worker.job_queue, "is_job_cancelled", lambda _jid: True)
        job = _make_job(payload={"output_dir": str(tmp_path), "kernel": "pyodide"})
        await worker._process_job_async(job)

        assert build_calls == [], "build_site must not run for cancelled job"

    async def test_build_error_propagates(
        self,
        worker_id: int,
        db_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fake_build_site(_args):
            raise RuntimeError("boom")

        monkeypatch.setattr("clm.workers.jupyterlite.builder.build_site", fake_build_site)

        worker = JupyterLiteWorker(worker_id, db_path=db_path)
        job = _make_job(payload={"output_dir": str(tmp_path), "kernel": "pyodide"})
        with pytest.raises(RuntimeError, match="boom"):
            await worker._process_job_async(job)


class TestProcessJobSync:
    """process_job (sync wrapper) drives the async variant."""

    def test_sync_wrapper_runs_async(
        self,
        worker_id: int,
        db_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        site_dir = tmp_path / "site"
        site_dir.mkdir()

        monkeypatch.setattr(
            "clm.workers.jupyterlite.builder.build_site",
            lambda _args: _fake_build_result(site_dir),
        )

        worker = JupyterLiteWorker(worker_id, db_path=db_path)
        job = _make_job(payload={"output_dir": str(site_dir), "kernel": "pyodide"})
        # Should return without error.
        worker.process_job(job)

    def test_sync_wrapper_reraises_on_failure(
        self,
        worker_id: int,
        db_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fake_build_site(_args):
            raise ValueError("fail")

        monkeypatch.setattr("clm.workers.jupyterlite.builder.build_site", fake_build_site)

        worker = JupyterLiteWorker(worker_id, db_path=db_path)
        job = _make_job(payload={"output_dir": str(tmp_path), "kernel": "pyodide"})
        with pytest.raises(ValueError, match="fail"):
            worker.process_job(job)


class TestMainEntryPoint:
    """main() routes between SQLite and API modes based on env."""

    def test_main_sqlite_mode_initializes_db(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "new-jobs.db"
        init_calls: list[Path] = []
        register_calls: list[dict[str, Any]] = []
        run_calls: list[JupyterLiteWorker] = []

        def fake_init_database(path: Path) -> None:
            init_calls.append(path)

        def fake_register(*, db_path, api_url, worker_type):
            register_calls.append(
                {"db_path": db_path, "api_url": api_url, "worker_type": worker_type}
            )
            return 42

        # Don't actually start the run loop.
        def fake_run(self: JupyterLiteWorker) -> None:
            run_calls.append(self)

        def fake_cleanup(self: JupyterLiteWorker) -> None:
            pass

        monkeypatch.setattr(worker_module, "init_database", fake_init_database)
        monkeypatch.setattr(worker_module, "API_URL", None, raising=True)
        monkeypatch.setattr(worker_module, "DB_PATH", db_path, raising=True)
        monkeypatch.setattr(
            "clm.infrastructure.workers.worker_base.Worker.get_or_register_worker",
            staticmethod(fake_register),
        )
        monkeypatch.setattr(JupyterLiteWorker, "run", fake_run, raising=False)
        monkeypatch.setattr(JupyterLiteWorker, "cleanup", fake_cleanup, raising=False)

        # DB doesn't exist yet — init_database should be called.
        worker_module.main()

        assert init_calls == [db_path]
        assert register_calls[0]["db_path"] == db_path
        assert register_calls[0]["api_url"] is None
        assert register_calls[0]["worker_type"] == "jupyterlite"
        assert len(run_calls) == 1

    def test_main_api_mode_skips_db_init(self, monkeypatch: pytest.MonkeyPatch) -> None:
        init_calls: list[Path] = []
        register_calls: list[dict[str, Any]] = []

        def fake_init_database(path: Path) -> None:
            init_calls.append(path)

        def fake_register(*, db_path, api_url, worker_type):
            register_calls.append(
                {"db_path": db_path, "api_url": api_url, "worker_type": worker_type}
            )
            return 7

        class _StubApi:
            def __init__(self, url: str, wid: int) -> None:  # noqa: D401
                self.url = url

        monkeypatch.setattr(worker_module, "init_database", fake_init_database)
        monkeypatch.setattr(worker_module, "API_URL", "http://api", raising=True)
        monkeypatch.setattr("clm.infrastructure.api.job_queue_adapter.ApiJobQueue", _StubApi)
        monkeypatch.setattr(
            "clm.infrastructure.workers.worker_base.Worker.get_or_register_worker",
            staticmethod(fake_register),
        )
        monkeypatch.setattr(JupyterLiteWorker, "run", lambda self: None, raising=False)
        monkeypatch.setattr(JupyterLiteWorker, "cleanup", lambda self: None, raising=False)

        worker_module.main()

        assert init_calls == [], "API mode must not initialize a local DB"
        assert register_calls[0]["api_url"] == "http://api"
        assert register_calls[0]["db_path"] is None

    def test_main_keyboard_interrupt_stops_worker(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "jobs.db"
        init_database(db_path)

        stop_calls: list[JupyterLiteWorker] = []
        cleanup_calls: list[JupyterLiteWorker] = []

        def fake_register(*, db_path, api_url, worker_type):
            return 1

        def fake_run(self: JupyterLiteWorker) -> None:
            raise KeyboardInterrupt

        def fake_stop(self: JupyterLiteWorker) -> None:
            stop_calls.append(self)

        def fake_cleanup(self: JupyterLiteWorker) -> None:
            cleanup_calls.append(self)

        monkeypatch.setattr(worker_module, "API_URL", None, raising=True)
        monkeypatch.setattr(worker_module, "DB_PATH", db_path, raising=True)
        monkeypatch.setattr(
            "clm.infrastructure.workers.worker_base.Worker.get_or_register_worker",
            staticmethod(fake_register),
        )
        monkeypatch.setattr(JupyterLiteWorker, "run", fake_run, raising=False)
        monkeypatch.setattr(JupyterLiteWorker, "stop", fake_stop, raising=False)
        monkeypatch.setattr(JupyterLiteWorker, "cleanup", fake_cleanup, raising=False)

        # Should not raise — KeyboardInterrupt is swallowed.
        worker_module.main()

        assert len(stop_calls) == 1
        assert len(cleanup_calls) == 1

    def test_main_unexpected_exception_reraises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "jobs.db"
        init_database(db_path)

        def fake_register(*, db_path, api_url, worker_type):
            return 1

        def fake_run(self: JupyterLiteWorker) -> None:
            raise RuntimeError("unexpected crash")

        cleanup_calls: list[JupyterLiteWorker] = []

        monkeypatch.setattr(worker_module, "API_URL", None, raising=True)
        monkeypatch.setattr(worker_module, "DB_PATH", db_path, raising=True)
        monkeypatch.setattr(
            "clm.infrastructure.workers.worker_base.Worker.get_or_register_worker",
            staticmethod(fake_register),
        )
        monkeypatch.setattr(JupyterLiteWorker, "run", fake_run, raising=False)
        monkeypatch.setattr(
            JupyterLiteWorker,
            "cleanup",
            lambda self: cleanup_calls.append(self),
            raising=False,
        )

        with pytest.raises(RuntimeError, match="unexpected crash"):
            worker_module.main()

        # Cleanup still runs via the `finally` block.
        assert len(cleanup_calls) == 1
