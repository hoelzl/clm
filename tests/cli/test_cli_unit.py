"""
Unit tests for CLI using Click's CliRunner.

These tests are fast and don't require workers or subprocess execution.
They test argument parsing, validation, and basic command structure.
"""

import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from clx.cli.main import cli


class TestCliBasics:
    """Basic CLI functionality tests"""

    def test_cli_help(self):
        """Test that CLI help text is displayed"""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "build" in result.output
        assert "delete-database" in result.output

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
                '<course xmlns="https://github.com/hoelzl/clx">\n'
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
                    "--ignore-db",
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
                    "--ignore-db",
                    "--force-db-init",
                    "--keep-directory",
                    "--data-dir",
                    ".",
                ],
            )
            # Verify no argument parsing errors
            assert "no such option" not in result.output.lower()

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
        result = runner.invoke(cli, ["delete-database", "--help"])
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
                    "delete-database",
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
                    "delete-database",
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
        from clx.core import Course, CourseSpec

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
        from clx.cli.main import BuildConfig, initialize_paths_and_course

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
            ignore_db=False,
            force_db_init=False,
            keep_directory=False,
            watch=False,
            print_correlation_ids=False,
            workers=None,
            notebook_workers=None,
            plantuml_workers=None,
            drawio_workers=None,
            no_auto_start=False,
            no_auto_stop=False,
            fresh_workers=False,
        )

        # Initialize paths and course
        course, root_dirs = initialize_paths_and_course(config)

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
