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

from clm.cli.build_data_classes import BuildError, BuildWarning
from clm.cli.commands import build as build_module
from clm.cli.commands.build import (
    BuildConfig,
    _compute_section_dirs_for_cleanup,
    _find_env_file,
    _report_duplicate_file_warnings,
    _report_image_collisions,
    _report_loading_issues,
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
        "keep_directory": False,
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
            "keep_directory": False,
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
                pass

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
        def fake_mover(root_dirs, keep_directory):
            yield

        monkeypatch.setattr(build_module, "git_dir_mover", fake_mover)

        # BuildReporter: trivial stub.
        monkeypatch.setattr(build_module, "BuildReporter", lambda formatter: MagicMock())

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
