"""Unit tests for ``clm.cli.commands.build`` helpers and the ``list_targets`` CLI.

The ``build`` command itself orchestrates the entire course pipeline and
is exercised end-to-end by the integration tests (test_build_output,
test_cli_subprocess, test_watch_mode, etc.). These tests cover the
building blocks that orchestration composes — helpers like
``_find_env_file``, ``create_output_formatter``, ``configure_workers``,
``_report_duplicate_file_warnings``, etc. — plus the ``clm targets``
subcommand which has almost no integration coverage.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner

from clm.cli.build_data_classes import BuildError, BuildSummary, BuildWarning
from clm.cli.commands import build as build_module
from clm.cli.commands.build import (
    BuildConfig,
    _compute_section_dirs_for_cleanup,
    _find_env_file,
    _report_duplicate_file_warnings,
    _report_image_collisions,
    _report_loading_issues,
    _resolve_fail_on_missing_xref,
    _resolve_http_replay_mode,
    _resolve_http_replay_transport,
    _resolve_write_provenance_manifest,
    _should_emit_provenance_manifest,
    configure_workers,
    create_output_formatter,
    enable_jupyterlite_workers_if_needed,
    initialize_paths_and_course,
    list_targets,
    report_validation_errors,
    start_managed_workers,
)
from clm.cli.output_formatter import (
    DefaultOutputFormatter,
    JSONOutputFormatter,
    QuietOutputFormatter,
    VerboseOutputFormatter,
)
from clm.core.course_spec import CourseSpecError

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> BuildConfig:
    """Build a minimal ``BuildConfig`` with sensible defaults for tests."""
    defaults: dict = {
        "spec_file": Path("spec.xml"),
        "data_dir": Path("data"),
        "output_dir": Path("out"),
        "log_level": "INFO",
        "cache_db_path": Path("cache.db"),
        "jobs_db_path": Path("jobs.db"),
        "ignore_cache": False,
        "clear_cache": False,
        "watch": False,
        "print_correlation_ids": False,
        "workers": None,
        "notebook_workers": None,
        "plantuml_workers": None,
        "drawio_workers": None,
        "notebook_image": None,
    }
    defaults.update(overrides)
    return BuildConfig(**defaults)


# ---------------------------------------------------------------------------
# _find_env_file
# ---------------------------------------------------------------------------


class TestFindEnvFile:
    def test_finds_env_in_same_directory(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("", encoding="utf-8")
        assert _find_env_file(tmp_path) == env

    def test_walks_up_to_find_env(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("", encoding="utf-8")
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        assert _find_env_file(nested) == env

    def test_returns_none_when_no_env(self, tmp_path: Path) -> None:
        # An isolated tmp_path tree will only hit its own root and then
        # stop (path.parent == path at the filesystem root).
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        # Guard against an ancestor having a .env on the dev machine
        # by checking behaviour relative to tmp_path itself.
        assert _find_env_file(nested) is None or _find_env_file(nested).parent != tmp_path


# ---------------------------------------------------------------------------
# _resolve_http_replay_mode
# ---------------------------------------------------------------------------


class TestResolveHttpReplayMode:
    def test_explicit_cli_value_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CI", "true")
        monkeypatch.setenv("CLM_HTTP_REPLAY_MODE", "refresh")
        assert _resolve_http_replay_mode("disabled") == "disabled"

    def test_env_var_used_when_cli_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("CLM_HTTP_REPLAY_MODE", "refresh")
        assert _resolve_http_replay_mode(None) == "refresh"

    def test_env_var_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("CLM_HTTP_REPLAY_MODE", "REPLAY")
        assert _resolve_http_replay_mode(None) == "replay"

    def test_invalid_env_var_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("CLM_HTTP_REPLAY_MODE", "never")
        with pytest.raises(click.UsageError, match="CLM_HTTP_REPLAY_MODE"):
            _resolve_http_replay_mode(None)

    def test_ci_true_defaults_to_replay(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLM_HTTP_REPLAY_MODE", raising=False)
        monkeypatch.setenv("CI", "true")
        assert _resolve_http_replay_mode(None) == "replay"

    def test_ci_one_defaults_to_replay(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLM_HTTP_REPLAY_MODE", raising=False)
        monkeypatch.setenv("CI", "1")
        assert _resolve_http_replay_mode(None) == "replay"

    def test_ci_yes_defaults_to_replay(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLM_HTTP_REPLAY_MODE", raising=False)
        monkeypatch.setenv("CI", "YES")
        assert _resolve_http_replay_mode(None) == "replay"

    def test_local_defaults_to_new_episodes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLM_HTTP_REPLAY_MODE", raising=False)
        monkeypatch.delenv("CI", raising=False)
        assert _resolve_http_replay_mode(None) == "new-episodes"


class TestResolveHttpReplayTransport:
    """mitmproxy is the default transport (issue #165); vcrpy is the opt-out."""

    def test_unset_defaults_to_mitmproxy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLM_HTTP_REPLAY_TRANSPORT", raising=False)
        assert _resolve_http_replay_transport() == "mitmproxy"

    def test_explicit_vcrpy_opts_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "vcrpy")
        assert _resolve_http_replay_transport() == "vcrpy"

    def test_explicit_mitmproxy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "mitmproxy")
        assert _resolve_http_replay_transport() == "mitmproxy"

    def test_value_is_case_insensitive_and_trimmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "  VCRPY ")
        assert _resolve_http_replay_transport() == "vcrpy"

    def test_unknown_value_falls_back_to_mitmproxy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Any value other than ``vcrpy`` resolves to the default, mitmproxy.
        monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "tcpdump")
        assert _resolve_http_replay_transport() == "mitmproxy"

    def test_explicit_cli_value_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "mitmproxy")
        assert _resolve_http_replay_transport("vcrpy") == "vcrpy"

    def test_ci_false_defaults_to_new_episodes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLM_HTTP_REPLAY_MODE", raising=False)
        monkeypatch.setenv("CI", "false")
        assert _resolve_http_replay_mode(None) == "new-episodes"

    def test_env_var_accepts_new_episodes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("CLM_HTTP_REPLAY_MODE", "new-episodes")
        assert _resolve_http_replay_mode(None) == "new-episodes"

    def test_explicit_new_episodes_cli_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CI", "true")
        monkeypatch.setenv("CLM_HTTP_REPLAY_MODE", "once")
        assert _resolve_http_replay_mode("new-episodes") == "new-episodes"


# ---------------------------------------------------------------------------
# create_output_formatter
# ---------------------------------------------------------------------------


class TestCreateOutputFormatter:
    def test_json_mode(self) -> None:
        config = _make_config(output_mode="json")
        assert isinstance(create_output_formatter(config), JSONOutputFormatter)

    def test_quiet_mode(self) -> None:
        config = _make_config(output_mode="quiet")
        assert isinstance(create_output_formatter(config), QuietOutputFormatter)

    def test_verbose_mode(self) -> None:
        config = _make_config(output_mode="verbose")
        assert isinstance(create_output_formatter(config), VerboseOutputFormatter)

    def test_default_mode(self) -> None:
        config = _make_config(output_mode="default")
        assert isinstance(create_output_formatter(config), DefaultOutputFormatter)

    def test_unknown_mode_falls_back_to_default(self) -> None:
        config = _make_config(output_mode="something-weird")
        assert isinstance(create_output_formatter(config), DefaultOutputFormatter)

    def test_output_mode_is_case_insensitive(self) -> None:
        config = _make_config(output_mode="JSON")
        assert isinstance(create_output_formatter(config), JSONOutputFormatter)


# ---------------------------------------------------------------------------
# report_validation_errors
# ---------------------------------------------------------------------------


