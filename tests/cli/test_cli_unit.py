"""
Unit tests for CLI using Click's CliRunner.

These tests are fast and don't require workers or subprocess execution.
They test argument parsing, validation, and basic command structure.
"""

import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.main import cli


class TestCliBasics:
    """Basic CLI functionality tests"""

    def test_cli_help(self):
        """Test that CLI help text is displayed"""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "build" in result.output
        assert "db" in result.output

    def test_cli_version(self):
        """Test that 'clm --version' displays the version"""
        from clm.__version__ import __version__

        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_help_command(self):
        """Test that 'clm help' works and shows the same output as 'clm --help'"""
        runner = CliRunner()
        help_result = runner.invoke(cli, ["help"])
        flag_result = runner.invoke(cli, ["--help"])
        assert help_result.exit_code == 0
        assert help_result.output == flag_result.output

    def test_help_command_lists_commands(self):
        """Test that 'clm help' lists available commands"""
        runner = CliRunner()
        result = runner.invoke(cli, ["help"])
        assert result.exit_code == 0
        assert "build" in result.output
        assert "status" in result.output
        assert "Commands:" in result.output

    def test_cli_with_no_command(self):
        """Test that CLI shows help when no command is provided"""
        runner = CliRunner()
        result = runner.invoke(cli, [])
        # Click returns 2 when no command is provided
        assert result.exit_code != 1  # Should not be an error
        assert "Usage:" in result.output or "Commands:" in result.output


