"""Tests for Wave 2b course-runtime kernel isolation (Direct mode).

Covers the resolver precedence, kernelspec provisioning, JUPYTER_PATH
assembly, and the DirectWorkerExecutor injection. No real kernel is launched;
the ipykernel validation subprocess is patched or bypassed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.infrastructure.workers import kernel_env
from clm.infrastructure.workers.kernel_env import (
    KERNEL_PYTHON_ENV_VAR,
    jupyter_path_with_kernel,
    provision_course_kernel,
    resolve_kernel_interpreter,
    resolve_notebook_kernel_python,
)
from clm.infrastructure.workers.worker_executor import DirectWorkerExecutor, WorkerConfig

# --------------------------------------------------------------------------- #
# resolve_notebook_kernel_python — env > spec > clm.toml > empty
# --------------------------------------------------------------------------- #


def test_resolve_env_wins_over_spec_and_toml(monkeypatch):
    monkeypatch.setenv(KERNEL_PYTHON_ENV_VAR, "/env/python")
    fake_cfg = MagicMock()
    fake_cfg.jupyter.kernel_python = "/toml/python"
    with patch("clm.infrastructure.config.get_config", return_value=fake_cfg):
        assert resolve_notebook_kernel_python("/spec/python") == "/env/python"


def test_resolve_spec_wins_over_toml(monkeypatch):
    monkeypatch.delenv(KERNEL_PYTHON_ENV_VAR, raising=False)
    fake_cfg = MagicMock()
    fake_cfg.jupyter.kernel_python = "/toml/python"
    with patch("clm.infrastructure.config.get_config", return_value=fake_cfg):
        assert resolve_notebook_kernel_python("/spec/python") == "/spec/python"


def test_resolve_falls_back_to_toml(monkeypatch):
    monkeypatch.delenv(KERNEL_PYTHON_ENV_VAR, raising=False)
    fake_cfg = MagicMock()
    fake_cfg.jupyter.kernel_python = "/toml/python"
    with patch("clm.infrastructure.config.get_config", return_value=fake_cfg):
        assert resolve_notebook_kernel_python("") == "/toml/python"


def test_resolve_empty_default(monkeypatch):
    monkeypatch.delenv(KERNEL_PYTHON_ENV_VAR, raising=False)
    fake_cfg = MagicMock()
    fake_cfg.jupyter.kernel_python = ""
    with patch("clm.infrastructure.config.get_config", return_value=fake_cfg):
        assert resolve_notebook_kernel_python("") == ""


def test_resolve_env_whitespace_is_ignored(monkeypatch):
    monkeypatch.setenv(KERNEL_PYTHON_ENV_VAR, "   ")
    fake_cfg = MagicMock()
    fake_cfg.jupyter.kernel_python = ""
    with patch("clm.infrastructure.config.get_config", return_value=fake_cfg):
        # Blank env falls through to the spec value.
        assert resolve_notebook_kernel_python("/spec/python") == "/spec/python"


# --------------------------------------------------------------------------- #
# resolve_kernel_interpreter — venv-dir + project-relative normalisation
# --------------------------------------------------------------------------- #


def _make_venv(root: Path) -> Path:
    """Create a venv-shaped dir with BOTH interpreter layouts present."""
    (root / "Scripts").mkdir(parents=True, exist_ok=True)
    (root / "bin").mkdir(parents=True, exist_ok=True)
    (root / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    (root / "bin" / "python").write_text("", encoding="utf-8")
    return root


def test_resolve_interpreter_empty_is_empty():
    assert resolve_kernel_interpreter("") == ""
    assert resolve_kernel_interpreter("   ") == ""


def test_resolve_interpreter_venv_dir_picks_platform_interpreter(tmp_path):
    venv = _make_venv(tmp_path / ".venv")
    result = Path(resolve_kernel_interpreter(str(venv)))
    if os.name == "nt":
        assert result == venv / "Scripts" / "python.exe"
    else:
        assert result == venv / "bin" / "python"


def test_resolve_interpreter_venv_dir_uses_existing_when_only_other_layout(tmp_path):
    # Only the POSIX layout exists → resolve to it regardless of host OS.
    venv = tmp_path / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("", encoding="utf-8")
    assert Path(resolve_kernel_interpreter(str(venv))) == venv / "bin" / "python"


def test_resolve_interpreter_relative_anchored_to_project_root(tmp_path):
    venv = _make_venv(tmp_path / ".venv")
    result = Path(resolve_kernel_interpreter(".venv", project_root=tmp_path))
    assert result.parent.parent == venv  # <root>/.venv/<bin|Scripts>/python*
    assert result.is_file()


def test_resolve_interpreter_relative_not_cwd(tmp_path, monkeypatch):
    # A relative value must anchor to project_root, NOT the process cwd.
    _make_venv(tmp_path / ".venv")
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    result = Path(resolve_kernel_interpreter(".venv", project_root=tmp_path))
    assert str(tmp_path) in str(result)
    assert str(other) not in str(result)


def test_resolve_interpreter_direct_file_returned_absolute(tmp_path):
    interp = tmp_path / "custom" / "python"
    interp.parent.mkdir(parents=True)
    interp.write_text("", encoding="utf-8")
    assert Path(resolve_kernel_interpreter(str(interp))) == interp


def test_resolve_interpreter_missing_venv_yields_platform_default(tmp_path):
    # Non-existent path (venv not built yet) → returned so provisioning can emit
    # an actionable "not found" naming a concrete interpreter.
    result = resolve_kernel_interpreter(str(tmp_path / "absent"))
    assert result == str(tmp_path / "absent")


# --------------------------------------------------------------------------- #
# provision_course_kernel
# --------------------------------------------------------------------------- #


@pytest.fixture
def kernel_root(tmp_path, monkeypatch):
    """Redirect provisioning output into tmp_path instead of the user data dir."""
    root = tmp_path / "kernel-envs"
    monkeypatch.setattr(kernel_env, "_kernel_envs_root", lambda: root)
    return root


def test_provision_writes_python3_kernelspec(kernel_root, tmp_path):
    fake_python = tmp_path / "venv" / "python"
    # validate=False so we don't need a real interpreter with ipykernel.
    root = provision_course_kernel(fake_python, validate=False)

    kernel_json_path = root / "kernels" / "python3" / "kernel.json"
    assert kernel_json_path.is_file()
    data = json.loads(kernel_json_path.read_text(encoding="utf-8"))
    assert data["argv"][0] == str(fake_python)
    assert data["argv"][1:] == ["-m", "ipykernel_launcher", "-f", "{connection_file}"]
    assert data["language"] == "python"
    # The returned root is the JUPYTER_PATH entry (contains kernels/).
    assert (root / "kernels").is_dir()


def test_provision_is_idempotent_per_interpreter(kernel_root, tmp_path):
    fake_python = tmp_path / "python"
    root_a = provision_course_kernel(fake_python, validate=False)
    root_b = provision_course_kernel(fake_python, validate=False)
    assert root_a == root_b


def test_provision_distinct_interpreters_distinct_roots(kernel_root, tmp_path):
    root_a = provision_course_kernel(tmp_path / "a" / "python", validate=False)
    root_b = provision_course_kernel(tmp_path / "b" / "python", validate=False)
    assert root_a != root_b


def test_provision_missing_interpreter_raises(kernel_root, tmp_path):
    missing = tmp_path / "does-not-exist" / "python"
    with pytest.raises(RuntimeError, match="not found"):
        provision_course_kernel(missing, validate=True)


def test_validate_ipykernel_failure_raises(kernel_root, tmp_path):
    real_file = tmp_path / "python"
    real_file.write_text("", encoding="utf-8")
    failed = MagicMock(returncode=1, stderr="ModuleNotFoundError: ipykernel")
    with patch.object(kernel_env.subprocess, "run", return_value=failed):
        with pytest.raises(RuntimeError, match="ipykernel"):
            provision_course_kernel(real_file, validate=True)


def test_validate_ipykernel_success_writes_spec(kernel_root, tmp_path):
    real_file = tmp_path / "python"
    real_file.write_text("", encoding="utf-8")
    ok = MagicMock(returncode=0, stderr="")
    with patch.object(kernel_env.subprocess, "run", return_value=ok):
        root = provision_course_kernel(real_file, validate=True)
    assert (root / "kernels" / "python3" / "kernel.json").is_file()


# --------------------------------------------------------------------------- #
# jupyter_path_with_kernel
# --------------------------------------------------------------------------- #


def test_jupyter_path_prepends_when_existing():
    import os

    root = Path("/course/root")
    result = jupyter_path_with_kernel(root, f"/prior{os.pathsep}/more")
    assert result == f"{root}{os.pathsep}/prior{os.pathsep}/more"
    # Course root sits first so its python3 kernelspec shadows clm's own.
    assert result.split(os.pathsep)[0] == str(root)


def test_jupyter_path_no_existing():
    root = Path("/course/root")
    assert jupyter_path_with_kernel(root, None) == str(root)
    assert jupyter_path_with_kernel(root, "") == str(root)


# --------------------------------------------------------------------------- #
# DirectWorkerExecutor injection
# --------------------------------------------------------------------------- #


@patch("subprocess.Popen")
def test_executor_injects_jupyter_path_for_notebook(mock_popen, tmp_path):
    mock_popen.return_value = MagicMock(pid=4321)
    fake_root = tmp_path / "kernel-root"

    with patch(
        "clm.infrastructure.workers.kernel_env.provision_course_kernel",
        return_value=fake_root,
    ) as prov:
        executor = DirectWorkerExecutor(
            db_path=tmp_path / "jobs.db",
            workspace_path=tmp_path / "ws",
            notebook_kernel_python="/course/venv/python",
        )
    prov.assert_called_once()

    config = WorkerConfig(worker_type="notebook", count=1, execution_mode="direct")
    executor.start_worker("notebook", 0, config)

    env = mock_popen.call_args[1]["env"]
    import os

    assert env["JUPYTER_PATH"].split(os.pathsep)[0] == str(fake_root)


@patch("subprocess.Popen")
def test_executor_skips_jupyter_path_for_non_notebook(mock_popen, tmp_path):
    mock_popen.return_value = MagicMock(pid=4321)
    fake_root = tmp_path / "kernel-root"
    with patch(
        "clm.infrastructure.workers.kernel_env.provision_course_kernel",
        return_value=fake_root,
    ):
        executor = DirectWorkerExecutor(
            db_path=tmp_path / "jobs.db",
            workspace_path=tmp_path / "ws",
            notebook_kernel_python="/course/venv/python",
        )
    config = WorkerConfig(worker_type="plantuml", count=1, execution_mode="direct")
    executor.start_worker("plantuml", 0, config)

    env = mock_popen.call_args[1]["env"]
    # A plantuml worker launches no Jupyter kernel — no course JUPYTER_PATH.
    assert str(fake_root) not in env.get("JUPYTER_PATH", "")


def test_jupyter_client_resolves_python3_to_provisioned_kernel(kernel_root, tmp_path, monkeypatch):
    """End-to-end mechanism check: JUPYTER_PATH → our python3 kernelspec wins.

    Proves the load-bearing assumption without launching a kernel: with our
    provisioned root on JUPYTER_PATH, jupyter_client's KernelSpecManager resolves
    the ``python3`` name to the interpreter we pointed at (shadowing clm's own).
    """
    import os

    pytest.importorskip("jupyter_client")
    from jupyter_client.kernelspec import KernelSpecManager

    fake_python = tmp_path / "course-venv" / "python"
    root = provision_course_kernel(fake_python, validate=False)

    monkeypatch.setenv("JUPYTER_PATH", str(root))
    spec = KernelSpecManager().get_kernel_spec("python3")

    assert spec.argv[0] == str(fake_python)
    assert os.path.commonpath([spec.resource_dir, str(root)]) == str(root)


@patch("subprocess.Popen")
def test_executor_no_provisioning_when_unset(mock_popen, tmp_path):
    mock_popen.return_value = MagicMock(pid=4321)
    with patch("clm.infrastructure.workers.kernel_env.provision_course_kernel") as prov:
        executor = DirectWorkerExecutor(
            db_path=tmp_path / "jobs.db",
            workspace_path=tmp_path / "ws",
        )
    # Empty (default) → no provisioning at all, today's behaviour untouched.
    prov.assert_not_called()
    assert executor._kernel_jupyter_path_root is None

    config = WorkerConfig(worker_type="notebook", count=1, execution_mode="direct")
    executor.start_worker("notebook", 0, config)
    env = mock_popen.call_args[1]["env"]
    # We didn't add a course kernel; JUPYTER_PATH is whatever the host had (if any).
    assert executor.notebook_kernel_python == ""
