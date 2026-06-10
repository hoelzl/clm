"""Tests for ``clm run`` — spec-defined task sequences."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from clm.cli.commands.run import run_cmd, unknown_cli_command_error

try:
    _RUNNER = CliRunner(mix_stderr=False)  # Click < 8.2
except TypeError:
    _RUNNER = CliRunner()  # Click >= 8.2 (stderr always separate)


def _out(result) -> str:
    """stdout + stderr, robust across Click 8.1/8.2 stderr handling."""
    return (result.output or "") + (result.stderr or "")


SPEC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<course>
  <name><de>T</de><en>T</en></name>
  <prog-lang>python</prog-lang>
  <sections>
    <section>
      <name><de>S</de><en>S</en></name>
      <topics><topic>intro</topic></topics>
    </section>
  </sections>
  <tasks>
    <task name="pre-release" description="Exports, then build">
      <step>export outline {spec} -o outline/</step>
      <step>build {spec}</step>
    </task>
    <task name="single">
      <step>info commands</step>
    </task>
  </tasks>
</course>
"""


@pytest.fixture
def spec_file(tmp_path: Path) -> Path:
    path = tmp_path / "course.xml"
    path.write_text(SPEC_XML, encoding="utf-8")
    return path


@pytest.fixture
def recorded_runs(monkeypatch):
    """Replace the step subprocess with a recorder returning exit code 0."""
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("clm.cli.commands.run.subprocess.run", fake_run)
    return calls