class TestBuildCommandArguments:
    """Test argument parsing and validation for build command"""

    def test_build_help(self):
        """Test build command help text"""
        runner = CliRunner()
        result = runner.invoke(cli, ["build", "--help"])
        assert result.exit_code == 0
        # Click shows argument as SPEC_FILE or spec_file
        assert "spec" in result.output.lower()
        assert "--data-dir" in result.output
        assert "--output-dir" in result.output
        assert "--watch" in result.output
        assert "--log-level" in result.output

    def test_build_requires_spec_file(self):
        """Test that build command requires spec-file argument"""
        runner = CliRunner()
        result = runner.invoke(cli, ["build"])
        assert result.exit_code != 0
        assert "Missing argument" in result.output or "Error" in result.output

    def test_build_rejects_nonexistent_spec_file(self):
        """Test that build command rejects non-existent spec files"""
        runner = CliRunner()
        result = runner.invoke(cli, ["build", "/nonexistent/spec.xml"])
        assert result.exit_code != 0
        assert "does not exist" in result.output.lower() or "error" in result.output.lower()

    def test_build_accepts_valid_options(self):
        """Test that build command accepts valid option combinations"""
        runner = CliRunner()
        with runner.isolated_filesystem():
            # Create a minimal test spec file
            spec_path = Path("test-spec.xml")
            spec_path.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<course xmlns="https://github.com/hoelzl/clm">\n'
                "  <name>test-course</name>\n"
                "</course>"
            )

            # This will fail during execution but should accept the arguments
            result = runner.invoke(
                cli,
                [
                    "build",
                    str(spec_path),
                    "--data-dir",
                    ".",
                    "--output-dir",
                    "output",
                    "--log-level",
                    "INFO",
                    "--ignore-cache",
                ],
            )
            # We're not checking exit code here because the command may fail
            # during execution, but we verify the arguments were accepted
            # by checking there's no argument parsing error
            if result.exit_code != 0:
                # Should not have argument parsing errors
                assert "no such option" not in result.output.lower()
                assert "missing argument" not in result.output.lower()

    def test_build_log_level_validation(self):
        """Test that build command validates log level choices"""
        runner = CliRunner()
        with runner.isolated_filesystem():
            spec_path = Path("test-spec.xml")
            spec_path.write_text("<course></course>")

            result = runner.invoke(
                cli,
                [
                    "build",
                    str(spec_path),
                    "--log-level",
                    "INVALID",
                ],
            )
            assert result.exit_code != 0
            assert "invalid choice" in result.output.lower() or "error" in result.output.lower()

    def test_build_accepts_valid_log_levels(self):
        """Test that build command accepts all valid log levels"""
        runner = CliRunner()
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

        with runner.isolated_filesystem():
            spec_path = Path("test-spec.xml")
            spec_path.write_text("<course></course>")

            for level in valid_levels:
                result = runner.invoke(
                    cli,
                    [
                        "build",
                        str(spec_path),
                        "--log-level",
                        level,
                        "--data-dir",
                        ".",
                    ],
                )
                # Should not have log level validation errors
                assert "invalid choice" not in result.output.lower()

    def test_build_boolean_flags(self):
        """Test that boolean flags are accepted"""
        runner = CliRunner()
        with runner.isolated_filesystem():
            spec_path = Path("test-spec.xml")
            spec_path.write_text("<course></course>")

            result = runner.invoke(
                cli,
                [
                    "build",
                    str(spec_path),
                    "--watch",
                    "--print-correlation-ids",
                    "--ignore-cache",
                    "--clear-cache",
                    "--data-dir",
                    ".",
                ],
            )
            # Verify no argument parsing errors
            assert "no such option" not in result.output.lower()

    def test_build_language_option(self):
        """Test that --language option is accepted with valid values"""
        runner = CliRunner()
        with runner.isolated_filesystem():
            spec_path = Path("test-spec.xml")
            spec_path.write_text("<course></course>")

            # Test with German
            result = runner.invoke(
                cli,
                [
                    "build",
                    str(spec_path),
                    "--language",
                    "de",
                    "--data-dir",
                    ".",
                ],
            )
            assert "no such option" not in result.output.lower()
            assert "invalid choice" not in result.output.lower()

            # Test with English
            result = runner.invoke(
                cli,
                [
                    "build",
                    str(spec_path),
                    "--language",
                    "en",
                    "--data-dir",
                    ".",
                ],
            )
            assert "no such option" not in result.output.lower()
            assert "invalid choice" not in result.output.lower()

            # Test short form -L
            result = runner.invoke(
                cli,
                [
                    "build",
                    str(spec_path),
                    "-L",
                    "de",
                    "--data-dir",
                    ".",
                ],
            )
            assert "no such option" not in result.output.lower()
            assert "invalid choice" not in result.output.lower()

    def test_build_language_option_invalid_choice(self):
        """Test that --language rejects invalid values"""
        runner = CliRunner()
        with runner.isolated_filesystem():
            spec_path = Path("test-spec.xml")
            spec_path.write_text("<course></course>")

            result = runner.invoke(
                cli,
                [
                    "build",
                    str(spec_path),
                    "--language",
                    "fr",  # Invalid language
                    "--data-dir",
                    ".",
                ],
            )
            assert result.exit_code != 0
            # Click may use "invalid choice" or "invalid value"
            assert "invalid" in result.output.lower()

    def test_build_speaker_only_flag(self):
        """Test that --speaker-only flag is accepted"""
        runner = CliRunner()
        with runner.isolated_filesystem():
            spec_path = Path("test-spec.xml")
            spec_path.write_text("<course></course>")

            result = runner.invoke(
                cli,
                [
                    "build",
                    str(spec_path),
                    "--speaker-only",
                    "--data-dir",
                    ".",
                ],
            )
            assert "no such option" not in result.output.lower()

    def test_build_language_and_speaker_only_combined(self):
        """Test that --language and --speaker-only can be combined"""
        runner = CliRunner()
        with runner.isolated_filesystem():
            spec_path = Path("test-spec.xml")
            spec_path.write_text("<course></course>")

            result = runner.invoke(
                cli,
                [
                    "build",
                    str(spec_path),
                    "--language",
                    "en",
                    "--speaker-only",
                    "--data-dir",
                    ".",
                ],
            )
            assert "no such option" not in result.output.lower()
            assert "invalid choice" not in result.output.lower()

    def test_build_db_path_option(self):
        """Test that global --jobs-db-path and --cache-db-path options are accepted"""
        runner = CliRunner()
        with runner.isolated_filesystem():
            spec_path = Path("test-spec.xml")
            spec_path.write_text("<course></course>")

            # Test --jobs-db-path option
            result = runner.invoke(
                cli,
                [
                    "--jobs-db-path",
                    "custom_jobs.db",
                    "build",
                    str(spec_path),
                    "--data-dir",
                    ".",
                ],
            )
            # Verify no argument parsing errors
            assert "no such option" not in result.output.lower()
            assert "missing argument" not in result.output.lower()

            # Test --cache-db-path option
            result = runner.invoke(
                cli,
                [
                    "--cache-db-path",
                    "custom_cache.db",
                    "build",
                    str(spec_path),
                    "--data-dir",
                    ".",
                ],
            )
            # Verify no argument parsing errors
            assert "no such option" not in result.output.lower()
            assert "missing argument" not in result.output.lower()

            # Test both options together
            result = runner.invoke(
                cli,
                [
                    "--jobs-db-path",
                    "custom_jobs.db",
                    "--cache-db-path",
                    "custom_cache.db",
                    "build",
                    str(spec_path),
                    "--data-dir",
                    ".",
                ],
            )
            # Verify no argument parsing errors
            assert "no such option" not in result.output.lower()
            assert "missing argument" not in result.output.lower()


