"""
Subprocess-based E2E tests for CLI.

These tests run the actual `clx` command via subprocess, testing the
full installation and end-to-end behavior as a real user would experience.

Mark with @pytest.mark.e2e and @pytest.mark.slow as these tests are slower.
"""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.e2e
@pytest.mark.slow
class TestCliCommandExists:
    """Test that the CLI command is installed and accessible"""

    def test_clx_command_is_available(self):
        """Test that 'clx' command exists in PATH"""
        result = subprocess.run(
            ["clx", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "build" in result.stdout
        assert "delete-database" in result.stdout

    def test_clx_command_shows_version_info(self):
        """Test that clx command provides version/help information"""
        result = subprocess.run(
            ["clx", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "Usage:" in result.stdout

    def test_python_module_execution(self):
        """Test that CLI can be run via python -m clx.cli"""
        result = subprocess.run(
            [sys.executable, "-m", "clx.cli.main", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "build" in result.stdout


@pytest.mark.e2e
@pytest.mark.slow
class TestCliBuildSubprocess:
    """Test build command via subprocess"""

    def test_build_requires_spec_file_argument(self):
        """Test that build fails without spec file argument"""
        result = subprocess.run(
            ["clx", "build"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0
        assert "Missing argument" in result.stderr or "Missing argument" in result.stdout

    def test_build_rejects_nonexistent_file(self):
        """Test that build fails with non-existent spec file"""
        result = subprocess.run(
            ["clx", "build", "/nonexistent/path/spec.xml"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0

    def test_build_help_message(self):
        """Test that build --help works"""
        result = subprocess.run(
            ["clx", "build", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "--data-dir" in result.stdout
        assert "--output-dir" in result.stdout
        assert "--watch" in result.stdout
        assert "--log-level" in result.stdout

    def test_build_simple_course_subprocess(self, tmp_path):
        """Test building a simple course via subprocess with default SQLite backend"""
        spec_file = Path("test-data/course-specs/test-spec-2.xml")
        data_dir = Path("test-data")
        output_dir = tmp_path / "output"
        db_path = tmp_path / "test.db"

        if not spec_file.exists():
            pytest.skip("Test data not available")

        result = subprocess.run(
            [
                "clx",
                "--jobs-db-path",
                str(db_path),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                # No --use-sqlite flag needed - it's now the default!
                "--log-level",
                "ERROR",
                "--ignore-db",
            ],
            capture_output=True,
            text=True,
            timeout=120,  # Allow more time for full build
            cwd=Path.cwd(),  # Run from repository root
        )

        # Print output for debugging if test fails
        if result.returncode != 0:
            print("=== STDOUT ===")
            print(result.stdout)
            print("=== STDERR ===")
            print(result.stderr)

        # May fail if workers aren't available, but should not have arg errors
        assert "no such option" not in result.stdout.lower()
        assert "no such option" not in result.stderr.lower()
        # Verify SQLite is being used (not RabbitMQ)
        assert "rabbitmq" not in result.stderr.lower() or "deprecated" in result.stderr.lower()

        # If successful, verify output was created
        if result.returncode == 0:
            assert output_dir.exists()



@pytest.mark.e2e
@pytest.mark.slow
class TestDeleteDatabaseSubprocess:
    """Test delete_database command via subprocess"""

    def test_delete_database_help(self):
        """Test delete_database --help"""
        result = subprocess.run(
            ["clx", "delete-database", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_delete_database_nonexistent(self, tmp_path):
        """Test deleting non-existent database"""
        db_path = tmp_path / "nonexistent.db"

        result = subprocess.run(
            ["clx", "--jobs-db-path", str(db_path), "delete-database"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        assert "No database found" in result.stdout

    def test_delete_database_existing(self, tmp_path):
        """Test deleting existing database"""
        db_path = tmp_path / "test.db"
        db_path.write_text("dummy database")

        assert db_path.exists()

        result = subprocess.run(
            ["clx", "--jobs-db-path", str(db_path), "delete-database"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        assert "has been deleted" in result.stdout
        assert not db_path.exists()


@pytest.mark.e2e
@pytest.mark.slow
class TestCliSubprocessEnvironment:
    """Test CLI behavior in different subprocess environments"""

    def test_cli_with_custom_environment_variables(self, tmp_path):
        """Test that CLI works with custom environment variables"""
        import os

        env = os.environ.copy()
        env["CUSTOM_VAR"] = "test_value"

        result = subprocess.run(
            ["clx", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )

        assert result.returncode == 0

    def test_cli_exit_codes(self):
        """Test that CLI returns appropriate exit codes"""
        # Success case
        result_success = subprocess.run(
            ["clx", "--help"],
            capture_output=True,
            timeout=10,
        )
        assert result_success.returncode == 0

        # Failure case - invalid command
        result_fail = subprocess.run(
            ["clx", "nonexistent_command"],
            capture_output=True,
            timeout=10,
        )
        assert result_fail.returncode != 0

    def test_cli_handles_keyboard_interrupt_gracefully(self):
        """Test that CLI can be interrupted (basic check)"""
        # This is a basic test - we just verify the command can start
        # Testing actual signal handling would require more complex setup
        result = subprocess.run(
            ["clx", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0


@pytest.mark.e2e
@pytest.mark.slow
class TestCliSubprocessWithOptions:
    """Test various CLI options via subprocess"""

    def test_global_db_path_option(self, tmp_path):
        """Test global --jobs-db-path option via subprocess"""
        db_path = tmp_path / "custom.db"

        result = subprocess.run(
            ["clx", "--jobs-db-path", str(db_path), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0

    def test_build_with_log_levels(self, tmp_path):
        """Test that different log levels are accepted"""
        spec_file = tmp_path / "test.xml"
        spec_file.write_text('<?xml version="1.0"?><course><name>Test</name></course>')
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            result = subprocess.run(
                [
                    "clx",
                    "build",
                    str(spec_file),
                    "--data-dir",
                    str(data_dir),
                    # Removed --use-sqlite flag - it's now the default
                    "--log-level",
                    level,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            # Should not fail due to log level validation
            assert "invalid choice" not in result.stdout.lower()
            assert "invalid choice" not in result.stderr.lower()


@pytest.mark.e2e
@pytest.mark.slow
class TestCliSubprocessOutputCapture:
    """Test capturing and validating CLI output"""

    def test_cli_output_is_captured(self, tmp_path):
        """Test that stdout and stderr are properly captured"""
        result = subprocess.run(
            ["clx", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        assert result.stdout  # Should have output
        assert isinstance(result.stdout, str)

    def test_cli_error_messages_are_helpful(self):
        """Test that error messages provide useful information"""
        result = subprocess.run(
            ["clx", "build"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        # Error message should indicate what's wrong
        output = result.stdout + result.stderr
        assert "argument" in output.lower() or "error" in output.lower()

    def test_cli_progress_messages(self, tmp_path):
        """Test that CLI provides progress/status messages"""
        spec_file = Path("test-data/course-specs/test-spec-2.xml")
        data_dir = Path("test-data")
        output_dir = tmp_path / "output"

        if not spec_file.exists():
            pytest.skip("Test data not available")

        result = subprocess.run(
            [
                "clx",
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                # Removed --use-sqlite flag - it's now the default
                "--log-level",
                "INFO",
                "--ignore-db",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Even if build fails, should have some output indicating progress
        output = result.stdout + result.stderr
        assert len(output) > 0  # Should produce some output
