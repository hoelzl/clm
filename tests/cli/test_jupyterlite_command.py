"""Tests for the ``clm jupyterlite`` CLI command group."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from clm.cli.commands import jupyterlite as jupyterlite_module
from clm.cli.commands.jupyterlite import _find_site_dirs, jupyterlite_group


def _make_output_target(name: str, output_root: Path, formats: list[str]):
    """Build a MagicMock that quacks like OutputTarget for the command."""
    target = MagicMock()
    target.name = name
    target.output_root = output_root
    target.formats = formats
    return target


class TestFindSiteDirs:
    def test_returns_empty_when_no_jupyterlite_output(self, tmp_path: Path):
        result = _find_site_dirs(tmp_path, kind=None, language=None)
        assert result == []

    def test_finds_site_with_index_html(self, tmp_path: Path):
        site = tmp_path / "course-de" / "Slides" / "JupyterLite" / "Completed" / "_output"
        site.mkdir(parents=True)
        (site / "index.html").write_text("<html></html>")

        result = _find_site_dirs(tmp_path, kind=None, language=None)

        assert len(result) == 1
        assert result[0] == site

    def test_skips_site_without_index_html(self, tmp_path: Path):
        site = tmp_path / "course-de" / "Slides" / "JupyterLite" / "Completed" / "_output"
        site.mkdir(parents=True)
        # No index.html — should be skipped.

        result = _find_site_dirs(tmp_path, kind=None, language=None)
        assert result == []

    def test_sorted_by_mtime_newest_first(self, tmp_path: Path):
        site_old = tmp_path / "course-de" / "Slides" / "JupyterLite" / "Completed" / "_output"
        site_new = tmp_path / "course-de" / "Slides" / "JupyterLite" / "CodeAlong" / "_output"
        for site in (site_old, site_new):
            site.mkdir(parents=True)
            (site / "index.html").write_text("x")

        # Make site_new newer by touching.
        older = time.time() - 100
        os.utime(site_old, (older, older))

        result = _find_site_dirs(tmp_path, kind=None, language=None)
        assert result[0] == site_new

    def test_language_filter_falls_back_when_no_match(self, tmp_path: Path):
        # The language filter only narrows results when at least one path
        # contains the /{lang}/ segment. When no path matches, it falls
        # back to returning all results.
        de = tmp_path / "course-de" / "Slides" / "JupyterLite" / "Completed" / "_output"
        de.mkdir(parents=True)
        (de / "index.html").write_text("x")

        result = _find_site_dirs(tmp_path, kind=None, language="zh")
        # "zh" doesn't appear in the path, so fall back to unfiltered list.
        assert len(result) == 1
        assert result[0] == de

    def test_language_filter_matches_posix_segment(self, tmp_path: Path):
        # When paths contain /de/ as an actual path segment, the filter matches.
        de_site = tmp_path / "out" / "de" / "Slides" / "JupyterLite" / "Completed" / "_output"
        en_site = tmp_path / "out" / "en" / "Slides" / "JupyterLite" / "Completed" / "_output"
        for site in (de_site, en_site):
            site.mkdir(parents=True)
            (site / "index.html").write_text("x")

        result = _find_site_dirs(tmp_path, kind=None, language="de")
        assert len(result) == 1
        assert result[0] == de_site


def _patch_course(monkeypatch, targets):
    """Patch CourseSpec.from_file and Course.from_spec to return a course with the
    given output targets."""
    fake_spec = MagicMock()
    fake_course = MagicMock()
    fake_course.output_targets = targets

    fake_course_spec_module = MagicMock()
    fake_course_spec_module.CourseSpec.from_file = MagicMock(return_value=fake_spec)

    fake_course_module = MagicMock()
    fake_course_module.Course.from_spec = MagicMock(return_value=fake_course)

    import sys

    monkeypatch.setitem(sys.modules, "clm.core.course", fake_course_module)
    monkeypatch.setitem(sys.modules, "clm.core.course_spec", fake_course_spec_module)


class TestPreviewCommand:
    def test_group_help(self):
        runner = CliRunner()
        result = runner.invoke(jupyterlite_group, ["--help"])
        assert result.exit_code == 0

    def test_preview_help(self):
        runner = CliRunner()
        result = runner.invoke(jupyterlite_group, ["preview", "--help"])
        assert result.exit_code == 0
        assert "--target" in result.output

    def test_unknown_target_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        spec_file = tmp_path / "spec.xml"
        spec_file.write_text("<course/>")

        output_root = tmp_path / "output"
        output_root.mkdir()
        _patch_course(
            monkeypatch,
            targets=[_make_output_target("public", output_root, ["jupyterlite"])],
        )

        runner = CliRunner()
        result = runner.invoke(
            jupyterlite_group, ["preview", str(spec_file), "--target", "nonexistent"]
        )

        assert result.exit_code != 0
        assert "No target named" in result.output
        # The error lists available targets.
        assert "public" in result.output

    def test_target_without_jupyterlite_format_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        spec_file = tmp_path / "spec.xml"
        spec_file.write_text("<course/>")

        output_root = tmp_path / "output"
        output_root.mkdir()
        _patch_course(
            monkeypatch,
            targets=[_make_output_target("public", output_root, ["html"])],
        )

        runner = CliRunner()
        result = runner.invoke(jupyterlite_group, ["preview", str(spec_file), "--target", "public"])

        assert result.exit_code != 0
        assert "does not include 'jupyterlite'" in result.output

    def test_no_built_site_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        spec_file = tmp_path / "spec.xml"
        spec_file.write_text("<course/>")

        output_root = tmp_path / "output"
        output_root.mkdir()
        _patch_course(
            monkeypatch,
            targets=[_make_output_target("public", output_root, ["jupyterlite"])],
        )

        runner = CliRunner()
        result = runner.invoke(jupyterlite_group, ["preview", str(spec_file), "--target", "public"])

        assert result.exit_code != 0
        assert "No built JupyterLite site" in result.output

    def test_multiple_sites_require_narrowing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        spec_file = tmp_path / "spec.xml"
        spec_file.write_text("<course/>")

        output_root = tmp_path / "output"
        # Create two sites that both have index.html.
        completed = output_root / "course-de" / "Slides" / "JupyterLite" / "Completed" / "_output"
        codealong = output_root / "course-de" / "Slides" / "JupyterLite" / "CodeAlong" / "_output"
        for site in (completed, codealong):
            site.mkdir(parents=True)
            (site / "index.html").write_text("x")

        _patch_course(
            monkeypatch,
            targets=[_make_output_target("public", output_root, ["jupyterlite"])],
        )

        runner = CliRunner()
        result = runner.invoke(jupyterlite_group, ["preview", str(spec_file), "--target", "public"])

        assert result.exit_code != 0
        assert "Multiple sites found" in result.output
        assert "Specify --kind" in result.output

    def test_invokes_launch_py_when_present(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        spec_file = tmp_path / "spec.xml"
        spec_file.write_text("<course/>")

        output_root = tmp_path / "output"
        site = output_root / "course-de" / "Slides" / "JupyterLite" / "Completed" / "_output"
        site.mkdir(parents=True)
        (site / "index.html").write_text("x")
        (site.parent / "launch.py").write_text("print('hi')")

        _patch_course(
            monkeypatch,
            targets=[_make_output_target("public", output_root, ["jupyterlite"])],
        )

        fake_run = MagicMock()
        monkeypatch.setattr(jupyterlite_module.subprocess, "run", fake_run)

        runner = CliRunner()
        result = runner.invoke(jupyterlite_group, ["preview", str(spec_file), "--target", "public"])

        assert result.exit_code == 0, result.output
        fake_run.assert_called_once()
        args = fake_run.call_args.args[0]
        assert args[1] == str(site.parent / "launch.py")
        assert fake_run.call_args.kwargs["check"] is True

    def test_falls_back_to_miniserve_launchers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        spec_file = tmp_path / "spec.xml"
        spec_file.write_text("<course/>")

        output_root = tmp_path / "output"
        site = output_root / "course-de" / "Slides" / "JupyterLite" / "Completed" / "_output"
        site.mkdir(parents=True)
        (site / "index.html").write_text("x")
        # No launch.py, but a launch.bat and launch.sh.
        (site.parent / "launch.bat").write_text("echo windows")
        (site.parent / "launch.sh").write_text("echo linux")

        _patch_course(
            monkeypatch,
            targets=[_make_output_target("public", output_root, ["jupyterlite"])],
        )

        fake_run = MagicMock()
        monkeypatch.setattr(jupyterlite_module.subprocess, "run", fake_run)

        runner = CliRunner()
        result = runner.invoke(jupyterlite_group, ["preview", str(spec_file), "--target", "public"])

        assert result.exit_code == 0, result.output
        assert "miniserve launcher" in result.output
        assert "launch.bat" in result.output
        assert "launch.sh" in result.output
        # Miniserve launcher mode does not shell out.
        fake_run.assert_not_called()

    def test_no_launcher_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        spec_file = tmp_path / "spec.xml"
        spec_file.write_text("<course/>")

        output_root = tmp_path / "output"
        site = output_root / "course-de" / "Slides" / "JupyterLite" / "Completed" / "_output"
        site.mkdir(parents=True)
        (site / "index.html").write_text("x")
        # No launch.py, no launchers.

        _patch_course(
            monkeypatch,
            targets=[_make_output_target("public", output_root, ["jupyterlite"])],
        )

        runner = CliRunner()
        result = runner.invoke(jupyterlite_group, ["preview", str(spec_file), "--target", "public"])

        assert result.exit_code != 0
        assert "No launcher found" in result.output