class TestDeleteDatabaseCommand:
    """Test delete_database command"""

    def test_delete_database_help(self):
        """Test delete_database command help text"""
        runner = CliRunner()
        result = runner.invoke(cli, ["db", "delete", "--help"])
        assert result.exit_code == 0

    def test_delete_database_when_not_exists(self):
        """Test delete_database when database doesn't exist"""
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(
                cli,
                [
                    "--cache-db-path",
                    "nonexistent_cache.db",
                    "--jobs-db-path",
                    "nonexistent_jobs.db",
                    "db",
                    "delete",
                ],
            )
            assert result.exit_code == 0
            assert "No databases found" in result.output

    def test_delete_database_when_exists(self):
        """Test delete_database when database exists"""
        runner = CliRunner()
        with runner.isolated_filesystem():
            # Create dummy database files
            cache_db_path = Path("test_cache.db")
            jobs_db_path = Path("test_jobs.db")
            cache_db_path.write_text("dummy")
            jobs_db_path.write_text("dummy")

            result = runner.invoke(
                cli,
                [
                    "--cache-db-path",
                    str(cache_db_path),
                    "--jobs-db-path",
                    str(jobs_db_path),
                    "db",
                    "delete",
                ],
            )
            assert result.exit_code == 0
            assert "Deleted:" in result.output
            assert not cache_db_path.exists()
            assert not jobs_db_path.exists()


class TestCliIsolation:
    """Test CLI command isolation and runner behavior"""

    def test_multiple_invocations_are_isolated(self):
        """Test that multiple CLI invocations don't interfere"""
        runner = CliRunner()

        # First invocation
        result1 = runner.invoke(cli, ["--help"])
        assert result1.exit_code == 0

        # Second invocation should work independently
        result2 = runner.invoke(cli, ["--help"])
        assert result2.exit_code == 0
        assert result1.output == result2.output

    def test_isolated_filesystem_provides_temp_directory(self):
        """Test that isolated_filesystem provides a working temp directory"""
        runner = CliRunner()
        with runner.isolated_filesystem() as temp_dir:
            temp_path = Path(temp_dir)
            assert temp_path.exists()
            assert temp_path.is_dir()

            # Can create files in isolated filesystem
            test_file = temp_path / "test.txt"
            test_file.write_text("test")
            assert test_file.exists()


class TestCourseOutputAttribute:
    """Test that Course object attribute names are used correctly in CLI"""

    def test_course_has_output_root_not_output_dir(self):
        """Test that Course class uses output_root attribute, not output_dir"""
        from clm.core import Course, CourseSpec

        # Use existing test spec file
        test_data_dir = Path(__file__).parent.parent / "test-data"
        spec_path = test_data_dir / "course-specs" / "test-spec-1.xml"

        # Create course object
        spec = CourseSpec.from_file(spec_path)
        course_root = test_data_dir
        output_root = test_data_dir / "output"
        course = Course.from_spec(spec, course_root, output_root)

        # Verify Course has output_root attribute
        assert hasattr(course, "output_root")
        assert course.output_root == output_root

        # Verify Course does NOT have output_dir attribute
        assert not hasattr(course, "output_dir"), (
            "Course object should use 'output_root' attribute, not 'output_dir'. "
            "This test catches the AttributeError bug in cli/main.py where "
            "WorkerLifecycleManager is initialized with course.output_dir instead "
            "of course.output_root."
        )

    def test_initialize_paths_returns_course_with_output_root(self):
        """Test that initialize_paths_and_course returns Course with output_root"""
        from clm.cli.main import BuildConfig, initialize_paths_and_course

        # Use existing test spec file
        test_data_dir = Path(__file__).parent.parent / "test-data"
        spec_path = test_data_dir / "course-specs" / "test-spec-1.xml"

        # Create build config
        config = BuildConfig(
            spec_file=spec_path,
            data_dir=test_data_dir,
            output_dir=test_data_dir / "output",
            log_level="INFO",
            cache_db_path=Path("cache.db"),
            jobs_db_path=Path("jobs.db"),
            ignore_cache=False,
            clear_cache=False,
            watch=False,
            print_correlation_ids=False,
            workers=None,
            notebook_workers=None,
            plantuml_workers=None,
            drawio_workers=None,
            notebook_image=None,
            language=None,
            speaker_only=False,
        )

        # Initialize paths and course
        course, root_dirs, data_dir = initialize_paths_and_course(config)

        # Verify course has output_root attribute
        assert hasattr(course, "output_root")
        assert isinstance(course.output_root, Path)

        # Verify course does NOT have output_dir attribute
        assert not hasattr(course, "output_dir"), (
            "Course object should use 'output_root' attribute, not 'output_dir'. "
            "This test catches the AttributeError bug in cli/main.py where "
            "WorkerLifecycleManager is initialized with course.output_dir instead "
            "of course.output_root."
        )


