"""Tests for .env file loading in the build command."""

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from clm.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def spec_dir(tmp_path):
    """Create a minimal spec file in a temp directory."""
    spec_file = tmp_path / "course.xml"
    spec_file.write_text(
        textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <course>
            <name lang="en">Test Course</name>
            <name lang="de">Testkurs</name>
        </course>
        """)
    )
    return tmp_path


class TestEnvFileLoadingOptions:
    """Test that --env-file and --no-env-file options are accepted."""

    def test_build_help_shows_env_file_option(self, runner):
        result = runner.invoke(cli, ["build", "--help"])
        assert result.exit_code == 0
        assert "--env-file" in result.output
        assert "--no-env-file" in result.output

    def test_env_file_option_rejects_nonexistent_file(self, runner, spec_dir):
        result = runner.invoke(
            cli, ["build", "--env-file", "nonexistent.env", str(spec_dir / "course.xml")]
        )
        # Click validates the file exists before the command runs
        assert result.exit_code != 0


class TestEnvFileLoading:
    """Test that .env files are loaded into os.environ."""

    def test_env_file_auto_detected_from_spec_dir(self, spec_dir):
        """A .env file next to the spec file is auto-loaded."""
        env_file = spec_dir / ".env"
        env_file.write_text("CLM_TEST_AUTO_DETECT=hello_from_dotenv\n")

        # Patch main_build to just check that the env var was set
        captured_env = {}

        async def fake_main_build(*args, **kwargs):
            captured_env["CLM_TEST_AUTO_DETECT"] = os.environ.get("CLM_TEST_AUTO_DETECT")

        with patch("clm.cli.commands.build.main_build", fake_main_build):
            runner = CliRunner()
            result = runner.invoke(cli, ["build", str(spec_dir / "course.xml")])

        assert captured_env.get("CLM_TEST_AUTO_DETECT") == "hello_from_dotenv"

        # Cleanup
        os.environ.pop("CLM_TEST_AUTO_DETECT", None)

    def test_explicit_env_file_loaded(self, spec_dir):
        """An explicit --env-file is loaded."""
        env_file = spec_dir / "custom.env"
        env_file.write_text("CLM_TEST_EXPLICIT=explicit_value\n")

        captured_env = {}

        async def fake_main_build(*args, **kwargs):
            captured_env["CLM_TEST_EXPLICIT"] = os.environ.get("CLM_TEST_EXPLICIT")

        with patch("clm.cli.commands.build.main_build", fake_main_build):
            runner = CliRunner()
            result = runner.invoke(
                cli, ["build", "--env-file", str(env_file), str(spec_dir / "course.xml")]
            )

        assert captured_env.get("CLM_TEST_EXPLICIT") == "explicit_value"

        # Cleanup
        os.environ.pop("CLM_TEST_EXPLICIT", None)

    def test_no_env_file_flag_skips_loading(self, spec_dir):
        """.env file is NOT loaded when --no-env-file is passed."""
        env_file = spec_dir / ".env"
        env_file.write_text("CLM_TEST_SKIP=should_not_appear\n")

        captured_env = {}

        async def fake_main_build(*args, **kwargs):
            captured_env["CLM_TEST_SKIP"] = os.environ.get("CLM_TEST_SKIP")

        with patch("clm.cli.commands.build.main_build", fake_main_build):
            runner = CliRunner()
            result = runner.invoke(cli, ["build", "--no-env-file", str(spec_dir / "course.xml")])

        assert captured_env.get("CLM_TEST_SKIP") is None

    def test_env_file_does_not_override_existing_vars(self, spec_dir):
        """.env loading uses override=False, so existing env vars are kept."""
        env_file = spec_dir / ".env"
        env_file.write_text("CLM_TEST_EXISTING=from_dotenv\n")

        captured_env = {}

        async def fake_main_build(*args, **kwargs):
            captured_env["CLM_TEST_EXISTING"] = os.environ.get("CLM_TEST_EXISTING")

        os.environ["CLM_TEST_EXISTING"] = "already_set"
        try:
            with patch("clm.cli.commands.build.main_build", fake_main_build):
                runner = CliRunner()
                result = runner.invoke(cli, ["build", str(spec_dir / "course.xml")])

            assert captured_env.get("CLM_TEST_EXISTING") == "already_set"
        finally:
            os.environ.pop("CLM_TEST_EXISTING", None)

    def test_no_env_file_present_is_silent(self, spec_dir):
        """When no .env file exists, the build proceeds without error."""
        # Ensure no .env file exists
        env_path = spec_dir / ".env"
        if env_path.exists():
            env_path.unlink()

        async def fake_main_build(*args, **kwargs):
            pass

        with patch("clm.cli.commands.build.main_build", fake_main_build):
            runner = CliRunner()
            result = runner.invoke(cli, ["build", str(spec_dir / "course.xml")])

        # Should not fail
        assert result.exit_code == 0