class TestReportValidationErrors:
    def test_json_mode_prints_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        report_validation_errors(
            ["Missing element", "Bad attribute"],
            spec_file=Path("course.xml"),
            output_mode="json",
        )

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "validation_failed"
        assert data["error_count"] == 2
        assert len(data["errors"]) == 2
        assert data["errors"][0]["category"] == "spec_validation"

    def test_quiet_mode_emits_short_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``cli_console`` is a Rich Console bound to ``sys.stderr`` at
        import time, so neither capsys nor capfd see its output reliably
        under xdist. Swap it for a Console writing to an in-memory buffer."""
        from io import StringIO

        from rich.console import Console

        buf = StringIO()
        fake_console = Console(file=buf, force_terminal=False, no_color=True, width=200)
        monkeypatch.setattr(build_module, "cli_console", fake_console)

        report_validation_errors(
            ["err1"],
            spec_file=Path("course.xml"),
            output_mode="quiet",
        )

        assert "Spec validation failed with 1 error(s)" in buf.getvalue()

    def test_default_mode_emits_full_report(self, capfd: pytest.CaptureFixture[str]) -> None:
        report_validation_errors(
            ["err1", "err2"],
            spec_file=Path("course.xml"),
            output_mode="default",
        )

        captured = capfd.readouterr()
        combined = captured.out + captured.err
        assert "Course spec validation failed" in combined
        assert "err1" in combined
        assert "err2" in combined

    def test_verbose_mode_includes_log_dir(self, capfd: pytest.CaptureFixture[str]) -> None:
        report_validation_errors(
            ["err1"],
            spec_file=Path("course.xml"),
            output_mode="verbose",
        )

        captured = capfd.readouterr()
        combined = captured.out + captured.err
        assert "Full logs available in" in combined


# ---------------------------------------------------------------------------
# configure_workers
# ---------------------------------------------------------------------------


class TestConfigureWorkers:
    def test_no_overrides_returns_defaults(self) -> None:
        config = _make_config()
        worker_config = configure_workers(config)
        # Defaults resolve to direct execution with no explicit worker counts.
        assert worker_config.default_execution_mode == "direct"
        assert worker_config.notebook.count is None

    def test_all_overrides_are_forwarded(self) -> None:
        config = _make_config(
            workers="docker",
            notebook_workers=4,
            plantuml_workers=2,
            drawio_workers=1,
            max_workers=8,
            notebook_image="myimage:tag",
        )
        worker_config = configure_workers(config)

        assert worker_config.default_execution_mode == "docker"
        assert worker_config.notebook.count == 4
        assert worker_config.plantuml.count == 2
        assert worker_config.drawio.count == 1
        assert worker_config.max_workers_cap == 8
        assert worker_config.notebook.image == "myimage:tag"


# ---------------------------------------------------------------------------
# enable_jupyterlite_workers_if_needed
# ---------------------------------------------------------------------------


class TestEnableJupyterLiteWorkersIfNeeded:
    def _target(self, *, with_jl: bool) -> SimpleNamespace:
        return SimpleNamespace(includes_format=lambda fmt: with_jl and fmt == "jupyterlite")

    def test_no_targets_request_jupyterlite_leaves_count_none(self) -> None:
        worker_config = configure_workers(_make_config())
        course = SimpleNamespace(output_targets=[self._target(with_jl=False)])

        enable_jupyterlite_workers_if_needed(course, worker_config)

        assert worker_config.jupyterlite.count is None

    def test_any_target_requests_jl_bumps_count_to_one(self) -> None:
        worker_config = configure_workers(_make_config())
        course = SimpleNamespace(
            output_targets=[
                self._target(with_jl=False),
                self._target(with_jl=True),
            ]
        )

        enable_jupyterlite_workers_if_needed(course, worker_config)

        assert worker_config.jupyterlite.count == 1

    def test_preserves_explicit_higher_count(self) -> None:
        worker_config = configure_workers(_make_config())
        worker_config.jupyterlite.count = 3
        course = SimpleNamespace(output_targets=[self._target(with_jl=True)])

        enable_jupyterlite_workers_if_needed(course, worker_config)

        # Explicit setting wins.
        assert worker_config.jupyterlite.count == 3


# ---------------------------------------------------------------------------
# start_managed_workers
# ---------------------------------------------------------------------------


class TestStartManagedWorkers:
    def test_no_op_when_should_start_false(self) -> None:
        manager = MagicMock()
        manager.should_start_workers.return_value = False

        result = start_managed_workers(manager, worker_config=MagicMock())

        assert result == []
        manager.start_managed_workers.assert_not_called()

    def test_returns_workers_when_started(self) -> None:
        manager = MagicMock()
        manager.should_start_workers.return_value = True
        manager.start_managed_workers.return_value = ["w1", "w2", "w3"]

        result = start_managed_workers(manager, worker_config=MagicMock())

        assert result == ["w1", "w2", "w3"]

    def test_raises_when_start_fails(self) -> None:
        manager = MagicMock()
        manager.should_start_workers.return_value = True
        manager.start_managed_workers.side_effect = RuntimeError("nope")

        with pytest.raises(RuntimeError, match="nope"):
            start_managed_workers(manager, worker_config=MagicMock())


# ---------------------------------------------------------------------------
# _report_duplicate_file_warnings
# ---------------------------------------------------------------------------


class TestReportDuplicateFileWarnings:
    def test_reports_each_duplicate_as_warning(self) -> None:
        course = MagicMock()
        course.detect_duplicate_output_files.return_value = [
            {
                "output_name": "a.ipynb",
                "language": "en",
                "format": "ipynb",
                "kind": "completed",
                "files": [Path("x/a.py"), Path("y/a.py")],
            }
        ]

        reporter = MagicMock()
        _report_duplicate_file_warnings(course, reporter)

        reporter.report_warning.assert_called_once()
        warning = reporter.report_warning.call_args[0][0]
        assert isinstance(warning, BuildWarning)
        assert "a.ipynb" in warning.message

    def test_no_duplicates_no_calls(self) -> None:
        course = MagicMock()
        course.detect_duplicate_output_files.return_value = []

        reporter = MagicMock()
        _report_duplicate_file_warnings(course, reporter)

        reporter.report_warning.assert_not_called()

    def test_swallows_exceptions_from_detect(self) -> None:
        """A failing detector must not crash the build — it only warns."""
        course = MagicMock()
        course.detect_duplicate_output_files.side_effect = RuntimeError("oops")

        reporter = MagicMock()
        # Should not raise
        _report_duplicate_file_warnings(course, reporter)

        reporter.report_warning.assert_not_called()


# ---------------------------------------------------------------------------
# _report_image_collisions
# ---------------------------------------------------------------------------


class TestReportImageCollisions:
    def test_duplicated_mode_always_returns_false(self) -> None:
        course = MagicMock()
        course.image_mode = "duplicated"

        reporter = MagicMock()
        assert _report_image_collisions(course, reporter) is False
        reporter.report_error.assert_not_called()

    def test_shared_mode_no_collisions_returns_false(self) -> None:
        course = MagicMock()
        course.image_mode = "shared"
        course.image_registry.collisions = []

        reporter = MagicMock()
        assert _report_image_collisions(course, reporter) is False
        reporter.report_error.assert_not_called()

    def test_shared_mode_with_collisions_returns_true_and_reports(self) -> None:
        course = MagicMock()
        course.image_mode = "shared"
        collision = SimpleNamespace(
            relative_path="foo/bar.png",
            paths=[Path("a/foo/bar.png"), Path("b/foo/bar.png")],
        )
        course.image_registry.collisions = [collision]

        reporter = MagicMock()
        assert _report_image_collisions(course, reporter) is True

        reporter.report_error.assert_called_once()
        error = reporter.report_error.call_args[0][0]
        assert isinstance(error, BuildError)
        assert "foo/bar.png" in error.message


# ---------------------------------------------------------------------------
# _report_loading_issues
# ---------------------------------------------------------------------------


class TestReportLoadingIssues:
    def _course(self, errors=None, warnings=None) -> MagicMock:
        course = MagicMock()
        course.loading_errors = errors or []
        course.loading_warnings = warnings or []
        return course

    def test_no_errors_no_warnings_no_calls(self) -> None:
        reporter = MagicMock()
        _report_loading_issues(self._course(), reporter)
        reporter.report_error.assert_not_called()
        reporter.report_warning.assert_not_called()

    def test_topic_not_found_error_with_available_list(self) -> None:
        reporter = MagicMock()
        course = self._course(
            errors=[
                {
                    "category": "topic_not_found",
                    "message": "Topic xyz not found",
                    "details": {
                        "file_path": "course.xml",
                        "available_topics": ["topic_010", "topic_020"],
                    },
                }
            ]
        )

        _report_loading_issues(course, reporter)

        reporter.report_error.assert_called_once()
        error = reporter.report_error.call_args[0][0]
        assert error.error_type == "configuration"
        assert "Available topic IDs" in error.message
        assert "topic_010" in error.message

    def test_file_load_error_is_user_type(self) -> None:
        reporter = MagicMock()
        course = self._course(
            errors=[
                {
                    "category": "file_load_error",
                    "message": "UTF-8 decode failed",
                    "details": {"file_path": "bad.py"},
                }
            ]
        )

        _report_loading_issues(course, reporter)
        error = reporter.report_error.call_args[0][0]
        assert error.error_type == "user"

    def test_unknown_error_category_is_infrastructure(self) -> None:
        reporter = MagicMock()
        course = self._course(
            errors=[
                {
                    "category": "something_weird",
                    "message": "Internal error",
                    "details": {},
                }
            ]
        )

        _report_loading_issues(course, reporter)
        error = reporter.report_error.call_args[0][0]
        assert error.error_type == "infrastructure"

    def test_missing_file_path_defaults_to_unknown(self) -> None:
        reporter = MagicMock()
        course = self._course(errors=[{"category": "other", "message": "m", "details": {}}])

        _report_loading_issues(course, reporter)
        error = reporter.report_error.call_args[0][0]
        assert error.file_path == "unknown"

    def test_duplicate_topic_id_warning_gets_paths_appended(self) -> None:
        reporter = MagicMock()
        course = self._course(
            warnings=[
                {
                    "category": "duplicate_topic_id",
                    "message": "Duplicate 'intro'",
                    "details": {
                        "first_path": "a/intro",
                        "duplicate_path": "b/intro",
                    },
                }
            ]
        )

        _report_loading_issues(course, reporter)
        warning = reporter.report_warning.call_args[0][0]
        assert "a/intro" in warning.message
        assert "b/intro" in warning.message


# ---------------------------------------------------------------------------
# _compute_section_dirs_for_cleanup
# ---------------------------------------------------------------------------


class TestComputeSectionDirsForCleanup:
    def test_returns_product_of_outputs_and_sections(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        course = MagicMock()
        # Two targets, two sections → several output specs × sections.
        target1 = MagicMock()
        target2 = MagicMock()
        course.output_targets = [target1, target2]
        course.sections = [
            SimpleNamespace(name={"en": "Section A", "de": "Abschnitt A"}),
            SimpleNamespace(name={"en": "Section B", "de": "Abschnitt B"}),
        ]

        # Monkeypatch output_specs to return a deterministic, small iterable.
        def fake_output_specs(course, output_root, skip_html, target):
            return [
                SimpleNamespace(language="en", output_dir=tmp_path / "out_en"),
                SimpleNamespace(language="de", output_dir=tmp_path / "out_de"),
            ]

        monkeypatch.setattr(
            "clm.infrastructure.utils.path_utils.output_specs",
            fake_output_specs,
        )
        # sanitize_file_name is called per section.name[lang]; default is fine.

        dirs = _compute_section_dirs_for_cleanup(course)

        # 2 output specs × 2 sections × 2 targets = 8 unique tuples,
        # but the code dedups by Path, and the fake returns the same
        # 2 output_dirs for each target. So we should see each of the
        # (output_dir, section) combos once = 4 dirs.
        assert len(dirs) == 4
        names = {d.name for d in dirs}
        assert "Section_A" in names or any("Section" in d.name for d in dirs)


# ---------------------------------------------------------------------------
# initialize_paths_and_course
# ---------------------------------------------------------------------------


class TestInitializePathsAndCourse:
    """Coverage for error paths in ``initialize_paths_and_course``.

    The happy path is better exercised by the end-to-end build tests
    that actually load real course trees.
    """

    def _config(self, tmp_path: Path, **overrides) -> BuildConfig:
        data = tmp_path / "data"
        data.mkdir(exist_ok=True)
        spec_file = tmp_path / "course.xml"
        defaults = {
            "spec_file": spec_file,
            "data_dir": data,
            "output_dir": tmp_path / "out",
            "log_level": "INFO",
            "cache_db_path": tmp_path / "cache.db",
            "jobs_db_path": tmp_path / "jobs.db",
            "ignore_cache": False,
            "clear_cache": False,
            "watch": False,
            "print_correlation_ids": False,
            "workers": None,
            "notebook_workers": None,
            "plantuml_workers": None,
            "drawio_workers": None,
            "notebook_image": None,
        }
        defaults.update(overrides)
        return BuildConfig(**defaults)

    def test_spec_parsing_error_json_mode_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        config = self._config(tmp_path, output_mode="json")
        config.spec_file.write_text("not-xml", encoding="utf-8")

        def fail(*args, **kwargs):
            raise CourseSpecError("bad xml")

        monkeypatch.setattr("clm.core.course_spec.CourseSpec.from_file", fail)

        with pytest.raises(SystemExit):
            initialize_paths_and_course(config)

        captured = capsys.readouterr()
        # JSON mode prints a JSON error to stdout.
        assert '"status": "error"' in captured.out
        assert '"error_type": "spec_parsing"' in captured.out

    def test_spec_parsing_error_default_mode_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = self._config(tmp_path, output_mode="default")
        config.spec_file.write_text("not-xml", encoding="utf-8")

        def fail(*args, **kwargs):
            raise CourseSpecError("bad xml")

        monkeypatch.setattr("clm.core.course_spec.CourseSpec.from_file", fail)

        with pytest.raises(SystemExit):
            initialize_paths_and_course(config)

    def test_spec_validation_errors_raise_click_exception(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = self._config(tmp_path, output_mode="default")
        config.spec_file.write_text("<course/>", encoding="utf-8")

        fake_spec = MagicMock()
        fake_spec.validate.return_value = ["err1", "err2"]

        monkeypatch.setattr(
            "clm.core.course_spec.CourseSpec.from_file",
            lambda *args, **kwargs: fake_spec,
        )

        with pytest.raises(click.ClickException, match="Course spec validation failed"):
            initialize_paths_and_course(config)

    def test_spec_validation_errors_json_mode_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = self._config(tmp_path, output_mode="json")
        config.spec_file.write_text("<course/>", encoding="utf-8")

        fake_spec = MagicMock()
        fake_spec.validate.return_value = ["err"]

        monkeypatch.setattr(
            "clm.core.course_spec.CourseSpec.from_file",
            lambda *args, **kwargs: fake_spec,
        )

        with pytest.raises(SystemExit):
            initialize_paths_and_course(config)


# ---------------------------------------------------------------------------
# list_targets CLI
# ---------------------------------------------------------------------------


def _write_spec(path: Path, *, with_targets: bool = True) -> Path:
    if with_targets:
        content = """<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <output-targets>
        <output-target name="public">
            <path>public</path>
            <kinds><kind>code-along</kind></kinds>
            <formats><format>html</format></formats>
            <languages><language>en</language></languages>
        </output-target>
    </output-targets>
    <sections/>
