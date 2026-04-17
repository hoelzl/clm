"""Tests for ``clm config`` commands.

Covers ``clm config init`` (both locations + --force), ``clm config show``,
and ``clm config locate``. All tests redirect ``platformdirs.user_config_dir``
into ``tmp_path`` so the user's real config file is never touched.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from clm.cli.main import cli


@pytest.fixture
def isolated_config_dirs(tmp_path, monkeypatch):
    """Redirect user + project config dirs into tmp_path.

    Patches:
    - ``platformdirs.user_config_dir`` → ``tmp_path/user``
    - cwd → ``tmp_path/project`` (so project config lives under that)
    """
    user_dir = tmp_path / "user"
    project_dir = tmp_path / "project"
    user_dir.mkdir()
    project_dir.mkdir()

    # platformdirs is imported by clm.infrastructure.config; patch it there too.
    monkeypatch.setattr(
        "clm.infrastructure.config.platformdirs.user_config_dir",
        lambda *a, **kw: str(user_dir),
    )
    # Some code paths call Path.cwd() directly.
    monkeypatch.chdir(project_dir)

    return {
        "user": user_dir / "config.toml",
        "project": project_dir / ".clm" / "config.toml",
        "user_dir": user_dir,
        "project_dir": project_dir,
    }


class TestConfigInit:
    def test_init_creates_user_config_by_default(self, isolated_config_dirs):
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "init"])
        assert result.exit_code == 0, result.output
        assert isolated_config_dirs["user"].exists()
        assert "Created configuration file" in result.output
        content = isolated_config_dirs["user"].read_text()
        # Example config must document a handful of expected sections.
        assert "[paths]" in content
        assert "[logging]" in content
        assert "[worker_management]" in content

    def test_init_creates_project_config(self, isolated_config_dirs):
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "init", "--location", "project"])
        assert result.exit_code == 0, result.output
        assert isolated_config_dirs["project"].exists()

    def test_init_refuses_to_overwrite_without_force(self, isolated_config_dirs):
        cfg = isolated_config_dirs["user"]
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("# existing\n")

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "init"])

        assert result.exit_code == 0
        assert "already exists" in result.output
        assert "Use --force" in result.output
        # File content is untouched.
        assert cfg.read_text() == "# existing\n"

    def test_init_with_force_overwrites(self, isolated_config_dirs):
        cfg = isolated_config_dirs["user"]
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("# existing\n")

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "init", "--force"])

        assert result.exit_code == 0, result.output
        assert "Created configuration file" in result.output
        # Now contains the templated content, not the sentinel.
        assert cfg.read_text() != "# existing\n"
        assert "[paths]" in cfg.read_text()

    def test_init_reports_permission_error(self, isolated_config_dirs):
        runner = CliRunner()
        with patch(
            "clm.infrastructure.config.write_example_config",
            side_effect=PermissionError("denied"),
        ):
            result = runner.invoke(cli, ["config", "init"])
        assert result.exit_code == 0  # command echoes error, doesn't raise
        assert "Permission denied" in result.output

    def test_init_reports_generic_error(self, isolated_config_dirs):
        runner = CliRunner()
        with patch(
            "clm.infrastructure.config.write_example_config",
            side_effect=RuntimeError("broke"),
        ):
            result = runner.invoke(cli, ["config", "init"])
        assert "Error creating configuration file" in result.output


class TestConfigShow:
    def test_show_prints_all_sections(self, isolated_config_dirs):
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0, result.output
        assert "Current CLM Configuration" in result.output
        for header in (
            "[Paths]",
            "[External Tools]",
            "[Logging]",
            "[Jupyter]",
            "[Workers]",
        ):
            assert header in result.output

    def test_show_reflects_project_config_values(self, isolated_config_dirs):
        project_cfg = isolated_config_dirs["project"]
        project_cfg.parent.mkdir(parents=True, exist_ok=True)
        project_cfg.write_text(
            '[logging]\nlog_level = "DEBUG"\n[paths]\ncache_db_path = "custom_cache.db"\n'
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0, result.output
        assert "DEBUG" in result.output
        assert "custom_cache.db" in result.output


class TestConfigLocate:
    def test_locate_shows_all_locations(self, isolated_config_dirs):
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "locate"])
        assert result.exit_code == 0, result.output
        assert "System config" in result.output
        assert "User config" in result.output
        assert "Project config" in result.output
        assert "Priority order" in result.output

    def test_locate_marks_existing_config_files(self, isolated_config_dirs):
        # Create a user config so locate detects it.
        cfg = isolated_config_dirs["user"]
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("# dummy\n")

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "locate"])
        assert result.exit_code == 0
        # User line should say "Exists"; project line "Not found".
        output = result.output
        user_idx = output.index("User config:")
        project_idx = output.index("Project config")
        assert "Exists" in output[user_idx:project_idx]
        assert "Not found" in output[project_idx:]


class TestConfigHelp:
    def test_group_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "--help"])
        assert result.exit_code == 0
        for sub in ("init", "show", "locate"):
            assert sub in result.output

    def test_init_help_lists_flags(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "init", "--help"])
        assert result.exit_code == 0
        assert "--location" in result.output
        assert "--force" in result.output

    def test_init_rejects_invalid_location(self, isolated_config_dirs):
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "init", "--location", "bogus"])
        assert result.exit_code != 0
        assert "Invalid value" in result.output or "bogus" in result.output
