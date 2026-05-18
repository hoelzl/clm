"""CLI flag-validation tests for ``clm build --snapshot`` and
``clm build --verify-against``.

These tests exercise the usage-error path: each command shape triggers
the validation check at the top of ``build()`` *before* any build
pipeline runs, so they are cheap and do not need any worker setup.

End-to-end build determinism (the actual snapshot/verify cycle on a
real spec) lives in ``tests/integration/`` so it can be marked slow.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands import build as build_module


def _write_minimal_spec(path: Path) -> Path:
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <sections/>
</course>
""",
        encoding="utf-8",
    )
    return path


def _invoke_build(args, tmp_path: Path):
    """Invoke ``build`` with the parent-context dict the top-level
    group normally seeds. Without it, the command bails on the missing
    DB-path keys."""
    obj = {
        "CACHE_DB_PATH": tmp_path / "cache.db",
        "JOBS_DB_PATH": tmp_path / "jobs.db",
    }
    return CliRunner().invoke(build_module.build, args, obj=obj)


class TestSnapshotFlagValidation:
    def test_snapshot_and_output_dir_mutex(self, tmp_path: Path) -> None:
        spec = _write_minimal_spec(tmp_path / "course.xml")
        result = _invoke_build(
            [
                str(spec),
                "--snapshot",
                str(tmp_path / "snap"),
                "--output-dir",
                str(tmp_path / "out"),
            ],
            tmp_path,
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output
        assert "--snapshot" in result.output and "--output-dir" in result.output

    def test_snapshot_and_verify_mutex(self, tmp_path: Path) -> None:
        spec = _write_minimal_spec(tmp_path / "course.xml")
        baseline = tmp_path / "baseline"
        baseline.mkdir()
        result = _invoke_build(
            [
                str(spec),
                "--snapshot",
                str(tmp_path / "snap"),
                "--verify-against",
                str(baseline),
            ],
            tmp_path,
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output

    def test_snapshot_target_must_be_empty(self, tmp_path: Path) -> None:
        spec = _write_minimal_spec(tmp_path / "course.xml")
        snap = tmp_path / "snap"
        snap.mkdir()
        (snap / "stale.txt").write_text("leftover", encoding="utf-8")
        result = _invoke_build(
            [str(spec), "--snapshot", str(snap)],
            tmp_path,
        )
        assert result.exit_code != 0
        assert "not empty" in result.output

    def test_snapshot_into_nonexistent_dir_passes_validation(self, tmp_path: Path) -> None:
        # The validation step should accept a non-existent target;
        # the build itself will create it. We assert the run got past
        # the validation by not finding the usage-error wording.
        # (The build will likely fail later for unrelated reasons in
        # this minimal-spec setup; we only care about the validation
        # gate here.)
        spec = _write_minimal_spec(tmp_path / "course.xml")
        result = _invoke_build(
            [str(spec), "--snapshot", str(tmp_path / "fresh-snap")],
            tmp_path,
        )
        # Either succeeded entirely OR failed for a build-pipeline
        # reason. The usage-error path must not have fired.
        assert "mutually exclusive" not in result.output
        assert "not empty" not in result.output


class TestVerifyFlagValidation:
    def test_include_html_without_verify_errors(self, tmp_path: Path) -> None:
        spec = _write_minimal_spec(tmp_path / "course.xml")
        result = _invoke_build(
            [str(spec), "--include-html"],
            tmp_path,
        )
        assert result.exit_code != 0
        assert "--include-html" in result.output
        assert "no effect" in result.output

    def test_strict_verify_without_verify_errors(self, tmp_path: Path) -> None:
        spec = _write_minimal_spec(tmp_path / "course.xml")
        result = _invoke_build(
            [str(spec), "--strict-verify"],
            tmp_path,
        )
        assert result.exit_code != 0
        assert "--strict-verify" in result.output
        assert "no effect" in result.output

    def test_verify_against_requires_existing_dir(self, tmp_path: Path) -> None:
        # Click's exists=True on the --verify-against type catches this
        # before our own logic runs; the error message comes from Click.
        spec = _write_minimal_spec(tmp_path / "course.xml")
        result = _invoke_build(
            [
                str(spec),
                "--verify-against",
                str(tmp_path / "missing-baseline"),
            ],
            tmp_path,
        )
        assert result.exit_code != 0
        # Click's standard error text for an exists=True path.
        assert "does not exist" in result.output or "Invalid value" in result.output