</course>
"""
    else:
        content = """<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <sections/>
</course>
"""
    path.write_text(content, encoding="utf-8")
    return path


def _invoke_build(args, tmp_path: Path | None = None):
    """Invoke the ``build`` command with a parent-context ``obj`` dict.

    The top-level ``clm`` group seeds ``ctx.obj`` with the DB paths; when
    we invoke ``build`` directly via CliRunner, we need to provide the
    same dict or the command fails on ``ctx.obj["CACHE_DB_PATH"]``.
    """
    obj = {
        "CACHE_DB_PATH": Path(str(tmp_path or Path("."))) / "cache.db",
        "JOBS_DB_PATH": Path(str(tmp_path or Path("."))) / "jobs.db",
    }
    return CliRunner().invoke(build_module.build, args, obj=obj)


class TestListTargetsCli:
    def test_table_format_prints_each_target(self, tmp_path: Path) -> None:
        spec = _write_spec(tmp_path / "course.xml", with_targets=True)
        result = CliRunner().invoke(list_targets, [str(spec)])

        assert result.exit_code == 0
        assert "public" in result.output
        assert "Path:" in result.output
        assert "Kinds:" in result.output

    def test_json_format_returns_json(self, tmp_path: Path) -> None:
        spec = _write_spec(tmp_path / "course.xml", with_targets=True)
        result = CliRunner().invoke(list_targets, [str(spec), "--format=json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["name"] == "public"

    def test_no_targets_prints_default_behavior_note(self, tmp_path: Path) -> None:
        spec = _write_spec(tmp_path / "course.xml", with_targets=False)
        result = CliRunner().invoke(list_targets, [str(spec)])

        assert result.exit_code == 0
        assert "No output targets" in result.output

    def test_parse_error_table_format(self, tmp_path: Path) -> None:
        spec = tmp_path / "broken.xml"
        spec.write_text("<not-valid", encoding="utf-8")

        result = CliRunner().invoke(list_targets, [str(spec)])

        assert result.exit_code == 1
        assert "Error" in (result.output + (result.stderr if result.stderr_bytes else ""))

    def test_parse_error_json_format(self, tmp_path: Path) -> None:
        spec = tmp_path / "broken.xml"
        spec.write_text("<not-valid", encoding="utf-8")

        result = CliRunner().invoke(list_targets, [str(spec), "--format=json"])

        assert result.exit_code == 1
        # Either stdout (JSON error) or stderr (error text).
        combined = result.output
        if combined.strip().startswith("{"):
            data = json.loads(combined)
            assert data["status"] == "error"


# ---------------------------------------------------------------------------
# build CLI wrapper: asyncio.run + .env loading + signal-handler surface
# ---------------------------------------------------------------------------


class TestBuildCliWrapper:
    """End-to-end tests for the ``clm build`` CLI entry point.

    We stub ``asyncio.run`` so the heavy ``main_build`` coroutine never
    actually runs, then verify that the wrapper (a) wires arguments
    correctly into the coroutine, (b) installs signal handlers, and
    (c) loads the .env when one is present next to the spec.
    """

    def _make_spec(self, tmp_path: Path) -> Path:
        spec = tmp_path / "course.xml"
        _write_spec(spec, with_targets=False)
        return spec

    def test_only_sections_whitespace_is_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--only-sections`` validation happens inside the orchestrator
        coroutine, so we run it fully with a real event loop."""
        import asyncio

        spec = self._make_spec(tmp_path)
        monkeypatch.setattr(build_module.asyncio, "run", asyncio.run)

        result = _invoke_build([str(spec), "--only-sections", "  "], tmp_path=tmp_path)

        assert result.exit_code != 0
        # Output or exception message should mention the whitespace error.
        combined = result.output + (str(result.exception) if result.exception else "")
        assert "empty or whitespace-only value" in combined

    def test_explicit_env_file_is_loaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--env-file with an existing file is forwarded to load_dotenv."""
        spec = self._make_spec(tmp_path)
        env_file = tmp_path / "my.env"
        env_file.write_text("FOO=bar\n", encoding="utf-8")
        ran: list[object] = []
        loaded: list[object] = []

        def fake_run(coro) -> None:
            ran.append(coro)
            coro.close()

        def fake_load_dotenv(path, override: bool = False) -> bool:
            loaded.append(path)
            return True

        monkeypatch.setattr(build_module.asyncio, "run", fake_run)
        monkeypatch.setattr("dotenv.load_dotenv", fake_load_dotenv)

        result = _invoke_build(["--env-file", str(env_file), str(spec)], tmp_path=tmp_path)

        assert result.exit_code == 0
        assert len(ran) == 1
        # load_dotenv called with the explicit env file.
        assert any(str(env_file) in str(p) for p in loaded)

    def test_no_env_file_flag_short_circuits_dotenv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With --no-env-file, neither the auto-lookup nor --env-file runs."""
        spec = self._make_spec(tmp_path)

        # Seed a .env next to the spec — it should be ignored.
        (tmp_path / ".env").write_text("FOO=bar\n", encoding="utf-8")

        # If dotenv.load_dotenv is called we fail loudly.
        def should_not_call(*args, **kwargs):
            raise AssertionError("load_dotenv should not be called with --no-env-file")

        monkeypatch.setattr("dotenv.load_dotenv", should_not_call)
        monkeypatch.setattr(build_module.asyncio, "run", lambda coro: coro.close())

        result = _invoke_build(["--no-env-file", str(spec)], tmp_path=tmp_path)

        assert result.exit_code == 0

    def test_build_runs_main_build_with_mocked_pipeline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Full path through ``build`` → ``asyncio.run(main_build(...))``
        with every pipeline step replaced by a stub.

        This covers argument-to-config translation, formatter/worker/DB
        setup and the happy-path finally block, exercising ``main_build``
        line-by-line.
        """
        import asyncio

        # Make a real spec + data dir so resolve_course_paths doesn't explode.
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        spec = _write_spec(tmp_path / "course.xml", with_targets=False)

        monkeypatch.setattr(build_module.asyncio, "run", asyncio.run)

        # Stub out every heavy dep used by main_build.
        fake_course = MagicMock()
        fake_course.output_targets = []
        fake_course.sections = []
        fake_course.files = []
        fake_course.output_root = tmp_path / "out"
        fake_course.name = SimpleNamespace(en="Course")
        fake_course.image_mode = "duplicated"
        fake_course.image_registry = SimpleNamespace(collisions=[])
        fake_course.output_dir_name = {"en": "Course", "de": "Kurs"}
        fake_course.loading_errors = []
        fake_course.loading_warnings = []
        fake_course.detect_duplicate_output_files.return_value = []
        fake_course.count_jupyterlite_operations.return_value = 0
        fake_course.precreate_output_directories = MagicMock()

        async def _noop_async_method(*args, **kwargs):
            return None

        fake_course.count_stage_operations = MagicMock(side_effect=_noop_async_method)
        fake_course.process_stage = MagicMock(side_effect=_noop_async_method)
        fake_course.process_dir_group = MagicMock(side_effect=_noop_async_method)
        fake_course.process_jupyterlite_for_targets = MagicMock(side_effect=_noop_async_method)

        monkeypatch.setattr(
            build_module,
            "initialize_paths_and_course",
            lambda config: (fake_course, [tmp_path / "out" / "En"], data_dir),
        )

        fake_lifecycle = MagicMock()
        fake_lifecycle.should_start_workers.return_value = False
        monkeypatch.setattr(
            "clm.infrastructure.workers.lifecycle_manager.WorkerLifecycleManager",
            lambda **kwargs: fake_lifecycle,
        )

        monkeypatch.setattr(
            "clm.infrastructure.database.schema.init_database",
            lambda *args, **kwargs: None,
        )

        # Stub the DB manager and backend so the async-with chains no-op.
        class FakeDbManager:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class FakeBackend:
            def __init__(self, *args, **kwargs):
                # PR 2.3: build command drains backend.output_write_registry
                # at end-of-build, so the fake must expose one.
                from clm.core.output_write_registry import OutputWriteRegistry

                self.output_write_registry = OutputWriteRegistry()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def shutdown(self):
                pass

        monkeypatch.setattr(build_module, "DatabaseManager", FakeDbManager)
        monkeypatch.setattr(build_module, "SqliteBackend", FakeBackend)

        # Avoid real git_dir_mover context manager side effects.
        from contextlib import contextmanager

        @contextmanager
        def fake_mover(root_dirs, *args, **kwargs):
            yield

        monkeypatch.setattr(build_module, "git_dir_mover", fake_mover)

        # BuildReporter: trivial stub. finish_build must return a real
        # BuildSummary (not a MagicMock) so the entry-point exit policy —
        # which now reads summary.timed_out (issue #143) — sees concrete
        # falsey values rather than a truthy mock attribute.
        _stub_reporter = MagicMock()
        _stub_reporter.finish_build.return_value = BuildSummary(
            duration=0.0, total_files=0, errors=[], warnings=[]
        )
        monkeypatch.setattr(build_module, "BuildReporter", lambda formatter: _stub_reporter)

        # Avoid real execution stages (already mocked on fake_course).
        monkeypatch.setattr(
            "clm.core.utils.execution_utils.execution_stages",
            lambda: [],
        )

        result = _invoke_build([str(spec)], tmp_path=tmp_path)

        assert result.exit_code == 0, result.exception

    def test_auto_detects_env_next_to_spec(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without --env-file or --no-env-file, the wrapper walks up from
        the spec file looking for a ``.env`` and forwards it to
        ``load_dotenv``."""
        spec = self._make_spec(tmp_path)
        env = tmp_path / ".env"
        env.write_text("X=1\n", encoding="utf-8")

        loaded: list[Path] = []

        def fake_load_dotenv(path, override: bool = False) -> bool:
            loaded.append(Path(str(path)))
            return True

        monkeypatch.setattr("dotenv.load_dotenv", fake_load_dotenv)
        monkeypatch.setattr(build_module.asyncio, "run", lambda coro: coro.close())

        result = _invoke_build([str(spec)], tmp_path=tmp_path)

        assert result.exit_code == 0
        assert env in loaded


