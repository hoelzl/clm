"""Tests for the ``clm provision`` command group (Wave 2b)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from clm.cli.commands.provision import provision_group
from clm.infrastructure.workers import kernel_env


def test_group_help():
    result = CliRunner().invoke(provision_group, ["--help"])
    assert result.exit_code == 0
    assert "kernel-env" in result.output


def test_kernel_env_help():
    result = CliRunner().invoke(provision_group, ["kernel-env", "--help"])
    assert result.exit_code == 0
    assert "--python" in result.output


def test_kernel_env_registers_and_prints_activation(tmp_path, monkeypatch):
    root = tmp_path / "kernel-envs"
    monkeypatch.setattr(kernel_env, "_kernel_envs_root", lambda: root)
    fake_python = tmp_path / "venv" / "python"

    result = CliRunner().invoke(
        provision_group,
        ["kernel-env", "--python", str(fake_python), "--no-validate"],
    )
    assert result.exit_code == 0, result.output
    assert "CLM_NOTEBOOK_KERNEL_PYTHON" in result.output
    assert "<kernel-python>" in result.output

    # It actually wrote a python3 kernelspec pointing at the interpreter.
    written = list(root.rglob("kernel.json"))
    assert len(written) == 1
    data = json.loads(written[0].read_text(encoding="utf-8"))
    assert data["argv"][0] == str(fake_python)


def test_kernel_env_missing_interpreter_is_clickexception(tmp_path):
    missing = tmp_path / "nope" / "python"
    result = CliRunner().invoke(provision_group, ["kernel-env", "--python", str(missing)])
    # Validation failure surfaces as a clean CLI error, not a traceback.
    assert result.exit_code != 0
    assert "not found" in result.output


def test_kernel_env_bad_ipykernel_is_clickexception(tmp_path, monkeypatch):
    monkeypatch.setattr(kernel_env, "_kernel_envs_root", lambda: tmp_path / "ke")
    real_file = tmp_path / "python"
    real_file.write_text("", encoding="utf-8")
    with patch.object(
        kernel_env.subprocess, "run", return_value=MagicMock(returncode=1, stderr="")
    ):
        result = CliRunner().invoke(provision_group, ["kernel-env", "--python", str(real_file)])
    assert result.exit_code != 0
    assert "ipykernel" in result.output
