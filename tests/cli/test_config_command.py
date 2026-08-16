"""Tests for ``clm config`` commands.

Covers ``clm config init`` (both locations + --force), ``clm config show``,
and ``clm config locate``. All tests redirect ``platformdirs.user_config_dir``
into ``tmp_path`` so the user's real config file is never touched.
"""

from __future__ import annotations

import json
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
        assert "[retention]" in content
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
        assert "[retention]" in cfg.read_text()

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
            "[Databases]",
            "[Authoring]",
            "[External Tools]",
            "[Logging]",
            "[Jupyter]",
            "[Workers]",
            "[Git]",
        ):
            assert header in result.output

    def test_show_reflects_project_config_values(self, isolated_config_dirs):
        project_cfg = isolated_config_dirs["project"]
        project_cfg.parent.mkdir(parents=True, exist_ok=True)
        project_cfg.write_text('[logging]\nlog_level = "DEBUG"\n')
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0, result.output
        assert "DEBUG" in result.output

    def test_show_includes_llm_cache_section(self, isolated_config_dirs):
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0, result.output
        assert "[LLM Cache]" in result.output
        assert "llm_cache_dir:" in result.output
        assert "clm-llm.sqlite" in result.output

    def test_show_json_is_machine_readable(self, isolated_config_dirs):
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        # Effective DB paths + LLM cache are resolved outside ClmConfig.
        assert set(data["databases"]) == {"cache_db_path", "jobs_db_path", "telemetry_db_path"}
        assert "dir" in data["llm_cache"]
        # Every ClmConfig section is dumped (retention is a live one).
        assert "retention" in data
        assert "logging" in data
        # use_sqlite_queue was removed — it must not resurface anywhere.
        assert "use_sqlite_queue" not in data.get("workers", {})
        # The authoring sidecar-layout is resolved outside ClmConfig too (A7).
        assert set(data["authoring"]) == {"sidecar_layout", "source", "pyproject"}

    def test_show_json_redacts_all_configured_secrets(
        self, isolated_config_dirs, monkeypatch: pytest.MonkeyPatch
    ):
        secrets = {
            "llm": "s12-llm-cleartext",
            "auphonic": "s12-auphonic-cleartext",
            "obs": "s12-obs-cleartext",
        }
        project_cfg = isolated_config_dirs["project"]
        project_cfg.parent.mkdir(parents=True, exist_ok=True)
        project_cfg.write_text(
            "[llm]\n"
            f'api_key = "{secrets["llm"]}"\n'
            "[recordings]\n"
            f'obs_password = "{secrets["obs"]}"\n'
            "[recordings.auphonic]\n"
            f'api_key = "{secrets["auphonic"]}"\n',
            encoding="utf-8",
        )
        for name in (
            "CLM_LLM__API_KEY",
            "CLM_RECORDINGS__AUPHONIC__API_KEY",
            "CLM_RECORDINGS__OBS_PASSWORD",
        ):
            monkeypatch.delenv(name, raising=False)

        result = CliRunner().invoke(cli, ["config", "show", "--json"])

        assert result.exit_code == 0, result.output
        assert not any(secret in result.output for secret in secrets.values())
        data = json.loads(result.output)
        assert data["llm"]["api_key"] == "**********"
        assert data["recordings"]["auphonic"]["api_key"] == "**********"
        assert data["recordings"]["obs_password"] == "**********"

    def test_show_json_reveal_outputs_configured_secrets_explicitly(
        self, isolated_config_dirs, monkeypatch: pytest.MonkeyPatch
    ):
        project_cfg = isolated_config_dirs["project"]
        project_cfg.parent.mkdir(parents=True, exist_ok=True)
        project_cfg.write_text(
            "[llm]\n"
            'api_key = "revealed-llm"\n'
            "[recordings]\n"
            'obs_password = "revealed-obs"\n'
            "[recordings.auphonic]\n"
            'api_key = "revealed-auphonic"\n',
            encoding="utf-8",
        )
        for name in (
            "CLM_LLM__API_KEY",
            "CLM_RECORDINGS__AUPHONIC__API_KEY",
            "CLM_RECORDINGS__OBS_PASSWORD",
        ):
            monkeypatch.delenv(name, raising=False)

        result = CliRunner().invoke(cli, ["config", "show", "--json", "--reveal"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["llm"]["api_key"] == "revealed-llm"
        assert data["recordings"]["auphonic"]["api_key"] == "revealed-auphonic"
        assert data["recordings"]["obs_password"] == "revealed-obs"

    def test_show_reveal_requires_json(self, isolated_config_dirs):
        result = CliRunner().invoke(cli, ["config", "show", "--reveal"])

        assert result.exit_code != 0
        assert "--reveal requires --json" in result.output

    @pytest.mark.parametrize(
        ("config_text", "env_name"),
        [
            ('[llm]\napi_key = ["{secret}"]\n', "CLM_LLM__API_KEY"),
            (
                '[recordings.auphonic]\napi_key = ["{secret}"]\n',
                "CLM_RECORDINGS__AUPHONIC__API_KEY",
            ),
            (
                '[recordings]\nobs_password = ["{secret}"]\n',
                "CLM_RECORDINGS__OBS_PASSWORD",
            ),
        ],
    )
    def test_show_config_validation_error_does_not_echo_malformed_secret(
        self,
        isolated_config_dirs,
        monkeypatch: pytest.MonkeyPatch,
        config_text: str,
        env_name: str,
    ):
        secret = "malformed-s12-secret"
        project_cfg = isolated_config_dirs["project"]
        project_cfg.parent.mkdir(parents=True, exist_ok=True)
        project_cfg.write_text(config_text.format(secret=secret), encoding="utf-8")
        monkeypatch.delenv(env_name, raising=False)

        result = CliRunner().invoke(cli, ["config", "show", "--json"])

        assert result.exit_code != 0
        assert secret not in result.output
        assert secret not in str(result.exception)

    def test_show_reports_pyproject_sidecar_layout(
        self, isolated_config_dirs, tmp_path, monkeypatch
    ):
        """A [tool.clm] sidecar-layout is visible with its source (A7, #802)."""
        (tmp_path / "pyproject.toml").write_text(
            '[tool.clm]\nsidecar-layout = "subdir"\n', encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CLM_SIDECAR_LAYOUT", raising=False)
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0, result.output
        assert "sidecar_layout: subdir  (from pyproject)" in result.output


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

    def test_locate_shows_llm_cache_directory(self, isolated_config_dirs):
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "locate"])
        assert result.exit_code == 0, result.output
        assert "LLM cache directory" in result.output
        assert "clm-llm.sqlite" in result.output
        # No DB exists in an isolated tmp project.
        section = result.output.split("LLM cache directory")[1]
        assert "Not found" in section

    def test_locate_reports_pyproject_cache_dir_source(self, isolated_config_dirs):
        # A project pyproject.toml [tool.clm] cache_dir is reported as the source.
        project_dir = isolated_config_dirs["project_dir"]
        (project_dir / "pyproject.toml").write_text(
            '[tool.clm]\ncache_dir = "my-llm-cache"\n', encoding="utf-8"
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "locate"])
        assert result.exit_code == 0, result.output
        section = result.output.split("LLM cache directory")[1]
        assert "pyproject.toml [tool.clm] cache_dir" in section
        assert "my-llm-cache" in section

    def test_locate_shows_sidecar_layout(self, isolated_config_dirs, monkeypatch):
        """The authoring sidecar layout is reported with its source (A7, #802)."""
        monkeypatch.delenv("CLM_SIDECAR_LAYOUT", raising=False)
        project_dir = isolated_config_dirs["project_dir"]
        (project_dir / "pyproject.toml").write_text(
            '[tool.clm]\nsidecar-layout = "sibling"\n', encoding="utf-8"
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "locate"])
        assert result.exit_code == 0, result.output
        section = result.output.split("Authoring sidecar layout")[1]
        assert "sibling" in section
        assert "pyproject.toml [tool.clm] sidecar-layout" in section

    def test_locate_shows_discovered_project_root_from_subdir(self, tmp_path, monkeypatch):
        # Issue #477: from a subdir, `config locate` reports the discovered root
        # (walked up) and notes it differs from cwd. (The cache-path anchoring
        # itself is covered deterministically in test_cache_dir_resolution.py.)
        monkeypatch.setattr(
            "clm.infrastructure.config.platformdirs.user_config_dir",
            lambda *a, **kw: str(tmp_path / "user"),
        )
        (tmp_path / "user").mkdir()
        repo = tmp_path / "repo"
        sub = repo / "slides" / "topic_031"
        sub.mkdir(parents=True)
        (repo / "pyproject.toml").write_text("[tool.clm]\n", encoding="utf-8")
        monkeypatch.chdir(sub)
        result = CliRunner().invoke(cli, ["config", "locate"])
        assert result.exit_code == 0, result.output
        assert "Project root" in result.output
        assert str(repo.resolve()) in result.output
        assert "differs from the current directory" in result.output


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

    def test_show_help_lists_secret_output_flags(self):
        result = CliRunner().invoke(cli, ["config", "show", "--help"])

        assert result.exit_code == 0
        assert "--json" in result.output
        assert "--reveal" in result.output

    def test_init_rejects_invalid_location(self, isolated_config_dirs):
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "init", "--location", "bogus"])
        assert result.exit_code != 0
        assert "Invalid value" in result.output or "bogus" in result.output