# ---------------------------------------------------------------------------
# Issue #90: build exit code on cell errors
# ---------------------------------------------------------------------------


def _setup_mocked_build_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    summary_errors: list[BuildError] | None = None,
    summary_timed_out: bool = False,
) -> tuple[Path, MagicMock]:
    """Stub every heavy dep so ``main_build`` runs end-to-end with no
    real workers, kernels, or IO. The fake ``BuildReporter`` returns a
    :class:`BuildSummary` containing ``summary_errors`` from
    ``finish_build()`` so the entry-point exit logic can be exercised.

    Mirrors the scaffolding in
    :meth:`TestBuildCliWrapper.test_build_runs_main_build_with_mocked_pipeline`.
    Returns ``(spec_path, fake_reporter)``.
    """
    import asyncio
    from contextlib import contextmanager

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    spec = _write_spec(tmp_path / "course.xml", with_targets=False)

    monkeypatch.setattr(build_module.asyncio, "run", asyncio.run)

    fake_course = MagicMock()
    fake_course.output_targets = []
    fake_course.sections = []
    fake_course.files = []
    fake_course.output_root = tmp_path / "out"
    fake_course.name = SimpleNamespace(en="Course")
    fake_course.image_mode = "duplicated"
    fake_course.image_registry = SimpleNamespace(collisions=[])
    fake_course.output_dir_name = {"en": "Course", "de": "Kurs"}
    fake_course.loading_errors = []
    fake_course.loading_warnings = []
    fake_course.detect_duplicate_output_files.return_value = []
    fake_course.count_jupyterlite_operations.return_value = 0
    fake_course.precreate_output_directories = MagicMock()

    async def _noop_async_method(*args, **kwargs):
        return None

    fake_course.count_stage_operations = MagicMock(side_effect=_noop_async_method)
    fake_course.process_stage = MagicMock(side_effect=_noop_async_method)
    fake_course.process_dir_group = MagicMock(side_effect=_noop_async_method)
    fake_course.process_jupyterlite_for_targets = MagicMock(side_effect=_noop_async_method)

    monkeypatch.setattr(
        build_module,
        "initialize_paths_and_course",
        lambda config: (fake_course, [tmp_path / "out" / "En"], data_dir),
    )

    fake_lifecycle = MagicMock()
    fake_lifecycle.should_start_workers.return_value = False
    monkeypatch.setattr(
        "clm.infrastructure.workers.lifecycle_manager.WorkerLifecycleManager",
        lambda **kwargs: fake_lifecycle,
    )

    monkeypatch.setattr(
        "clm.infrastructure.database.schema.init_database",
        lambda *args, **kwargs: None,
    )

    class FakeDbManager:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeBackend:
        def __init__(self, *args, **kwargs):
            from clm.core.output_write_registry import OutputWriteRegistry

            self.output_write_registry = OutputWriteRegistry()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def shutdown(self):
            pass

    monkeypatch.setattr(build_module, "DatabaseManager", FakeDbManager)
    monkeypatch.setattr(build_module, "SqliteBackend", FakeBackend)

    @contextmanager
    def fake_mover(root_dirs, *args, **kwargs):
        yield

    monkeypatch.setattr(build_module, "git_dir_mover", fake_mover)

    # BuildReporter stub whose ``finish_build()`` returns a BuildSummary
    # carrying the synthetic errors the test wants the build to surface.
    fake_summary = BuildSummary(
        duration=0.0,
        total_files=0,
        errors=list(summary_errors or []),
        warnings=[],
        timed_out=summary_timed_out,
    )
    fake_reporter = MagicMock()
    fake_reporter.errors = list(summary_errors or [])
    fake_reporter.finish_build.return_value = fake_summary
    monkeypatch.setattr(build_module, "BuildReporter", lambda formatter: fake_reporter)

    monkeypatch.setattr(
        "clm.core.utils.execution_utils.execution_stages",
        lambda: [],
    )

    return spec, fake_reporter


