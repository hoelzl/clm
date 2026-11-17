"""GitLab default-branch reconcile after ``clm git push/sync --channel`` (#1284).

``clm release provision`` runs before the first push, so it can only warn
that the branch is not on the remote yet; the push that creates the branch
now points the project's ``default_branch`` at it. Gated on ``<share-with>``,
on the push creating the remote branch, and on a configured token. Problems
are warnings; the push's exit code never changes.

The channel repo's *fetch* URL is a GitLab URL (what the reconcile parses);
its *push* URL is a local bare repo, so the push itself really runs. All
GitLab calls are mocked.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from clm.cli.commands.git import git_group
from clm.infrastructure import gitlab_api
from clm.infrastructure.gitlab_api import GitLabApiError

BASE = "https://gitlab.example.com"
PROJECT = "ca/ml-2026-04-solutions"

SPEC = """<?xml version="1.0"?>
<course>
  <name><de>T</de><en>T</en></name>
  <prog-lang>python</prog-lang>
  <project-slug>ml</project-slug>
  <github>
    <repository-base>https://gitlab.example.com/ca</repository-base>
  </github>
  <sections>
    <section>
      <name><de>S</de><en>S</en></name>
      <topics><topic>intro</topic></topics>
    </section>
  </sections>
  <output-targets>
    <output-target name="completed"><path>output/completed</path></output-target>
  </output-targets>
  <release-channels name="solutions" source-target="completed">
    {share_with}
    <channel name="2026-04" path="release/2026-04" ledger="release/2026-04.txt"/>
  </release-channels>
</course>
"""
SHARE = '<share-with access="maintainer">trainers</share-with>'
RECONCILE = "clm.infrastructure.gitlab_api.reconcile_default_branch"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """Clean git config, and no ``.env`` lookup up the tmp tree."""
    from clm.infrastructure.config import GitConfig

    mock_config = MagicMock()
    mock_config.git = GitConfig()
    monkeypatch.setenv("CLM_GITLAB_TOKEN", "tok")
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    with (
        patch("clm.cli.commands.git.get_config", return_value=mock_config),
        patch("clm.cli.env_loading.load_env_files"),
        patch.object(gitlab_api.httpx, "request", side_effect=AssertionError("network")),
    ):
        yield


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(cwd),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def _course(tmp_path: Path, *, share: bool = True, dirty: bool = False) -> Path:
    specs = tmp_path / "course-specs"
    specs.mkdir()
    spec_file = specs / "course.xml"
    spec_file.write_text(SPEC.format(share_with=SHARE if share else ""), encoding="utf-8")
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    dest = tmp_path / "release" / "2026-04"
    dest.mkdir(parents=True)
    _git(dest, "init", "-q", "-b", "master")
    _git(dest, "remote", "add", "origin", f"{BASE}/{PROJECT}.git")
    _git(dest, "remote", "set-url", "--push", "origin", str(bare))
    (dest / "README.md").write_text("x\n", encoding="utf-8")
    _git(dest, "add", "README.md")
    _git(dest, "commit", "-q", "-m", "init")
    if dirty:
        (dest / "NEW.md").write_text("y\n", encoding="utf-8")
    return spec_file


def _push(spec_file: Path, *extra: str):
    return CliRunner().invoke(git_group, ["push", str(spec_file), "--channel", "2026-04", *extra])


class TestPush:
    def test_first_push_reconciles_the_default_branch(self, tmp_path):
        spec_file = _course(tmp_path)
        with patch(RECONCILE, return_value=("updated", "main")) as reconcile:
            result = _push(spec_file)
        assert result.exit_code == 0, result.output
        reconcile.assert_called_once_with(BASE, PROJECT, "master", "tok")
        assert f"default branch of {PROJECT} set to 'master' (was 'main')" in result.output

    def test_routine_push_makes_no_api_call(self, tmp_path):
        spec_file = _course(tmp_path)
        with patch(RECONCILE, return_value=("updated", "main")):
            assert _push(spec_file).exit_code == 0
        # The first push created origin/master; the next one must not ask GitLab.
        with patch(RECONCILE) as reconcile:
            result = _push(spec_file)
        assert result.exit_code == 0, result.output
        reconcile.assert_not_called()

    def test_without_share_with_no_api_call(self, tmp_path):
        spec_file = _course(tmp_path, share=False)
        with patch(RECONCILE) as reconcile:
            result = _push(spec_file)
        assert result.exit_code == 0, result.output
        reconcile.assert_not_called()

    def test_without_token_no_call_and_no_output(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLM_GITLAB_TOKEN")
        spec_file = _course(tmp_path)
        with patch(RECONCILE) as reconcile:
            result = _push(spec_file)
        assert result.exit_code == 0, result.output
        reconcile.assert_not_called()
        assert "default branch" not in result.output.lower()

    def test_refusal_warns_but_the_push_still_succeeds(self, tmp_path):
        spec_file = _course(tmp_path)
        with patch(RECONCILE, side_effect=GitLabApiError("HTTP 403: forbidden")):
            result = _push(spec_file)
        assert result.exit_code == 0, result.output
        assert "WARNING: default branch not reconciled" in result.output
        assert gitlab_api.DEFAULT_BRANCH_SETTING in result.output

    def test_not_pushed_status_warns(self, tmp_path):
        spec_file = _course(tmp_path)
        with patch(RECONCILE, return_value=("not-pushed", "main")):
            result = _push(spec_file)
        assert result.exit_code == 0, result.output
        assert "clm release provision" in result.output

    def test_unchanged_is_silent(self, tmp_path):
        spec_file = _course(tmp_path)
        with patch(RECONCILE, return_value=("unchanged", "master")):
            result = _push(spec_file)
        assert result.exit_code == 0, result.output
        assert "default branch" not in result.output.lower()

    def test_dry_run_reports_and_calls_nothing(self, tmp_path):
        spec_file = _course(tmp_path)
        with patch(RECONCILE) as reconcile:
            result = _push(spec_file, "--dry-run")
        assert result.exit_code == 0, result.output
        reconcile.assert_not_called()
        assert f"Would point the GitLab default branch of {PROJECT} at 'master'" in (result.output)


class TestSync:
    def test_git_sync_first_push_reconciles(self, tmp_path):
        """``clm git sync`` pushes through the shared ``commit_and_push_repo``,
        which ``clm release sync --push`` uses too."""
        spec_file = _course(tmp_path, dirty=True)
        with patch(RECONCILE, return_value=("updated", "main")) as reconcile:
            result = CliRunner().invoke(
                git_group, ["sync", str(spec_file), "--channel", "2026-04", "-m", "release"]
            )
        assert result.exit_code == 0, result.output
        reconcile.assert_called_once_with(BASE, PROJECT, "master", "tok")
