"""``clm git`` exits non-zero when a git step fails (issue #1293).

``push``, ``commit``, ``reset`` and ``init`` used to print ``Error: ...`` for a
failed git step and still exit 0, so a script or agent checking the exit code
believed a rejected push had landed. Each command still works through the
remaining repos, but now exits 1 at the end when any repo failed.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from clm.cli.commands.git import git_group

SPEC = """<?xml version="1.0"?>
<course>
  <name><de>T</de><en>T</en></name>
  <prog-lang>python</prog-lang>
  <project-slug>ml-course</project-slug>
  <github>
    <repository-base>https://github.com/Org</repository-base>
  </github>
  <sections>
    <section>
      <name><de>S</de><en>S</en></name>
      <topics><topic>intro</topic></topics>
    </section>
  </sections>
  <output-targets>
    <output-target name="sol">
      <path>./output/sol</path>
      <kinds><kind>completed</kind></kinds>
    </output-target>
  </output-targets>
  <release-channels source-target="sol">
    <channel name="jan" path="./solutions/jan" ledger="release/jan.txt"/>
    <channel name="may" path="./solutions/may" ledger="release/may.txt"/>
  </release-channels>
</course>
"""


@pytest.fixture(autouse=True)
def _mock_config():
    """Clean GitConfig defaults so env vars don't perturb URL derivation."""
    from clm.infrastructure.config import GitConfig

    mock_config = MagicMock()
    mock_config.git = GitConfig()
    with patch("clm.cli.commands.git.get_config", return_value=mock_config):
        yield


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    for key, value in {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }.items():
        monkeypatch.setenv(key, value)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _spec(tmp_path: Path) -> Path:
    specs = tmp_path / "course-specs"
    specs.mkdir()
    spec_file = specs / "course.xml"
    spec_file.write_text(SPEC, encoding="utf-8")
    return spec_file


def _channel_repo(tmp_path: Path, name: str, origin: Path | None) -> Path:
    """A channel working tree with one commit and an optional ``origin``."""
    repo = tmp_path / "solutions" / name
    repo.mkdir(parents=True)
    (repo / "README.md").write_text(name, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "checkout", "-q", "-b", "master")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    if origin is not None:
        _git(repo, "remote", "add", "origin", str(origin))
    return repo


def _bare(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "--bare")
    return path


def _has_branch(bare: Path, branch: str) -> bool:
    out = _git(bare, "branch", "--list", branch).stdout
    return bool(out.strip())


# Real `git` subprocesses, like the other end-to-end git CLI tests.
pytestmark = pytest.mark.integration


class TestPush:
    def test_a_failed_push_exits_1_and_the_other_repos_still_push(self, tmp_path):
        spec_file = _spec(tmp_path)
        # jan's origin does not exist, so its push fails; may's is a real bare repo.
        _channel_repo(tmp_path, "jan", tmp_path / "missing-remote.git")
        may_remote = _bare(tmp_path / "remotes" / "may.git")
        _channel_repo(tmp_path, "may", may_remote)

        result = CliRunner().invoke(git_group, ["push", str(spec_file), "--all-channels"])

        assert result.exit_code == 1, result.output
        assert "Error:" in result.output
        assert _has_branch(may_remote, "master"), "a failed repo must not stop the others"

    def test_all_pushes_succeeding_still_exits_0(self, tmp_path):
        spec_file = _spec(tmp_path)
        remote = _bare(tmp_path / "remotes" / "jan.git")
        _channel_repo(tmp_path, "jan", remote)

        result = CliRunner().invoke(git_group, ["push", str(spec_file), "--channel", "jan"])

        assert result.exit_code == 0, result.output
        assert _has_branch(remote, "master")


class TestCommit:
    def test_a_failed_commit_exits_1(self, tmp_path):
        spec_file = _spec(tmp_path)
        # A repo without any commit has nothing to amend: `git commit --amend` fails.
        repo = tmp_path / "solutions" / "jan"
        repo.mkdir(parents=True)
        _git(repo, "init", "-q")

        result = CliRunner().invoke(
            git_group, ["commit", str(spec_file), "--channel", "jan", "--amend", "-m", "x"]
        )

        assert result.exit_code == 1, result.output
        assert "Error:" in result.output


class TestReset:
    def test_a_failed_fetch_exits_1_without_next_steps(self, tmp_path):
        spec_file = _spec(tmp_path)
        _channel_repo(tmp_path, "jan", tmp_path / "missing-remote.git")

        result = CliRunner().invoke(git_group, ["reset", str(spec_file), "--channel", "jan"])

        assert result.exit_code == 1, result.output
        assert "Error fetching" in result.output
        assert "Next steps" not in result.output

    def test_a_successful_reset_exits_0(self, tmp_path):
        spec_file = _spec(tmp_path)
        remote = _bare(tmp_path / "remotes" / "jan.git")
        repo = _channel_repo(tmp_path, "jan", remote)
        _git(repo, "push", "-q", "-u", "origin", "master")

        result = CliRunner().invoke(git_group, ["reset", str(spec_file), "--channel", "jan"])

        assert result.exit_code == 0, result.output
        assert "Next steps" in result.output


class TestInit:
    def test_a_failed_repository_init_exits_1(self, tmp_path):
        spec_file = _spec(tmp_path)
        (tmp_path / "solutions" / "jan").mkdir(parents=True)

        with (
            patch("clm.cli.commands.git.remote_exists", return_value=False),
            patch("clm.cli.commands.git.init_repo_fresh", return_value=False),
        ):
            result = CliRunner().invoke(git_group, ["init", str(spec_file), "--channel", "jan"])

        assert result.exit_code == 1, result.output

    def test_a_failed_remote_add_exits_1(self, tmp_path):
        spec_file = _spec(tmp_path)
        _channel_repo(tmp_path, "jan", origin=None)
        real_run_git = __import__("clm.cli.commands.git", fromlist=["run_git"]).run_git

        def failing_remote_add(path, *args):
            if args[:2] == ("remote", "add"):
                return subprocess.CompletedProcess(args, 1, "", "boom")
            return real_run_git(path, *args)

        with (
            patch("clm.cli.commands.git.remote_exists", return_value=True),
            patch("clm.cli.commands.git.run_git", side_effect=failing_remote_add),
        ):
            result = CliRunner().invoke(git_group, ["init", str(spec_file), "--channel", "jan"])

        assert result.exit_code == 1, result.output
        assert "Failed to add remote" in result.output