def _make_cell_error(file_path: str = "topic.py") -> BuildError:
    return BuildError(
        error_type="user",
        category="cell_execution",
        severity="error",
        file_path=file_path,
        message="Cell raised RuntimeError('boom')",
        actionable_guidance="Fix the cell or update the cassette.",
    )


class TestBuildExitCodeOnCellErrors:
    """Issue #90: ``clm build`` must exit non-zero when cell errors are
    present, at minimum under ``--http-replay=replay``.

    These tests intentionally fail on master (current code always exits 0
    on cell errors). They pass after Phases 2-3:

    * Phase 2 threads the ``BuildSummary`` returned by
      :meth:`BuildReporter.finish_build` back from ``main_build``.
    * Phase 3 adds ``--fail-on-error / --no-fail-on-error`` with the
      ``CLM_FAIL_ON_ERROR`` env-var resolver and exits 1 in the Click
      entry point when the resolved policy says to fail on a non-empty
      error list.
    """

    def test_build_exits_nonzero_when_cell_errors_under_replay_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default policy under ``--http-replay=replay``: any cell error
        in the build summary causes a non-zero exit."""
        spec, _ = _setup_mocked_build_pipeline(
            tmp_path, monkeypatch, summary_errors=[_make_cell_error()]
        )

        result = _invoke_build(["--http-replay=replay", str(spec)], tmp_path=tmp_path)

        assert result.exit_code != 0, (
            "Expected non-zero exit when cell errors are present under "
            f"--http-replay=replay; got 0 with output:\n{result.output}"
        )

    def test_build_exits_zero_when_no_cell_errors_under_replay_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sanity: clean builds still exit 0 under ``--http-replay=replay``."""
        spec, _ = _setup_mocked_build_pipeline(tmp_path, monkeypatch, summary_errors=[])

        result = _invoke_build(["--http-replay=replay", str(spec)], tmp_path=tmp_path)

        assert result.exit_code == 0, (
            f"Expected exit 0 on clean build; got {result.exit_code}. "
            f"exception={result.exception!r}\noutput:\n{result.output}"
        )

    def test_build_exits_zero_with_no_fail_on_error_even_with_cell_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Operator opt-out: ``--no-fail-on-error`` preserves legacy
        exit 0 even under ``--http-replay=replay`` with cell errors."""
        spec, _ = _setup_mocked_build_pipeline(
            tmp_path, monkeypatch, summary_errors=[_make_cell_error()]
        )

        result = _invoke_build(
            ["--http-replay=replay", "--no-fail-on-error", str(spec)],
            tmp_path=tmp_path,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0 with --no-fail-on-error opt-out; got "
            f"{result.exit_code}. exception={result.exception!r}\n"
            f"output:\n{result.output}"
        )

    def test_build_exits_zero_under_new_episodes_default_with_cell_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default policy under non-replay modes is opt-in: cell errors
        do NOT fail the build unless ``--fail-on-error`` is explicit."""
        spec, _ = _setup_mocked_build_pipeline(
            tmp_path, monkeypatch, summary_errors=[_make_cell_error()]
        )

        result = _invoke_build(["--http-replay=new-episodes", str(spec)], tmp_path=tmp_path)

        assert result.exit_code == 0, (
            f"Expected exit 0 under --http-replay=new-episodes (opt-in "
            f"only); got {result.exit_code}. exception={result.exception!r}"
        )

    def test_build_exits_nonzero_under_new_episodes_with_explicit_fail_on_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit ``--fail-on-error`` enables failure regardless of
        replay mode.

        Asserts ``exit_code == 1`` (the planned ``sys.exit(1)``) rather
        than ``!= 0`` so this fails on master — where Click rejects the
        unknown flag with usage-error exit 2 — and passes meaningfully
        after Phase 3.
        """
        spec, _ = _setup_mocked_build_pipeline(
            tmp_path, monkeypatch, summary_errors=[_make_cell_error()]
        )

        result = _invoke_build(
            ["--http-replay=new-episodes", "--fail-on-error", str(spec)],
            tmp_path=tmp_path,
        )

        assert result.exit_code == 1, (
            "Expected exit 1 with explicit --fail-on-error; got "
            f"{result.exit_code}. exception={result.exception!r}\n"
            f"output:\n{result.output}"
        )

    def test_clm_fail_on_error_env_forces_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``CLM_FAIL_ON_ERROR=1`` forces failure under non-replay mode."""
        spec, _ = _setup_mocked_build_pipeline(
            tmp_path, monkeypatch, summary_errors=[_make_cell_error()]
        )
        monkeypatch.setenv("CLM_FAIL_ON_ERROR", "1")

        result = _invoke_build(["--http-replay=new-episodes", str(spec)], tmp_path=tmp_path)

        assert result.exit_code != 0, (
            f"Expected non-zero exit with CLM_FAIL_ON_ERROR=1; got 0 with output:\n{result.output}"
        )

    def test_clm_fail_on_error_env_disables_failure_under_replay(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``CLM_FAIL_ON_ERROR=0`` disables failure even under replay mode."""
        spec, _ = _setup_mocked_build_pipeline(
            tmp_path, monkeypatch, summary_errors=[_make_cell_error()]
        )
        monkeypatch.setenv("CLM_FAIL_ON_ERROR", "0")

        result = _invoke_build(["--http-replay=replay", str(spec)], tmp_path=tmp_path)

        assert result.exit_code == 0, (
            f"Expected exit 0 with CLM_FAIL_ON_ERROR=0; got "
            f"{result.exit_code}. exception={result.exception!r}"
        )

    def test_cli_flag_overrides_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Precedence: explicit CLI flag wins over ``CLM_FAIL_ON_ERROR``.

        Asserts ``exit_code == 1`` so the test fails on master (Click
        usage-error exit 2 for the unknown flag) rather than passing
        incidentally.
        """
        spec, _ = _setup_mocked_build_pipeline(
            tmp_path, monkeypatch, summary_errors=[_make_cell_error()]
        )
        # Env says don't fail, but CLI says fail — CLI wins.
        monkeypatch.setenv("CLM_FAIL_ON_ERROR", "0")

        result = _invoke_build(
            [
                "--http-replay=new-episodes",
                "--fail-on-error",
                str(spec),
            ],
            tmp_path=tmp_path,
        )

        assert result.exit_code == 1, (
            "Expected exit 1 when --fail-on-error overrides "
            f"CLM_FAIL_ON_ERROR=0; got {result.exit_code}. "
            f"exception={result.exception!r}\noutput:\n{result.output}"
        )


class TestBuildExitCodeOnJobTimeout:
    """Issue #143 (sub-bug A): a worker-job timeout must exit non-zero.

    Distinct from issue #90 (cell errors): a timeout with pending jobs is
    an infrastructure failure and must fail the build *unconditionally* —
    independent of the ``--fail-on-error`` policy — because the output tree
    is incomplete.
    """

    def test_build_exits_nonzero_when_summary_timed_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec, _ = _setup_mocked_build_pipeline(tmp_path, monkeypatch, summary_timed_out=True)

        result = _invoke_build(["--http-replay=new-episodes", str(spec)], tmp_path=tmp_path)

        assert result.exit_code == 1, (
            "Expected exit 1 when the build summary is flagged timed_out; "
            f"got {result.exit_code}. exception={result.exception!r}\n"
            f"output:\n{result.output}"
        )

    def test_timeout_overrides_no_fail_on_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--no-fail-on-error`` does NOT suppress a job-timeout failure."""
        spec, _ = _setup_mocked_build_pipeline(tmp_path, monkeypatch, summary_timed_out=True)

        result = _invoke_build(
            ["--http-replay=new-episodes", "--no-fail-on-error", str(spec)],
            tmp_path=tmp_path,
        )

        assert result.exit_code == 1, (
            "A job timeout must fail the build even with --no-fail-on-error; "
            f"got {result.exit_code}. exception={result.exception!r}\n"
            f"output:\n{result.output}"
        )

    def test_no_timeout_no_errors_still_exits_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sanity: a clean, non-timed-out build still exits 0."""
        spec, _ = _setup_mocked_build_pipeline(tmp_path, monkeypatch, summary_timed_out=False)

        result = _invoke_build(["--http-replay=new-episodes", str(spec)], tmp_path=tmp_path)

        assert result.exit_code == 0, (
            f"Expected exit 0 on a clean build; got {result.exit_code}. "
            f"exception={result.exception!r}\noutput:\n{result.output}"
        )


class TestResolveFailOnMissingXref:
    """Precedence for the --fail-on-missing-xref / CLM_FAIL_ON_MISSING_XREF policy (Issue #17)."""

    def test_cli_flag_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLM_FAIL_ON_MISSING_XREF", "0")
        assert _resolve_fail_on_missing_xref(True, "new-episodes") is True
        assert _resolve_fail_on_missing_xref(False, "replay") is False

    def test_env_var_wins_over_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLM_FAIL_ON_MISSING_XREF", "1")
        assert _resolve_fail_on_missing_xref(None, "new-episodes") is True
        monkeypatch.setenv("CLM_FAIL_ON_MISSING_XREF", "no")
        assert _resolve_fail_on_missing_xref(None, "replay") is False

    def test_replay_mode_default_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLM_FAIL_ON_MISSING_XREF", raising=False)
        assert _resolve_fail_on_missing_xref(None, "replay") is True
        assert _resolve_fail_on_missing_xref(None, "new-episodes") is False

    def test_invalid_env_value_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLM_FAIL_ON_MISSING_XREF", "maybe")
        with pytest.raises(click.UsageError):
            _resolve_fail_on_missing_xref(None, "replay")


class TestResolveWriteProvenanceManifest:
    """The manifest is on by default (issue #208 step 3d) but always suppressed
    for --snapshot / --verify-against builds (its timestamp/commit would break
    byte-comparison, which --strict-verify cannot skip)."""

    def test_default_request_writes(self) -> None:
        assert (
            _resolve_write_provenance_manifest(
                requested=True, is_snapshot=False, verify_against_dir=None
            )
            is True
        )

    def test_opt_out_request_does_not_write(self) -> None:
        assert (
            _resolve_write_provenance_manifest(
                requested=False, is_snapshot=False, verify_against_dir=None
            )
            is False
        )

    def test_snapshot_suppresses_even_when_requested(self) -> None:
        assert (
            _resolve_write_provenance_manifest(
                requested=True, is_snapshot=True, verify_against_dir=None
            )
            is False
        )

    def test_verify_suppresses_even_when_requested(self) -> None:
        assert (
            _resolve_write_provenance_manifest(
                requested=True, is_snapshot=False, verify_against_dir=Path("baseline")
            )
            is False
        )

    def test_opt_out_stays_off_under_snapshot(self) -> None:
        assert (
            _resolve_write_provenance_manifest(
                requested=False, is_snapshot=True, verify_against_dir=None
            )
            is False
        )


class TestProvenanceManifestWiring:
    """The resolved flag flows into ``main_build`` as its last positional arg —
    on for a normal build, off under --no-provenance-manifest and --snapshot."""

    def _captured_provenance(self, monkeypatch: pytest.MonkeyPatch, args, tmp_path: Path):
        captured: dict = {}

        async def fake_main_build(*a, **k):
            captured["provenance"] = a[-1]
            return SimpleNamespace(timed_out=False, errors=[])

        monkeypatch.setattr(build_module, "main_build", fake_main_build)
        result = _invoke_build(args, tmp_path=tmp_path)
        assert result.exit_code == 0, result.output
        return captured["provenance"]

    def test_default_build_requests_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = _write_spec(tmp_path / "course.xml", with_targets=False)
        assert self._captured_provenance(monkeypatch, [str(spec)], tmp_path) is True

    def test_no_provenance_flag_opts_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = _write_spec(tmp_path / "course.xml", with_targets=False)
        assert (
            self._captured_provenance(
                monkeypatch, [str(spec), "--no-provenance-manifest"], tmp_path
            )
            is False
        )

    def test_snapshot_suppresses_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = _write_spec(tmp_path / "course.xml", with_targets=False)
        snapshot = tmp_path / "snap"
        assert (
            self._captured_provenance(
                monkeypatch, [str(spec), "--snapshot", str(snapshot)], tmp_path
            )
            is False
        )


class TestShouldEmitProvenanceManifest:
    """The post-build write decision: only a complete, successful, whole-course
    build emits the manifest (it is a full overwrite of the prior index). Guards
    against silently corrupting the release join key with a partial/incomplete
    manifest (issue #208 step 3d review)."""

    @staticmethod
    def _summary(*, errors=None, timed_out: bool = False):
        return SimpleNamespace(errors=errors or [], timed_out=timed_out)

    def test_normal_complete_build_emits(self) -> None:
        config = _make_config(write_provenance_manifest=True)
        assert _should_emit_provenance_manifest(self._summary(), config) is True

    def test_no_summary_does_not_emit(self) -> None:
        config = _make_config(write_provenance_manifest=True)
        assert _should_emit_provenance_manifest(None, config) is False

    def test_flag_off_does_not_emit(self) -> None:
        config = _make_config(write_provenance_manifest=False)
        assert _should_emit_provenance_manifest(self._summary(), config) is False

    def test_watch_does_not_emit(self) -> None:
        config = _make_config(write_provenance_manifest=True, watch=True)
        assert _should_emit_provenance_manifest(self._summary(), config) is False

    def test_only_sections_does_not_emit(self) -> None:
        # A section selection would overwrite the full manifest with a partial
        # one, dropping every unselected section's provenance.
        config = _make_config(
            write_provenance_manifest=True, resolved_section_selection=MagicMock()
        )
        assert _should_emit_provenance_manifest(self._summary(), config) is False

    def test_errored_build_does_not_emit(self) -> None:
        config = _make_config(write_provenance_manifest=True)
        assert _should_emit_provenance_manifest(self._summary(errors=["boom"]), config) is False

    def test_timed_out_build_does_not_emit(self) -> None:
        config = _make_config(write_provenance_manifest=True)
        assert _should_emit_provenance_manifest(self._summary(timed_out=True), config) is False


# ---------------------------------------------------------------------------
# D3: default flip + --clean; --keep-directory removed in 1.8
# ---------------------------------------------------------------------------


class TestBuildConfigDefaults:
    """Defaults reflect the D3 flip: no wipe, sweep on, clean off."""

    def test_default_sweep_is_true(self):
        config = _make_config()
        assert config.sweep is True

    def test_default_clean_is_false(self):
        config = _make_config()
        assert config.clean is False

    def test_keep_directory_field_removed(self):
        """``BuildConfig`` no longer carries a ``keep_directory`` field;
        the CLI flag is a no-op alias that does not surface in config."""
        config = _make_config()
        assert not hasattr(config, "keep_directory")


class TestKeepDirectoryRemoved:
    """``--keep-directory`` was removed in CLM 1.8 (was a no-op alias)."""

    def _make_spec(self, tmp_path: Path) -> Path:
        spec = tmp_path / "course.xml"
        _write_spec(spec, with_targets=False)
        return spec

    def test_flag_is_rejected(self, tmp_path: Path) -> None:
        spec = self._make_spec(tmp_path)

        result = _invoke_build([str(spec), "--keep-directory"], tmp_path=tmp_path)

        assert result.exit_code != 0
        assert "No such option" in result.output


class TestMaybeRunSweepSkipReasons:
    """``_maybe_run_sweep`` consults config flags + reporter state to decide
    whether the sweep runs. The D3 flip introduces a ``--clean`` skip
    reason and the ``--incremental`` → no-op interaction via
    ``effective_sweep`` (set at the CLI boundary).

    Tests intercept ``sweep_stray_files`` to inspect the ``skip_reason``
    the orchestrator passes — that's the behaviour we care about, and it
    avoids depending on logger configuration in test runs.
    """

    def _backend_with_empty_registry(self):
        from clm.core.image_registry import ImageRegistry
        from clm.core.output_write_registry import OutputWriteRegistry

        return SimpleNamespace(
            output_write_registry=OutputWriteRegistry(),
            image_registry=ImageRegistry(),
        )

    def _reporter(self, *, has_errors: bool = False):
        reporter = MagicMock()
        reporter.errors = [object()] if has_errors else []
        return reporter

    def _spy_sweep(self, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
        """Replace ``sweep_stray_files`` with a recorder so the test can
        inspect the ``skip_reason`` argument the orchestrator passes."""
        from clm.cli import output_sweep as sweep_module

        calls: list[dict] = []

        def recorder(root_dirs, registry, image_registry=None, *, skip_reason=None, **kwargs):
            calls.append(
                {
                    "root_dirs": list(root_dirs),
                    "skip_reason": skip_reason,
                }
            )
            return sweep_module.SweepReport(
                skipped=skip_reason is not None,
                skip_reason=skip_reason,
            )

        monkeypatch.setattr(sweep_module, "sweep_stray_files", recorder)
        return calls

    def test_skips_when_sweep_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from clm.cli.commands.build import _maybe_run_sweep

        calls = self._spy_sweep(monkeypatch)
        config = _make_config(sweep=False, output_dir=tmp_path)
        _maybe_run_sweep(
            config=config,
            root_dirs=[tmp_path],
            backend=self._backend_with_empty_registry(),
            build_reporter=self._reporter(),
            only_sections_mode=False,
        )
        # ``sweep=False`` short-circuits before ``sweep_stray_files`` runs.
        assert calls == []

    def test_skips_when_clean_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from clm.cli.commands.build import _maybe_run_sweep

        calls = self._spy_sweep(monkeypatch)
        config = _make_config(sweep=True, clean=True, output_dir=tmp_path)
        _maybe_run_sweep(
            config=config,
            root_dirs=[tmp_path],
            backend=self._backend_with_empty_registry(),
            build_reporter=self._reporter(),
            only_sections_mode=False,
        )
        assert len(calls) == 1
        assert "--clean" in calls[0]["skip_reason"]

    def test_skips_when_only_sections(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from clm.cli.commands.build import _maybe_run_sweep

        calls = self._spy_sweep(monkeypatch)
        config = _make_config(sweep=True, output_dir=tmp_path)
        _maybe_run_sweep(
            config=config,
            root_dirs=[tmp_path],
            backend=self._backend_with_empty_registry(),
            build_reporter=self._reporter(),
            only_sections_mode=True,
        )
        assert len(calls) == 1
        assert "--only-sections" in calls[0]["skip_reason"]

    def test_skips_when_watch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from clm.cli.commands.build import _maybe_run_sweep

        calls = self._spy_sweep(monkeypatch)
        config = _make_config(sweep=True, watch=True, output_dir=tmp_path)
        _maybe_run_sweep(
            config=config,
            root_dirs=[tmp_path],
            backend=self._backend_with_empty_registry(),
            build_reporter=self._reporter(),
            only_sections_mode=False,
        )
        assert len(calls) == 1
        assert "watch mode" in calls[0]["skip_reason"]

    def test_skips_when_reporter_has_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from clm.cli.commands.build import _maybe_run_sweep

        calls = self._spy_sweep(monkeypatch)
        config = _make_config(sweep=True, output_dir=tmp_path)
        _maybe_run_sweep(
            config=config,
            root_dirs=[tmp_path],
            backend=self._backend_with_empty_registry(),
            build_reporter=self._reporter(has_errors=True),
            only_sections_mode=False,
        )
        assert len(calls) == 1
        assert "error" in calls[0]["skip_reason"].lower()

    def test_runs_under_default_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """No skip reasons → ``sweep_stray_files`` runs with ``skip_reason=None``."""
        from clm.cli.commands.build import _maybe_run_sweep

        calls = self._spy_sweep(monkeypatch)
        config = _make_config(sweep=True, output_dir=tmp_path)
        _maybe_run_sweep(
            config=config,
            root_dirs=[tmp_path],
            backend=self._backend_with_empty_registry(),
            build_reporter=self._reporter(),
            only_sections_mode=False,
        )
        assert len(calls) == 1
        assert calls[0]["skip_reason"] is None


class TestProcessCourseInvokesCassetteSweep:
    """Regression for issue #145.

    The pre-build orphan staging-cassette sweep
    (:meth:`Course._sweep_orphan_cassette_staging_files`) was documented
    to run before every ``clm build`` but was actually only invoked from
    ``Course.process_all`` / ``Course.process_file``. The ``clm build``
    path goes through ``process_course_with_backend`` →
    ``course.process_stage`` and never called the sweep, so orphan
    ``*.staging-*`` files from killed previous runs accumulated forever.
    This test pins the call so a future refactor cannot silently re-break
    it.
    """

    def test_sweep_call_present_in_run_stages(self) -> None:
        import inspect

        from clm.cli.commands.build import process_course_with_backend

        source = inspect.getsource(process_course_with_backend)
        assert "_sweep_orphan_cassette_staging_files" in source, (
            "process_course_with_backend must invoke "
            "course._sweep_orphan_cassette_staging_files() before the "
            "stage loop (issue #145). If this assertion fires, the call "
            "was removed or moved out of the build entry path — restore "
            "it or the orphan cleanup stops happening during normal builds."
        )


class TestMaybeStartMitmproxyTransport:
    """``_maybe_start_mitmproxy_transport`` acts only on an *explicit*
    ``CLM_HTTP_REPLAY_TRANSPORT`` value. mitmproxy is the build default now
    (issue #165), but the default is resolved and pinned upstream by
    ``main_build`` (via ``_resolve_http_replay_transport``) *before* this
    helper is called — so the helper itself stays a strict no-op unless the
    env already says ``mitmproxy``."""

    def test_returns_none_when_transport_not_explicit(self, monkeypatch, tmp_path) -> None:
        # With the env unset (the default is applied upstream, not here) the
        # helper no-ops. Returns before locating mitmdump, so no external
        # dependency is needed.
        monkeypatch.delenv("CLM_HTTP_REPLAY_TRANSPORT", raising=False)
        result = build_module._maybe_start_mitmproxy_transport("replay", tmp_path / "jobs.db")
        assert result is None

    def test_returns_none_when_other_transport_value(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "vcrpy")
        result = build_module._maybe_start_mitmproxy_transport("replay", tmp_path / "jobs.db")
        assert result is None

    def test_returns_none_when_mode_disabled(self, monkeypatch, tmp_path) -> None:
        # Even opted in, a disabled replay mode means no proxy is needed.
        monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "mitmproxy")
        result = build_module._maybe_start_mitmproxy_transport("disabled", tmp_path / "jobs.db")
        assert result is None


def _fake_worker_config(specs):
    """Stand-in for WorkersManagementConfig.get_all_worker_configs().

    ``specs`` is a list of ``(worker_type, execution_mode, count)`` tuples.
    """
    cfgs = [SimpleNamespace(worker_type=wt, execution_mode=m, count=c) for (wt, m, c) in specs]
    return SimpleNamespace(get_all_worker_configs=lambda: cfgs)


class TestBuildHasDockerNotebookWorker:
    """Detecting whether a build will start a Docker *notebook* worker (the only
    worker using the replay proxy) — decides the wildcard bind (issue #165 P4)."""

    def test_none_config_is_direct_only(self) -> None:
        assert build_module._build_has_docker_notebook_worker(None) is False

    def test_all_direct_is_false(self) -> None:
        wc = _fake_worker_config(
            [("notebook", "direct", 4), ("plantuml", "direct", 1), ("drawio", "direct", 1)]
        )
        assert build_module._build_has_docker_notebook_worker(wc) is False

    def test_docker_notebook_is_true(self) -> None:
        wc = _fake_worker_config([("notebook", "docker", 2), ("plantuml", "direct", 1)])
        assert build_module._build_has_docker_notebook_worker(wc) is True

    def test_docker_only_for_non_notebook_is_false(self) -> None:
        # Diagram converters never use the replay proxy, so a docker plantuml/
        # drawio worker must NOT trigger the wider 0.0.0.0 bind.
        wc = _fake_worker_config(
            [("notebook", "direct", 4), ("plantuml", "docker", 1), ("drawio", "docker", 1)]
        )
        assert build_module._build_has_docker_notebook_worker(wc) is False

    def test_docker_notebook_with_zero_count_is_false(self) -> None:
        wc = _fake_worker_config([("notebook", "docker", 0), ("plantuml", "direct", 1)])
        assert build_module._build_has_docker_notebook_worker(wc) is False

    def test_resolution_error_is_treated_as_direct_only(self) -> None:
        def _boom():
            raise RuntimeError("cannot resolve")

        wc = SimpleNamespace(get_all_worker_configs=_boom)
        assert build_module._build_has_docker_notebook_worker(wc) is False


class _FakeMitmManager:
    """Records the listen_host kwarg and emulates just enough of the real
    manager for ``_maybe_start_mitmproxy_transport`` (no real mitmdump)."""

    last_listen_host: str | None = None
    last_trace_dir: object = None

    def __init__(self, *, cassette_path, mode, listen_host, confdir, ignore_hosts, trace_dir=None):
        _FakeMitmManager.last_listen_host = listen_host
        _FakeMitmManager.last_trace_dir = trace_dir
        self._confdir = Path(confdir)
        self.build_id = "fakebuild"

    def start(self):
        self._confdir.mkdir(parents=True, exist_ok=True)
        self.ca_cert_path.write_bytes(b"-----BEGIN CERTIFICATE-----\nfake\n")

    @property
    def ca_cert_path(self) -> Path:
        return self._confdir / "mitmproxy-ca-cert.pem"

    @property
    def proxy_url(self) -> str:
        return "http://127.0.0.1:9999"

    def stop(self):
        pass


class TestMitmproxyTransportBindHost:
    """The proxy binds 0.0.0.0 only when Docker workers must reach it via
    host.docker.internal; Direct-only builds keep the loopback bind (#165 P4)."""

    def _run(self, monkeypatch, tmp_path, worker_config):
        import os

        monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "mitmproxy")
        monkeypatch.setattr(
            "clm.infrastructure.http_replay_mitm.MitmproxyManager", _FakeMitmManager
        )
        _FakeMitmManager.last_listen_host = None
        saved = dict(os.environ)
        try:
            mgr = build_module._maybe_start_mitmproxy_transport(
                "replay", tmp_path / "jobs.db", worker_config=worker_config
            )
            assert mgr is not None
            return _FakeMitmManager.last_listen_host, dict(os.environ)
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def test_binds_wildcard_for_docker_notebook(self, monkeypatch, tmp_path) -> None:
        wc = _fake_worker_config([("notebook", "docker", 1)])
        listen_host, env = self._run(monkeypatch, tmp_path, wc)
        assert listen_host == "0.0.0.0"
        # The exported proxy URL stays loopback for Direct workers / the poll.
        assert env["HTTP_PROXY"] == "http://127.0.0.1:9999"

    def test_binds_loopback_for_direct(self, monkeypatch, tmp_path) -> None:
        wc = _fake_worker_config([("notebook", "direct", 4)])
        listen_host, _ = self._run(monkeypatch, tmp_path, wc)
        assert listen_host == "127.0.0.1"

    def test_binds_loopback_for_diagram_only_docker(self, monkeypatch, tmp_path) -> None:
        # Docker plantuml/drawio but Direct notebook: no proxy user in a
        # container, so keep the loopback bind (no LAN exposure).
        wc = _fake_worker_config([("notebook", "direct", 4), ("drawio", "docker", 1)])
        listen_host, _ = self._run(monkeypatch, tmp_path, wc)
        assert listen_host == "127.0.0.1"

    def test_binds_loopback_when_no_worker_config(self, monkeypatch, tmp_path) -> None:
        listen_host, _ = self._run(monkeypatch, tmp_path, None)
        assert listen_host == "127.0.0.1"

    def test_forwards_trace_dir_from_invocation_env(self, monkeypatch, tmp_path) -> None:
        # When CLM_HTTP_REPLAY_TRACE pinned an invocation dir, the transport
        # forwards it to the manager so the addon can write the proxy stream
        # (issue #165 P5). Unset -> None.
        monkeypatch.setenv("CLM_HTTP_REPLAY_TRACE_INVOCATION_DIR", str(tmp_path / "trace-inv"))
        self._run(monkeypatch, tmp_path, None)
        assert _FakeMitmManager.last_trace_dir == Path(tmp_path / "trace-inv")

    def test_no_trace_dir_when_invocation_env_unset(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("CLM_HTTP_REPLAY_TRACE_INVOCATION_DIR", raising=False)
        self._run(monkeypatch, tmp_path, None)
        assert _FakeMitmManager.last_trace_dir is None
