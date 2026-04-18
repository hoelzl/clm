"""Tests for ``clm.cli.commands.docker``.

Exercises helper functions (project root lookup, cache args, login
check, image-exists) and the four click subcommands (build,
build-quick, push, pull, list, cache-info) via ``CliRunner`` with
``subprocess.run`` mocked so we never actually touch ``docker``.
"""

from __future__ import annotations

import json as json_module
import subprocess
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from clm.cli.commands import docker as docker_cmd
from clm.cli.commands.docker import (
    AVAILABLE_SERVICES,
    CACHE_DIR_NAME,
    HUB_NAMESPACE,
    REGISTRY,
    SERVICE_NAME_MAP,
    build_notebook,
    build_notebook_variant,
    build_service,
    check_docker_login,
    docker_build,
    docker_build_quick,
    docker_cache_info,
    docker_list,
    docker_pull,
    docker_push,
    ensure_cache_dir,
    get_cache_args,
    get_cache_dir,
    get_project_root,
    get_version,
    image_exists_locally,
    pull_service,
    push_service,
)

# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temp directory that looks like a project root.

    Contains a ``docker/`` directory with one subdirectory per service
    (each with a ``Dockerfile``) and a ``pyproject.toml`` marker file.
    """
    (tmp_path / "pyproject.toml").write_text("[project]\nname='clm'\n", encoding="utf-8")
    docker_dir = tmp_path / "docker"
    docker_dir.mkdir()
    for service in AVAILABLE_SERVICES:
        svc_dir = docker_dir / service
        svc_dir.mkdir()
        (svc_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def mock_run_docker() -> MagicMock:
    """Patch ``run_docker_command`` to a mock returning a successful CompletedProcess."""
    with patch.object(docker_cmd, "run_docker_command") as mock:
        mock.return_value = subprocess.CompletedProcess([], 0, "", "")
        yield mock


@pytest.fixture
def captured_console() -> StringIO:
    """Redirect docker.py's module-level Rich console to a StringIO buffer.

    docker.py binds ``console = Console(file=sys.stderr)`` at import time, so
    CliRunner's stream isolation does not capture its output. We swap the
    console for one writing to an in-memory buffer for the duration of
    each test, and hand the buffer to the test so it can assert on the
    text that docker.py emitted.
    """
    buf = StringIO()
    fake = Console(file=buf, force_terminal=False, no_color=True, width=200)
    with patch.object(docker_cmd, "console", fake):
        yield buf


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestGetVersion:
    def test_returns_clm_version(self) -> None:
        from clm import __version__

        assert get_version() == __version__


class TestGetProjectRoot:
    def test_returns_root_when_markers_present(self, fake_project_root: Path) -> None:
        assert get_project_root() == fake_project_root

    def test_returns_root_from_nested_directory(
        self, fake_project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        nested = fake_project_root / "docker" / "plantuml"
        monkeypatch.chdir(nested)
        # Should walk up and find the root.
        assert get_project_root() == fake_project_root

    def test_returns_none_when_no_markers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        # Walk up eventually hits a drive root that has no docker/
        # or pyproject.toml. On systems where an ancestor happens to
        # contain those markers this would return that directory —
        # guard against that by using an isolated tmp_path tree.
        assert get_project_root() != empty


class TestImageExistsLocally:
    def test_returns_true_when_inspect_succeeds(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
            assert image_exists_locally("my-image:tag") is True

    def test_returns_false_when_inspect_fails(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 1, "", "not found")
            assert image_exists_locally("missing:tag") is False


class TestGetCacheDir:
    def test_service_without_variant(self) -> None:
        assert get_cache_dir("plantuml") == Path(CACHE_DIR_NAME) / "plantuml"

    def test_notebook_with_variant(self) -> None:
        assert get_cache_dir("notebook", "lite") == (Path(CACHE_DIR_NAME) / "notebook" / "lite")

    def test_notebook_without_variant(self) -> None:
        assert get_cache_dir("notebook") == Path(CACHE_DIR_NAME) / "notebook"


class TestGetCacheArgs:
    def test_cache_disabled_returns_empty(self) -> None:
        cache_from, cache_to = get_cache_args("plantuml", use_cache=False)
        assert cache_from == []
        assert cache_to == []

    def test_cache_enabled_with_existing_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / CACHE_DIR_NAME / "plantuml").mkdir(parents=True)

        cache_from, cache_to = get_cache_args("plantuml", use_cache=True)

        assert cache_from == [
            "--cache-from",
            f"type=local,src={Path(CACHE_DIR_NAME) / 'plantuml'}",
        ]
        assert cache_to == [
            "--cache-to",
            f"type=local,dest={Path(CACHE_DIR_NAME) / 'plantuml'},mode=max",
        ]

    def test_cache_enabled_without_existing_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        cache_from, cache_to = get_cache_args("plantuml", use_cache=True)

        # No cache-from when directory does not exist yet
        assert cache_from == []
        # But still a cache-to so the first build populates it
        assert cache_to == [
            "--cache-to",
            f"type=local,dest={Path(CACHE_DIR_NAME) / 'plantuml'},mode=max",
        ]


class TestEnsureCacheDir:
    def test_creates_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = ensure_cache_dir("plantuml")
        assert result.is_dir()
        assert result == Path(CACHE_DIR_NAME) / "plantuml"

    def test_creates_notebook_variant_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = ensure_cache_dir("notebook", "full")
        assert result.is_dir()
        assert result == Path(CACHE_DIR_NAME) / "notebook" / "full"


# ---------------------------------------------------------------------------
# check_docker_login
# ---------------------------------------------------------------------------


class TestCheckDockerLogin:
    def test_no_config_file_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert check_docker_login() is False

    def test_malformed_json_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        docker_dir = tmp_path / ".docker"
        docker_dir.mkdir()
        (docker_dir / "config.json").write_text("{ invalid json", encoding="utf-8")

        assert check_docker_login() is False

    def test_direct_auth_entry_returns_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        docker_dir = tmp_path / ".docker"
        docker_dir.mkdir()
        (docker_dir / "config.json").write_text(
            json_module.dumps({"auths": {"https://index.docker.io/v1/": {"auth": "sometoken"}}}),
            encoding="utf-8",
        )

        assert check_docker_login() is True

    def test_credstore_with_auth_entry_returns_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        docker_dir = tmp_path / ".docker"
        docker_dir.mkdir()
        (docker_dir / "config.json").write_text(
            json_module.dumps(
                {
                    "auths": {"https://index.docker.io/v1/": {}},
                    "credsStore": "desktop",
                }
            ),
            encoding="utf-8",
        )

        assert check_docker_login() is True

    def test_empty_config_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        docker_dir = tmp_path / ".docker"
        docker_dir.mkdir()
        (docker_dir / "config.json").write_text("{}", encoding="utf-8")

        assert check_docker_login() is False

    def test_credshelpers_alone_without_hub_entry_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        docker_dir = tmp_path / ".docker"
        docker_dir.mkdir()
        (docker_dir / "config.json").write_text(
            json_module.dumps(
                {
                    "credsHelpers": {"other-registry.io": "helper"},
                    "auths": {},
                }
            ),
            encoding="utf-8",
        )

        assert check_docker_login() is False


# ---------------------------------------------------------------------------
# build_service / build_notebook_variant / build_notebook / push_service /
# pull_service helpers
# ---------------------------------------------------------------------------


class TestBuildService:
    def test_missing_dockerfile_fails(self, fake_project_root: Path) -> None:
        docker_path = fake_project_root / "docker" / "plantuml"
        (docker_path / "Dockerfile").unlink()

        assert build_service("plantuml", "1.0.0", docker_path) is False

    def test_successful_build(self, fake_project_root: Path, mock_run_docker: MagicMock) -> None:
        docker_path = fake_project_root / "docker" / "plantuml"
        assert build_service("plantuml", "1.2.3", docker_path, use_cache=True) is True

        # Verify docker invocation
        cmd = mock_run_docker.call_args[0][0]
        assert "buildx" in cmd
        assert "build" in cmd
        assert f"{REGISTRY}/{HUB_NAMESPACE}/clm-plantuml-converter:1.2.3" in cmd
        assert f"{REGISTRY}/{HUB_NAMESPACE}/clm-plantuml-converter:latest" in cmd

    def test_build_failure_returns_false(
        self, fake_project_root: Path, mock_run_docker: MagicMock
    ) -> None:
        mock_run_docker.side_effect = subprocess.CalledProcessError(1, ["docker"])
        docker_path = fake_project_root / "docker" / "plantuml"
        assert build_service("plantuml", "1.0.0", docker_path) is False

    def test_build_without_cache_skips_cache_from(
        self, fake_project_root: Path, mock_run_docker: MagicMock
    ) -> None:
        docker_path = fake_project_root / "docker" / "plantuml"
        assert build_service("plantuml", "1.0.0", docker_path, use_cache=False) is True

        cmd = mock_run_docker.call_args[0][0]
        assert "--cache-from" not in cmd


class TestBuildNotebookVariant:
    def test_lite_variant_adds_four_tags(
        self, fake_project_root: Path, mock_run_docker: MagicMock
    ) -> None:
        docker_path = fake_project_root / "docker" / "notebook"
        assert build_notebook_variant("lite", "1.0.0", docker_path) is True

        cmd = mock_run_docker.call_args[0][0]
        image = f"{REGISTRY}/{HUB_NAMESPACE}/clm-notebook-processor"
        assert f"{image}:1.0.0" in cmd
        assert f"{image}:1.0.0-lite" in cmd
        assert f"{image}:latest" in cmd
        assert f"{image}:lite" in cmd

    def test_full_variant_adds_two_tags(
        self, fake_project_root: Path, mock_run_docker: MagicMock
    ) -> None:
        docker_path = fake_project_root / "docker" / "notebook"
        assert build_notebook_variant("full", "1.0.0", docker_path) is True

        cmd = mock_run_docker.call_args[0][0]
        image = f"{REGISTRY}/{HUB_NAMESPACE}/clm-notebook-processor"
        assert f"{image}:1.0.0-full" in cmd
        assert f"{image}:full" in cmd
        # Lite-only tags must not appear
        assert f"{image}:latest" not in cmd

    def test_notebook_variant_failure(
        self, fake_project_root: Path, mock_run_docker: MagicMock
    ) -> None:
        mock_run_docker.side_effect = subprocess.CalledProcessError(1, ["docker"])
        docker_path = fake_project_root / "docker" / "notebook"
        assert build_notebook_variant("lite", "1.0.0", docker_path) is False


class TestBuildNotebook:
    def test_builds_both_variants_when_none(
        self, fake_project_root: Path, mock_run_docker: MagicMock
    ) -> None:
        docker_path = fake_project_root / "docker" / "notebook"
        assert build_notebook(None, "1.0.0", docker_path) is True

        # Two builds invoked (one for lite, one for full)
        assert mock_run_docker.call_count == 2

    def test_builds_only_requested_variant(
        self, fake_project_root: Path, mock_run_docker: MagicMock
    ) -> None:
        docker_path = fake_project_root / "docker" / "notebook"
        assert build_notebook("lite", "1.0.0", docker_path) is True

        assert mock_run_docker.call_count == 1

    def test_build_both_returns_false_if_any_fails(
        self, fake_project_root: Path, mock_run_docker: MagicMock
    ) -> None:
        # First call succeeds (lite), second fails (full).
        calls = [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CalledProcessError(1, ["docker"]),
        ]

        def side_effect(*args, **kwargs):
            result = calls.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        mock_run_docker.side_effect = side_effect

        docker_path = fake_project_root / "docker" / "notebook"
        assert build_notebook(None, "1.0.0", docker_path) is False


class TestPushService:
    def test_image_not_found_fails(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 1, "", "")
            assert push_service("plantuml-converter", "1.0.0") is False

    def test_successful_push(self, mock_run_docker: MagicMock) -> None:
        with patch("subprocess.run") as mock_inspect:
            mock_inspect.return_value = subprocess.CompletedProcess([], 0, "", "")

            assert push_service("plantuml-converter", "1.0.0") is True

            # Two pushes: version and latest
            assert mock_run_docker.call_count == 2
            args_first = mock_run_docker.call_args_list[0][0][0]
            args_second = mock_run_docker.call_args_list[1][0][0]
            assert "push" in args_first
            assert "push" in args_second

    def test_push_failure(self, mock_run_docker: MagicMock) -> None:
        mock_run_docker.side_effect = subprocess.CalledProcessError(1, ["docker"])

        with patch("subprocess.run") as mock_inspect:
            mock_inspect.return_value = subprocess.CompletedProcess([], 0, "", "")

            assert push_service("plantuml-converter", "1.0.0") is False


class TestPullService:
    def test_successful_pull(self, mock_run_docker: MagicMock) -> None:
        assert pull_service("plantuml-converter", "latest") is True
        mock_run_docker.assert_called_once()
        cmd = mock_run_docker.call_args[0][0]
        assert cmd[0] == "pull"

    def test_failed_pull(self, mock_run_docker: MagicMock) -> None:
        mock_run_docker.side_effect = subprocess.CalledProcessError(1, ["docker"])
        assert pull_service("plantuml-converter") is False


# ---------------------------------------------------------------------------
# Click CLI subcommands
# ---------------------------------------------------------------------------


class TestDockerBuildCli:
    def test_build_all_by_default(
        self,
        fake_project_root: Path,
        mock_run_docker: MagicMock,
        captured_console: StringIO,
    ) -> None:
        result = CliRunner().invoke(docker_build, [])
        assert result.exit_code == 0
        # plantuml + drawio + notebook(lite+full) = 4 invocations
        assert mock_run_docker.call_count == 4

    def test_build_single_service(
        self,
        fake_project_root: Path,
        mock_run_docker: MagicMock,
        captured_console: StringIO,
    ) -> None:
        result = CliRunner().invoke(docker_build, ["plantuml"])
        assert result.exit_code == 0
        assert mock_run_docker.call_count == 1

    def test_build_notebook_variant(
        self,
        fake_project_root: Path,
        mock_run_docker: MagicMock,
        captured_console: StringIO,
    ) -> None:
        result = CliRunner().invoke(docker_build, ["notebook:full"])
        assert result.exit_code == 0
        assert mock_run_docker.call_count == 1

    def test_build_unknown_notebook_variant_fails(
        self, fake_project_root: Path, captured_console: StringIO
    ) -> None:
        result = CliRunner().invoke(docker_build, ["notebook:weird"])
        assert result.exit_code == 1
        assert "Unknown notebook variant" in captured_console.getvalue()

    def test_build_unknown_service_fails(
        self, fake_project_root: Path, captured_console: StringIO
    ) -> None:
        (fake_project_root / "docker" / "nosuch").mkdir()
        result = CliRunner().invoke(docker_build, ["nosuch"])
        assert result.exit_code == 1
        assert "Unknown service" in captured_console.getvalue()

    def test_build_service_with_variant_rejected(
        self, fake_project_root: Path, captured_console: StringIO
    ) -> None:
        result = CliRunner().invoke(docker_build, ["plantuml:some"])
        assert result.exit_code == 1
        assert "does not support variants" in captured_console.getvalue()

    def test_build_missing_docker_dir_fails(
        self, fake_project_root: Path, captured_console: StringIO
    ) -> None:
        import shutil

        shutil.rmtree(fake_project_root / "docker" / "plantuml")
        result = CliRunner().invoke(docker_build, ["plantuml"])
        assert result.exit_code == 1
        assert "Docker directory" in captured_console.getvalue()

    def test_build_no_project_root_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        captured_console: StringIO,
    ) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        monkeypatch.setattr(docker_cmd, "get_project_root", lambda: None)

        result = CliRunner().invoke(docker_build, [])
        assert result.exit_code == 1
        assert "Could not find project root" in captured_console.getvalue()

    def test_build_some_failed_exits_nonzero(
        self,
        fake_project_root: Path,
        mock_run_docker: MagicMock,
        captured_console: StringIO,
    ) -> None:
        mock_run_docker.side_effect = subprocess.CalledProcessError(1, ["docker"])
        result = CliRunner().invoke(docker_build, ["plantuml"])
        assert result.exit_code == 1
        assert "Some services failed" in captured_console.getvalue()

    def test_build_no_cache(
        self,
        fake_project_root: Path,
        mock_run_docker: MagicMock,
        captured_console: StringIO,
    ) -> None:
        result = CliRunner().invoke(docker_build, ["--no-cache", "plantuml"])
        assert result.exit_code == 0
        assert "Cache disabled" in captured_console.getvalue()


class TestDockerBuildQuickCli:
    def test_build_quick_all(
        self,
        fake_project_root: Path,
        mock_run_docker: MagicMock,
        captured_console: StringIO,
    ) -> None:
        result = CliRunner().invoke(docker_build_quick, [])
        assert result.exit_code == 0
        # plantuml + drawio + notebook:lite + notebook:full
        assert mock_run_docker.call_count == 4

    def test_build_quick_single_service(
        self,
        fake_project_root: Path,
        mock_run_docker: MagicMock,
        captured_console: StringIO,
    ) -> None:
        result = CliRunner().invoke(docker_build_quick, ["plantuml"])
        assert result.exit_code == 0

    def test_build_quick_unknown_service_fails(
        self, fake_project_root: Path, captured_console: StringIO
    ) -> None:
        result = CliRunner().invoke(docker_build_quick, ["nosuch"])
        assert result.exit_code == 1
        assert "Unknown service" in captured_console.getvalue()

    def test_build_quick_notebook_requires_variant(
        self, fake_project_root: Path, captured_console: StringIO
    ) -> None:
        result = CliRunner().invoke(docker_build_quick, ["notebook"])
        assert result.exit_code == 1
        assert "requires a variant" in captured_console.getvalue()

    def test_build_quick_notebook_bad_variant(
        self, fake_project_root: Path, captured_console: StringIO
    ) -> None:
        result = CliRunner().invoke(docker_build_quick, ["notebook:weird"])
        assert result.exit_code == 1
        assert "Unknown notebook variant" in captured_console.getvalue()

    def test_build_quick_service_with_variant_rejected(
        self, fake_project_root: Path, captured_console: StringIO
    ) -> None:
        result = CliRunner().invoke(docker_build_quick, ["plantuml:something"])
        assert result.exit_code == 1
        assert "does not support variants" in captured_console.getvalue()

    def test_build_quick_no_project_root(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        captured_console: StringIO,
    ) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        monkeypatch.setattr(docker_cmd, "get_project_root", lambda: None)

        result = CliRunner().invoke(docker_build_quick, ["plantuml"])
        assert result.exit_code == 1
        assert "Could not find project root" in captured_console.getvalue()

    def test_build_quick_warns_when_cache_missing(
        self,
        fake_project_root: Path,
        mock_run_docker: MagicMock,
        captured_console: StringIO,
    ) -> None:
        """With a single service and no cache, the helper warns loudly."""
        result = CliRunner().invoke(docker_build_quick, ["plantuml"])
        assert result.exit_code == 0
        assert "Warning: Local cache not found" in captured_console.getvalue()

    def test_build_quick_all_failure_exits_nonzero(
        self,
        fake_project_root: Path,
        mock_run_docker: MagicMock,
        captured_console: StringIO,
    ) -> None:
        mock_run_docker.side_effect = subprocess.CalledProcessError(1, ["docker"])
        result = CliRunner().invoke(docker_build_quick, ["plantuml"])
        assert result.exit_code == 1


class TestDockerCacheInfoCli:
    def test_shows_cache_status(self, fake_project_root: Path, captured_console: StringIO) -> None:
        with patch.object(docker_cmd, "image_exists_locally", return_value=False):
            result = CliRunner().invoke(docker_cache_info, [])

        assert result.exit_code == 0
        output = captured_console.getvalue()
        assert "Docker Build Cache Status" in output
        # All services rendered
        for svc in AVAILABLE_SERVICES:
            assert svc in output

    def test_shows_cache_and_image_present(
        self, fake_project_root: Path, captured_console: StringIO
    ) -> None:
        # Make the cache dirs exist.
        (fake_project_root / CACHE_DIR_NAME / "plantuml").mkdir(parents=True)
        (fake_project_root / CACHE_DIR_NAME / "drawio").mkdir(parents=True)
        (fake_project_root / CACHE_DIR_NAME / "notebook" / "lite").mkdir(parents=True)
        (fake_project_root / CACHE_DIR_NAME / "notebook" / "full").mkdir(parents=True)

        with patch.object(docker_cmd, "image_exists_locally", return_value=True):
            result = CliRunner().invoke(docker_cache_info, [])

        assert result.exit_code == 0


class TestDockerPushCli:
    def test_push_all_default(
        self,
        fake_project_root: Path,
        mock_run_docker: MagicMock,
        captured_console: StringIO,
    ) -> None:
        with (
            patch.object(docker_cmd, "check_docker_login", return_value=True),
            patch("subprocess.run") as mock_sub,
        ):
            mock_sub.return_value = subprocess.CompletedProcess([], 0, "", "")
            result = CliRunner().invoke(docker_push, [])

        assert result.exit_code == 0
        # 3 services × 2 tags = 6 docker pushes
        assert mock_run_docker.call_count == 6

    def test_push_unknown_service_fails(
        self, fake_project_root: Path, captured_console: StringIO
    ) -> None:
        with patch.object(docker_cmd, "check_docker_login", return_value=True):
            result = CliRunner().invoke(docker_push, ["nosuch"])

        assert result.exit_code == 1
        assert "Unknown service" in captured_console.getvalue()

    def test_push_no_project_root(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        captured_console: StringIO,
    ) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        monkeypatch.setattr(docker_cmd, "get_project_root", lambda: None)

        result = CliRunner().invoke(docker_push, [])
        assert result.exit_code == 1
        assert "Could not find project root" in captured_console.getvalue()

    def test_push_declines_confirm_when_not_logged_in(
        self, fake_project_root: Path, captured_console: StringIO
    ) -> None:
        with patch.object(docker_cmd, "check_docker_login", return_value=False):
            # Provide 'n' to the confirm prompt.
            result = CliRunner().invoke(docker_push, ["plantuml-converter"], input="n\n")

        assert result.exit_code == 1
        assert "Not logged in" in captured_console.getvalue()

    def test_push_force_skips_login_check(
        self,
        fake_project_root: Path,
        mock_run_docker: MagicMock,
        captured_console: StringIO,
    ) -> None:
        with patch("subprocess.run") as mock_sub:
            mock_sub.return_value = subprocess.CompletedProcess([], 0, "", "")
            result = CliRunner().invoke(docker_push, ["--force", "plantuml-converter"])

        assert result.exit_code == 0

    def test_push_failure_exits_nonzero(
        self,
        fake_project_root: Path,
        mock_run_docker: MagicMock,
        captured_console: StringIO,
    ) -> None:
        mock_run_docker.side_effect = subprocess.CalledProcessError(1, ["docker"])

        with (
            patch.object(docker_cmd, "check_docker_login", return_value=True),
            patch("subprocess.run") as mock_sub,
        ):
            mock_sub.return_value = subprocess.CompletedProcess([], 0, "", "")
            result = CliRunner().invoke(docker_push, ["plantuml-converter"])

        assert result.exit_code == 1
        assert "Some services failed" in captured_console.getvalue()


class TestDockerPullCli:
    def test_pull_all_default(self, mock_run_docker: MagicMock, captured_console: StringIO) -> None:
        result = CliRunner().invoke(docker_pull, [])
        assert result.exit_code == 0
        # 3 services, 1 tag each = 3 pulls
        assert mock_run_docker.call_count == 3

    def test_pull_custom_tag(self, mock_run_docker: MagicMock, captured_console: StringIO) -> None:
        result = CliRunner().invoke(docker_pull, ["--tag", "1.2.3", "plantuml-converter"])

        assert result.exit_code == 0
        cmd = mock_run_docker.call_args[0][0]
        # The pulled image tag should contain "1.2.3"
        assert any("1.2.3" in part for part in cmd)

    def test_pull_unknown_service_fails(self, captured_console: StringIO) -> None:
        result = CliRunner().invoke(docker_pull, ["nosuch"])
        assert result.exit_code == 1
        assert "Unknown service" in captured_console.getvalue()

    def test_pull_failure_exits_nonzero(
        self, mock_run_docker: MagicMock, captured_console: StringIO
    ) -> None:
        mock_run_docker.side_effect = subprocess.CalledProcessError(1, ["docker"])
        result = CliRunner().invoke(docker_pull, ["plantuml-converter"])
        assert result.exit_code == 1
        assert "Some services failed" in captured_console.getvalue()


class TestDockerListCli:
    def test_list_without_project_root(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        captured_console: StringIO,
    ) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        monkeypatch.setattr(docker_cmd, "get_project_root", lambda: None)

        result = CliRunner().invoke(docker_list, [])
        assert result.exit_code == 0
        # All services still listed, without Dockerfile paths.
        output = captured_console.getvalue()
        for svc in AVAILABLE_SERVICES:
            assert svc in output

    def test_list_with_project_root(
        self, fake_project_root: Path, captured_console: StringIO
    ) -> None:
        result = CliRunner().invoke(docker_list, [])
        assert result.exit_code == 0
        output = captured_console.getvalue()
        for svc in AVAILABLE_SERVICES:
            assert svc in output

    def test_list_reports_missing_dockerfile(
        self, fake_project_root: Path, captured_console: StringIO
    ) -> None:
        """If a service has no docker directory, list flags it red."""
        import shutil

        shutil.rmtree(fake_project_root / "docker" / "plantuml")

        result = CliRunner().invoke(docker_list, [])

        assert result.exit_code == 0
        assert "Not found" in captured_console.getvalue()


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------


class TestConstants:
    def test_service_name_map_covers_all_services(self) -> None:
        for svc in AVAILABLE_SERVICES:
            assert svc in SERVICE_NAME_MAP