def _write_spec(tmp_path: Path, tasks_block: str) -> Path:
    xml = SPEC_XML.replace(
        SPEC_XML[SPEC_XML.index("<tasks>") : SPEC_XML.index("</tasks>") + len("</tasks>")],
        tasks_block,
    )
    path = tmp_path / "course.xml"
    path.write_text(xml, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Listing and argument dispatch
# ---------------------------------------------------------------------------


def test_single_file_argument_lists_tasks(spec_file: Path):
    result = _RUNNER.invoke(run_cmd, [str(spec_file)])
    assert result.exit_code == 0
    assert "pre-release" in _out(result)
    assert "Exports, then build" in _out(result)
    assert "(2 steps)" in _out(result)
    assert "clm build {spec}" in _out(result)


def test_list_flag_lists_tasks(spec_file: Path):
    result = _RUNNER.invoke(run_cmd, ["--list", str(spec_file)])
    assert result.exit_code == 0
    assert "single" in _out(result)


def test_no_arguments_is_a_usage_error():
    result = _RUNNER.invoke(run_cmd, [])
    assert result.exit_code == 2
    assert "Missing spec file" in _out(result)


def test_missing_spec_file_is_a_usage_error(tmp_path: Path):
    result = _RUNNER.invoke(run_cmd, ["pre-release", str(tmp_path / "nope.xml")])
    assert result.exit_code == 2
    assert "not found" in _out(result)


def test_unknown_task_reports_available_tasks(spec_file: Path):
    result = _RUNNER.invoke(run_cmd, ["nope", str(spec_file)])
    assert result.exit_code == 1
    assert "No task named 'nope'" in _out(result)
    assert "pre-release" in _out(result)


def test_spec_without_tasks_lists_nothing(tmp_path: Path):
    spec = _write_spec(tmp_path, "")
    result = _RUNNER.invoke(run_cmd, [str(spec)])
    assert result.exit_code == 0
    assert "No <tasks> defined" in _out(result)


# ---------------------------------------------------------------------------
# Resolution and validation (nothing executed)
# ---------------------------------------------------------------------------


def test_dry_run_prints_resolved_commands_without_executing(spec_file: Path, recorded_runs: list):
    result = _RUNNER.invoke(run_cmd, ["pre-release", str(spec_file), "--dry-run"])
    assert result.exit_code == 0
    assert "[1/2] clm export outline" in _out(result)
    assert "[2/2] clm build" in _out(result)
    assert str(spec_file.resolve()) in _out(result)
    assert recorded_runs == []


def test_unknown_command_in_any_step_fails_before_executing(tmp_path: Path, recorded_runs: list):
    spec = _write_spec(
        tmp_path,
        """<tasks>
      <task name="bad">
        <step>info commands</step>
        <step>frobnicate {spec}</step>
      </task>
    </tasks>""",
    )
    result = _RUNNER.invoke(run_cmd, ["bad", str(spec)])
    assert result.exit_code == 1
    assert "step 2" in _out(result)
    assert "frobnicate" in _out(result)
    assert recorded_runs == []


def test_group_without_subcommand_is_an_error(tmp_path: Path, recorded_runs: list):
    spec = _write_spec(
        tmp_path,
        '<tasks><task name="bad"><step>export --help</step></task></tasks>',
    )
    result = _RUNNER.invoke(run_cmd, ["bad", str(spec)])
    assert result.exit_code == 1
    assert "needs a subcommand" in _out(result)
    assert recorded_runs == []


def test_unknown_placeholder_fails_before_executing(tmp_path: Path, recorded_runs: list):
    spec = _write_spec(
        tmp_path,
        '<tasks><task name="bad"><step>build {sepc}</step></task></tasks>',
    )
    result = _RUNNER.invoke(run_cmd, ["bad", str(spec)])
    assert result.exit_code == 1
    assert "unknown placeholder {sepc}" in _out(result)
    assert recorded_runs == []


def test_structurally_invalid_tasks_block_is_an_error(tmp_path: Path):
    spec = _write_spec(
        tmp_path,
        """<tasks>
      <task name="a"><step>build {spec}</step></task>
      <task name="a"><step>validate {spec}</step></task>
    </tasks>""",
    )
    result = _RUNNER.invoke(run_cmd, [str(spec)])
    assert result.exit_code == 1
    assert "Duplicate task name" in _out(result)


def test_task_nesting_is_rejected(tmp_path: Path, recorded_runs: list):
    spec = _write_spec(
        tmp_path,
        '<tasks><task name="a"><step>run b {spec}</step></task></tasks>',
    )
    result = _RUNNER.invoke(run_cmd, ["a", str(spec)])
    assert result.exit_code == 1
    assert "cannot invoke other tasks" in _out(result)
    assert recorded_runs == []


def test_unknown_cli_command_error_resolves_real_commands():
    assert unknown_cli_command_error(["build", "spec.xml"]) is None
    assert unknown_cli_command_error(["calendar", "generate", "spec.xml"]) is None
    error = unknown_cli_command_error(["export", "nope"])
    assert error is not None and "not a clm command" in error
    error = unknown_cli_command_error(["export"])
    assert error is not None and "needs a subcommand" in error


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def test_steps_run_in_order_via_module_invocation(spec_file: Path, recorded_runs: list):
    result = _RUNNER.invoke(run_cmd, ["pre-release", str(spec_file)])
    assert result.exit_code == 0
    assert len(recorded_runs) == 2
    spec_abs = str(spec_file.resolve())
    assert recorded_runs[0] == [
        sys.executable,
        "-m",
        "clm",
        "export",
        "outline",
        spec_abs,
        "-o",
        "outline/",
    ]
    assert recorded_runs[1] == [sys.executable, "-m", "clm", "build", spec_abs]


def test_first_failing_step_aborts_with_its_exit_code(spec_file: Path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0 if len(calls) == 1 else 3)

    monkeypatch.setattr("clm.cli.commands.run.subprocess.run", fake_run)
    result = _RUNNER.invoke(run_cmd, ["pre-release", str(spec_file)])
    assert result.exit_code == 3
    assert len(calls) == 2  # step 2 failed; nothing ran after it
    assert "step 2/2 failed with exit code 3" in _out(result)


# ---------------------------------------------------------------------------
# End-to-end (real subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.slow
def test_run_executes_a_real_step_end_to_end(spec_file: Path):
    result = subprocess.run(
        [sys.executable, "-m", "clm", "run", "single", str(spec_file)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "[1/1] clm info commands" in result.stdout
    # The step's own output (the commands info topic) reached the console.
    assert "clm" in result.stdout
