"""Docker containment: what a container may mount and write (S10 + D7, #798).

Docker-mode workers bind-mount two host directories — the output tree at
``/workspace`` and the course sources at ``/source`` — and until now both
were read-write for every worker type, with only a partial guard against
mounting an entire drive.

Three things this pins:

* ``/source`` is **read-only for the notebook worker**. It reads slide
  sources and writes nothing there; PlantUML and DrawIO genuinely do
  write into the source tree (rendered diagrams), so they keep ``rw``.
* Neither mount may be a **whole volume**. A drive/filesystem root as
  the workspace or the data dir would hand the container everything on
  that disk.
* The "is a Docker notebook worker in this build?" probe must fail in
  the *safe* direction at each call site — and the two call sites have
  opposite safe directions, which is the subtle part.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.infrastructure.workers.worker_executor import DockerWorkerExecutor, WorkerConfig


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "jobs.db"
    path.write_bytes(b"")
    return path


@pytest.fixture
def workspace_path(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


def _volumes_for(worker_type: str, db_path: Path, workspace_path: Path, data_dir: Path):
    """Run ``start_worker`` against a fake client and return its volumes."""
    import docker.errors

    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.id = "deadbeefcafe"
    mock_client.containers.run.return_value = mock_container
    mock_client.containers.get.side_effect = docker.errors.NotFound("nope")

    executor = DockerWorkerExecutor(
        docker_client=mock_client,
        db_path=db_path,
        workspace_path=workspace_path,
        data_dir=data_dir,
    )
    executor.start_worker(
        worker_type,
        0,
        WorkerConfig(
            worker_type=worker_type,
            count=1,
            execution_mode="docker",
            image=f"{worker_type}:latest",
        ),
    )
    return mock_client.containers.run.call_args.kwargs["volumes"]


class TestSourceMountMode:
    @patch("docker.DockerClient")
    def test_notebook_worker_gets_source_read_only(
        self, _mock_docker, db_path: Path, workspace_path: Path, tmp_path: Path
    ) -> None:
        """The notebook worker reads sources and writes to /workspace only.

        It executes course-authored notebook code, so it is the worker
        most likely to be turned against the host — and the one with no
        reason to write into the course tree.
        """
        data_dir = tmp_path / "course"
        data_dir.mkdir()
        volumes = _volumes_for("notebook", db_path, workspace_path, data_dir)
        assert volumes[str(data_dir.absolute())] == {"bind": "/source", "mode": "ro"}

    @patch("docker.DockerClient")
    @pytest.mark.parametrize("worker_type", ["plantuml", "drawio"])
    def test_diagram_workers_keep_source_writable(
        self,
        _mock_docker,
        worker_type: str,
        db_path: Path,
        workspace_path: Path,
        tmp_path: Path,
    ) -> None:
        """They render images *into* the source tree — that is their job."""
        data_dir = tmp_path / "course"
        data_dir.mkdir()
        volumes = _volumes_for(worker_type, db_path, workspace_path, data_dir)
        assert volumes[str(data_dir.absolute())] == {"bind": "/source", "mode": "rw"}

    @patch("docker.DockerClient")
    def test_the_workspace_stays_writable_for_the_notebook_worker(
        self, _mock_docker, db_path: Path, workspace_path: Path, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "course"
        data_dir.mkdir()
        volumes = _volumes_for("notebook", db_path, workspace_path, data_dir)
        assert volumes[str(workspace_path.absolute())]["mode"] == "rw"


class TestContainerUser:
    """Who the container runs as, and why it is not always the image default."""

    def _run_kwargs(self, db_path: Path, workspace_path: Path):
        import docker.errors

        mock_client = MagicMock()
        mock_client.containers.run.return_value = MagicMock(id="deadbeefcafe")
        mock_client.containers.get.side_effect = docker.errors.NotFound("nope")
        executor = DockerWorkerExecutor(
            docker_client=mock_client, db_path=db_path, workspace_path=workspace_path
        )
        executor.start_worker(
            "notebook",
            0,
            WorkerConfig(
                worker_type="notebook", count=1, execution_mode="docker", image="nb:latest"
            ),
        )
        return mock_client.containers.run.call_args.kwargs

    @patch("docker.DockerClient")
    def test_the_resolved_user_reaches_docker(
        self, _mock_docker, db_path: Path, workspace_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whatever ``_container_user`` decides is what the container runs as."""
        import clm.infrastructure.workers.worker_executor as we

        monkeypatch.setattr(we, "_container_user", lambda: "1001:1002")
        assert self._run_kwargs(db_path, workspace_path)["user"] == "1001:1002"

    @patch("docker.DockerClient")
    def test_no_user_override_leaves_the_image_default(
        self, _mock_docker, db_path: Path, workspace_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Docker Desktop virtualizes the mount; the image's USER stands."""
        import clm.infrastructure.workers.worker_executor as we

        monkeypatch.setattr(we, "_container_user", lambda: None)
        assert "user" not in self._run_kwargs(db_path, workspace_path)

    def test_posix_hosts_pin_the_host_uid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A bind mount keeps host ownership on native Linux.

        The images default to uid 1000, but a GitHub runner is 1001 — a
        container writing output into the mount as 1000 simply fails. So
        the executor pins the host uid rather than the image baking one in.

        ``os.name`` is read through a module-level indirection rather than
        patched globally: patching ``os.name`` to "posix" on Windows also
        makes ``pathlib`` try to build ``PosixPath`` objects.
        """
        from clm.infrastructure.workers import worker_executor as we

        monkeypatch.setattr(we, "_HOST_IS_POSIX", True)
        monkeypatch.setattr(we.os, "getuid", lambda: 1001, raising=False)
        monkeypatch.setattr(we.os, "getgid", lambda: 1002, raising=False)
        assert we._container_user() == "1001:1002"

    def test_non_posix_hosts_return_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clm.infrastructure.workers import worker_executor as we

        monkeypatch.setattr(we, "_HOST_IS_POSIX", False)
        assert we._container_user() is None

    def test_running_clm_as_root_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """uid 0 still works, but the containment is gone — say so."""
        import logging

        from clm.infrastructure.workers import worker_executor as we

        monkeypatch.setattr(we, "_HOST_IS_POSIX", True)
        monkeypatch.setattr(we.os, "getuid", lambda: 0, raising=False)
        monkeypatch.setattr(we.os, "getgid", lambda: 0, raising=False)

        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = _Capture()
        we.logger.addHandler(handler)
        try:
            assert we._container_user() == "0:0"
        finally:
            we.logger.removeHandler(handler)

        assert any(r.levelno >= logging.WARNING and "root" in r.getMessage() for r in records)


class TestWholeVolumeMountsAreRefused:
    """Neither mount root may be a drive or filesystem root."""

    def _root_of(self, path: Path) -> Path:
        resolved = path.resolve()
        return Path(resolved.anchor)

    @patch("docker.DockerClient")
    def test_a_drive_root_data_dir_is_refused(
        self, _mock_docker, db_path: Path, workspace_path: Path, tmp_path: Path
    ) -> None:
        """``<data-dir>`` had no whole-volume guard at all.

        ``course.workspace_root`` guarded the output side; the source
        mount was passed through untouched, so a course root of ``C:\\``
        or ``/`` mounted the whole disk at ``/source``.
        """
        with pytest.raises(ValueError, match="whole"):
            DockerWorkerExecutor(
                docker_client=MagicMock(),
                db_path=db_path,
                workspace_path=workspace_path,
                data_dir=self._root_of(tmp_path),
            )

    @patch("docker.DockerClient")
    def test_a_drive_root_workspace_is_refused(
        self, _mock_docker, db_path: Path, tmp_path: Path
    ) -> None:
        with pytest.raises(ValueError, match="whole"):
            DockerWorkerExecutor(
                docker_client=MagicMock(),
                db_path=db_path,
                workspace_path=self._root_of(tmp_path),
            )

    @patch("docker.DockerClient")
    def test_ordinary_paths_are_accepted(
        self, _mock_docker, db_path: Path, workspace_path: Path, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "course"
        data_dir.mkdir()
        executor = DockerWorkerExecutor(
            docker_client=MagicMock(),
            db_path=db_path,
            workspace_path=workspace_path,
            data_dir=data_dir,
        )
        assert executor.data_dir == data_dir


class TestWorkspaceRootGuard:
    """``Course.workspace_root`` — the output-side guard (gap 1)."""

    def _course(self, roots: list[Path]):
        from clm.core.course import Course

        course = Course.__new__(Course)
        course.output_targets = [MagicMock(output_root=root) for root in roots]
        course.output_root = roots[0]
        return course

    def test_a_single_drive_root_target_is_refused(self, tmp_path: Path) -> None:
        """The single-target early return skipped the guard entirely.

        ``len(resolved) == 1`` returned before the whole-volume check, so
        one target at ``C:\\`` mounted the drive — while *two* targets
        that merely shared it were correctly refused.
        """
        drive_root = Path(tmp_path.resolve().anchor)
        with pytest.raises(ValueError, match="whole"):
            _ = self._course([drive_root]).workspace_root

    def test_a_single_ordinary_target_is_unchanged(self, tmp_path: Path) -> None:
        root = tmp_path / "output"
        assert self._course([root]).workspace_root == root.resolve()

    def test_two_targets_sharing_a_drive_root_are_still_refused(self, tmp_path: Path) -> None:
        drive_root = Path(tmp_path.resolve().anchor)
        with pytest.raises(ValueError, match="whole"):
            _ = self._course([drive_root / "a", drive_root / "b"]).workspace_root


class TestUndeterminableWorkerModeFailsSafe:
    """The probe's two consumers have *opposite* safe directions."""

    def _exploding_config(self):
        config = MagicMock()
        config.get_all_worker_configs.side_effect = RuntimeError("cannot enumerate")
        return config

    def test_the_workspace_guard_applies_when_the_mode_is_unknown(self, tmp_path: Path) -> None:
        """Assume Docker: that routes through the guarded ``workspace_root``.

        Guessing "Direct" would silently return the unguarded
        ``output_root`` — the guard disabled by an exception nobody sees.
        """
        from clm.build.engine import _resolve_worker_workspace_path

        course = MagicMock()
        course.workspace_root = tmp_path / "guarded"
        course.output_root = tmp_path / "unguarded"

        assert (
            _resolve_worker_workspace_path(course, self._exploding_config())
            == course.workspace_root
        )

    def test_the_proxy_keeps_its_loopback_bind_when_the_mode_is_unknown(self) -> None:
        """Assume Direct: binding 0.0.0.0 would open a LAN listener.

        Same unknown, opposite safe answer — "fail safe" is a direction,
        not a constant, and the recording proxy's wildcard bind is the
        one place where assuming Docker makes things *less* contained.
        """
        from clm.build.engine import _build_has_docker_notebook_worker

        assert (
            _build_has_docker_notebook_worker(self._exploding_config(), default_on_error=False)
            is False
        )
        assert (
            _build_has_docker_notebook_worker(self._exploding_config(), default_on_error=True)
            is True
        )

    def test_a_resolvable_config_is_unaffected(self) -> None:
        from clm.build.engine import _build_has_docker_notebook_worker

        config = MagicMock()
        config.get_all_worker_configs.return_value = [
            MagicMock(worker_type="notebook", execution_mode="docker", count=1)
        ]
        assert _build_has_docker_notebook_worker(config, default_on_error=False) is True

        config.get_all_worker_configs.return_value = [
            MagicMock(worker_type="drawio", execution_mode="docker", count=1)
        ]
        assert _build_has_docker_notebook_worker(config, default_on_error=True) is False