class TestOutputFiltering:
    """Tests for language and speaker-only filtering in initialize_paths_and_course"""

    def _create_config(self, language=None, speaker_only=False):
        """Helper to create BuildConfig with filter options"""
        from clm.cli.main import BuildConfig

        test_data_dir = Path(__file__).parent.parent / "test-data"
        spec_path = test_data_dir / "course-specs" / "test-spec-1.xml"

        return BuildConfig(
            spec_file=spec_path,
            data_dir=test_data_dir,
            output_dir=test_data_dir / "output",
            log_level="INFO",
            cache_db_path=Path("cache.db"),
            jobs_db_path=Path("jobs.db"),
            ignore_cache=False,
            clear_cache=False,
            watch=False,
            print_correlation_ids=False,
            workers=None,
            notebook_workers=None,
            plantuml_workers=None,
            drawio_workers=None,
            notebook_image=None,
            language=language,
            speaker_only=speaker_only,
        )

    def test_default_generates_all_root_dirs(self):
        """The default shared/trainer/speaker structure (#383) yields 8 root dirs.

        Per language, each tier contributes a cleanup root for each side it
        produces: shared (code-along/completed) → 1, trainer (also has the
        trainer kind) → 2, speaker (recording) → 1. So 4 per language × 2
        languages = 8.
        """
        from clm.cli.main import initialize_paths_and_course

        config = self._create_config()
        course, root_dirs, data_dir = initialize_paths_and_course(config)

        assert len(root_dirs) == 8

        # Course should have no filters set
        assert course.output_languages is None
        assert course.output_kinds is None

        # Every default tier directory is represented.
        joined = " ".join(str(d).lower() for d in root_dirs)
        assert "shared" in joined
        assert "trainer" in joined
        assert "speaker" in joined

    def test_single_language_filter_reduces_root_dirs(self):
        """Test that a language filter halves the root dirs (en only → 4)."""
        from clm.cli.main import initialize_paths_and_course

        config = self._create_config(language="en")
        course, root_dirs, data_dir = initialize_paths_and_course(config)

        assert len(root_dirs) == 4

        # All root dirs should be for English
        for root_dir in root_dirs:
            assert "En" in str(root_dir) or "en" in str(root_dir).lower()

        # Course should have language filter set
        assert course.output_languages == ["en"]
        assert course.output_kinds is None

    def test_speaker_only_filter_reduces_root_dirs(self):
        """``--speaker-only`` keeps only the private kinds (trainer + recording).

        Those land in the ``trainer/`` and ``speaker/`` tiers; the ``shared``
        tier (code-along/completed only) drops out entirely. With 2 languages
        that leaves 4 cleanup roots.
        """
        from clm.cli.main import initialize_paths_and_course

        config = self._create_config(speaker_only=True)
        course, root_dirs, data_dir = initialize_paths_and_course(config)

        assert len(root_dirs) == 4

        # Only the trainer/ and speaker/ tiers survive — never shared/.
        for root_dir in root_dirs:
            text = str(root_dir).lower()
            assert "trainer" in text or "speaker" in text
            assert "shared" not in text

        # Course should have kinds filter set to both private kinds.
        assert course.output_languages is None
        assert course.output_kinds == ["trainer", "recording"]

    def test_combined_filters_reduce_root_dirs(self):
        """language=de + ``--speaker-only`` → trainer/ and speaker/ tiers, de only."""
        from clm.cli.main import initialize_paths_and_course

        config = self._create_config(language="de", speaker_only=True)
        course, root_dirs, data_dir = initialize_paths_and_course(config)

        assert len(root_dirs) == 2

        for root_dir in root_dirs:
            text = str(root_dir).lower()
            assert "de" in text
            assert "trainer" in text or "speaker" in text
            assert "shared" not in text

        # Course should have both filters set; ``--speaker-only`` now selects
        # both private kinds so trainer and recording are both built.
        assert course.output_languages == ["de"]
        assert course.output_kinds == ["trainer", "recording"]

    def test_no_html_skips_html_for_all_topics(self):
        """--no-html flips skip_html on every topic before course creation"""
        from clm.cli.main import initialize_paths_and_course

        config = self._create_config()
        config.no_html = True
        course, root_dirs, data_dir = initialize_paths_and_course(config)

        notebook_files = [f for f in course.files if hasattr(f, "skip_html")]
        assert notebook_files, "test spec should contain notebook files"
        assert all(f.skip_html for f in notebook_files)

    def test_default_does_not_skip_html(self):
        """Without --no-html, notebook files keep their spec-level skip_html"""
        from clm.cli.main import initialize_paths_and_course

        config = self._create_config()
        course, root_dirs, data_dir = initialize_paths_and_course(config)

        notebook_files = [f for f in course.files if hasattr(f, "skip_html")]
        assert notebook_files, "test spec should contain notebook files"
        assert not any(f.skip_html for f in notebook_files)

    def test_no_diagrams_excludes_diagram_sources(self):
        """--no-diagrams keeps DrawIO/PlantUML sources out of the file map"""
        from clm.cli.main import initialize_paths_and_course
        from clm.core.course_files.drawio_file import DrawIoFile
        from clm.core.course_files.plantuml_file import PlantUmlFile

        config = self._create_config()
        config.no_diagrams = True
        course, root_dirs, data_dir = initialize_paths_and_course(config)

        diagram_files = [f for f in course.files if isinstance(f, (PlantUmlFile, DrawIoFile))]
        assert diagram_files == []
        # Committed rendered images are ordinary image files and still ship.
        assert any(f.path.name == "my_diag.png" for f in course.files)
        assert any(f.path.name == "my_drawing.png" for f in course.files)

    def test_default_includes_diagram_sources(self):
        """Without --no-diagrams, DrawIO/PlantUML sources schedule conversions"""
        from clm.cli.main import initialize_paths_and_course
        from clm.core.course_files.drawio_file import DrawIoFile
        from clm.core.course_files.plantuml_file import PlantUmlFile

        config = self._create_config()
        course, root_dirs, data_dir = initialize_paths_and_course(config)

        assert any(isinstance(f, PlantUmlFile) for f in course.files)
        assert any(isinstance(f, DrawIoFile) for f in course.files)

    def test_no_diagrams_disables_diagram_workers(self):
        """--no-diagrams zeroes the plantuml/drawio worker counts"""
        from clm.cli.commands.build import disable_diagram_workers_if_requested
        from clm.infrastructure.config import WorkersManagementConfig

        config = self._create_config()
        config.no_diagrams = True
        worker_config = WorkersManagementConfig()
        disable_diagram_workers_if_requested(config, worker_config)

        assert worker_config.plantuml.count == 0
        assert worker_config.drawio.count == 0
        # Zero must survive the effective-config merge (0 means "disabled",
        # not "fall back to the default count").
        assert worker_config.get_worker_config("plantuml").count == 0
        assert worker_config.get_worker_config("drawio").count == 0
        # Notebook workers are unaffected.
        assert worker_config.get_worker_config("notebook").count >= 1

    def test_default_does_not_disable_diagram_workers(self):
        """Without --no-diagrams the worker counts are left alone"""
        from clm.cli.commands.build import disable_diagram_workers_if_requested
        from clm.infrastructure.config import WorkersManagementConfig

        config = self._create_config()
        worker_config = WorkersManagementConfig()
        disable_diagram_workers_if_requested(config, worker_config)

        assert worker_config.plantuml.count is None
        assert worker_config.drawio.count is None
