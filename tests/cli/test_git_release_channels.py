"""Tests for ``clm git --channel`` release-channel support (issue #208, step 3b).

Covers the channel remote-URL derivation, channel repo discovery, the repo-set
selection guards, and an end-to-end check that the private provenance manifest
(``.clm-manifest.json``) is never committed while the per-cohort frozen manifest
(``.clm-released.json``) is.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from clm.cli.commands.git_ops import (
    OutputRepo,
    _select_repos,
    find_release_channel_repos,
    git_group,
)
from clm.core.course_spec import GitHubSpec

# ---------------------------------------------------------------------------
# Spec fixtures
# ---------------------------------------------------------------------------

SPEC_WITH_CHANNELS = """<?xml version="1.0"?>
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
    <output-target name="solutions-source">
      <path>./output/sol</path>
      <kinds><kind>completed</kind></kinds>
    </output-target>
  </output-targets>
  <release-channels source-target="solutions-source">
    <remote-path>cohorts</remote-path>
    <channel name="jan" path="./solutions/jan" ledger="release/jan.txt"/>
    <channel name="may" path="./solutions/may" ledger="release/may.txt">
      <remote-path>special</remote-path>
    </channel>
  </release-channels>
</course>
"""

SPEC_NO_CHANNELS = """<?xml version="1.0"?>
<course>
  <name><de>T</de><en>T</en></name>
  <prog-lang>python</prog-lang>
  <project-slug>ml-course</project-slug>
  <sections>
    <section>
      <name><de>S</de><en>S</en></name>
      <topics><topic>intro</topic></topics>
    </section>
  </sections>
</course>
"""


def _write_spec(tmp_path: Path, body: str) -> Path:
    """Write a spec under a ``course-specs/`` subdir (its grandparent is the root)."""
    specs_dir = tmp_path / "course-specs"
    specs_dir.mkdir(exist_ok=True)
    spec_file = specs_dir / "course.xml"
    spec_file.write_text(body, encoding="utf-8")
    return spec_file


@pytest.fixture(autouse=True)
def _mock_config():
    """Return clean GitConfig defaults so env vars don't perturb URL derivation."""
    from clm.infrastructure.config import GitConfig

    mock_config = MagicMock()
    mock_config.git = GitConfig()
    with patch("clm.cli.commands.git_ops.get_config", return_value=mock_config):
        yield


@pytest.fixture
def git_identity(monkeypatch):
    """Provide a deterministic git identity so real commits succeed hermetically."""
    for key, value in {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }.items():
        monkeypatch.setenv(key, value)


# ---------------------------------------------------------------------------
# GitHubSpec.derive_channel_remote_url
# ---------------------------------------------------------------------------


class TestDeriveChannelRemoteUrl:
    def test_not_configured_returns_none(self):
        assert GitHubSpec().derive_channel_remote_url("jan") is None

    def test_default_no_remote_path(self):
        spec = GitHubSpec(project_slug="ml-course", repository_base="https://github.com/Org")
        assert spec.derive_channel_remote_url("jan") == "https://github.com/Org/ml-course-jan"

    def test_with_remote_path(self):
        spec = GitHubSpec(project_slug="ml-course", repository_base="https://github.com/Org")
        url = spec.derive_channel_remote_url("cohort-jan", remote_path="cohorts")
        assert url == "https://github.com/Org/cohorts/ml-course-cohort-jan"

    def test_no_double_dash_unlike_language_path(self):
        """The channel name — not an empty language — disambiguates the repo."""
        spec = GitHubSpec(project_slug="ml-course", repository_base="https://github.com/Org")
        assert "--" not in spec.derive_channel_remote_url("jan")

    def test_project_slug_override(self):
        spec = GitHubSpec(repository_base="https://github.com/Org")
        url = spec.derive_channel_remote_url("jan", project_slug="other")
        assert url == "https://github.com/Org/other-jan"

    def test_custom_template(self):
        spec = GitHubSpec(project_slug="ml-course", repository_base="https://github.com/Org")
        url = spec.derive_channel_remote_url("jan", remote_template="git@host:{slug}/{repo}.git")
        assert url == "git@host:ml-course/ml-course-jan.git"


# ---------------------------------------------------------------------------
# find_release_channel_repos
# ---------------------------------------------------------------------------


class TestFindReleaseChannelRepos:
    def test_no_channels_block_returns_empty(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_NO_CHANNELS)
        assert find_release_channel_repos(spec_file) == []

    def test_resolves_paths_under_course_root_not_spec_parent(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        repos = find_release_channel_repos(spec_file)
        by_name = {r.target_name: r for r in repos}
        assert by_name["jan"].path == tmp_path / "solutions" / "jan"
        assert by_name["may"].path == tmp_path / "solutions" / "may"

    def test_repo_shape_is_channel_and_language_free(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        repo = find_release_channel_repos(spec_file, "jan")[0]
        assert repo.source == "channel"
        assert repo.language == ""
        assert repo.display_name == "jan"

    def test_remote_urls_inherit_and_override_remote_path(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        by_name = {r.target_name: r.remote_url for r in find_release_channel_repos(spec_file)}
        assert by_name["jan"] == "https://github.com/Org/cohorts/ml-course-jan"
        assert by_name["may"] == "https://github.com/Org/special/ml-course-may"

    def test_channel_filter(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        repos = find_release_channel_repos(spec_file, "may")
        assert [r.target_name for r in repos] == ["may"]

    def test_absolute_channel_path_is_not_rerooted(self, tmp_path: Path):
        abs_cohort = (tmp_path / "abs-cohort").resolve()
        body = SPEC_WITH_CHANNELS.replace(
            '<channel name="jan" path="./solutions/jan" ledger="release/jan.txt"/>',
            f'<channel name="jan" path="{abs_cohort.as_posix()}" ledger="release/jan.txt"/>',
        )
        spec_file = _write_spec(tmp_path, body)
        repo = find_release_channel_repos(spec_file, "jan")[0]
        assert repo.path == abs_cohort  # preserved, NOT joined under course_root


# ---------------------------------------------------------------------------
# _select_repos guards
# ---------------------------------------------------------------------------


class TestSelectReposGuards:
    def test_target_and_channel_are_mutually_exclusive(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        with pytest.raises(click.UsageError, match="cannot be combined"):
            _select_repos(spec_file, target="x", channel="jan", all_channels=False)

    def test_unknown_channel_lists_available(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        with pytest.raises(click.ClickException, match="Unknown channel 'nope'"):
            _select_repos(spec_file, target=None, channel="nope", all_channels=False)

    def test_no_channels_block_is_a_clear_error(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_NO_CHANNELS)
        with pytest.raises(click.ClickException, match="no <release-channels>"):
            _select_repos(spec_file, target=None, channel="jan", all_channels=False)

    def test_all_channels_returns_every_channel(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        repos = _select_repos(spec_file, target=None, channel=None, all_channels=True)
        assert {r.target_name for r in repos} == {"jan", "may"}

    def test_default_mode_uses_output_targets(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        repos = _select_repos(spec_file, target=None, channel=None, all_channels=False)
        # The single completed output target, one repo per language.
        assert {r.source for r in repos} == {"output"}
        assert {r.target_name for r in repos} == {"solutions-source"}


# ---------------------------------------------------------------------------
# OutputRepo.source / display_name
# ---------------------------------------------------------------------------


class TestOutputRepoSource:
    def test_default_source_is_output(self, tmp_path: Path):
        assert OutputRepo(tmp_path, "students", "de").source == "output"

    def test_channel_display_name_drops_empty_language(self, tmp_path: Path):
        assert OutputRepo(tmp_path, "jan", "", source="channel").display_name == "jan"

    def test_output_display_name_keeps_language(self, tmp_path: Path):
        assert OutputRepo(tmp_path, "students", "de").display_name == "students/de"


# ---------------------------------------------------------------------------
# End-to-end: real git, manifest exclusion
# ---------------------------------------------------------------------------


def _ls_files(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    return {line.strip().replace("\\", "/") for line in out.stdout.splitlines() if line.strip()}


def _populate_channel(channel_dir: Path) -> None:
    """Lay down a realistic synced cohort working tree."""
    channel_dir.mkdir(parents=True, exist_ok=True)
    (channel_dir / "Sec").mkdir(exist_ok=True)
    (channel_dir / "Sec" / "01 Intro.ipynb").write_text("notebook", encoding="utf-8")
    # Private build manifest that must NEVER be committed.
    (channel_dir / ".clm-manifest.json").write_text("{}", encoding="utf-8")
    # Frozen manifest that MUST travel with the cohort repo.
    (channel_dir / ".clm-released.json").write_text("{}", encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    """Run a git command in ``repo`` (inherits the env-based identity)."""
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


def _manual_channel_repo(channel_dir: Path, *, track_manifest: bool) -> None:
    """Init a channel repo WITHOUT ``clm`` — so its ``.gitignore`` (if any) has no
    manifest entry. With ``track_manifest`` the manifest is committed the old
    (bare ``git add -A``) way, reproducing a repo built before the exclusion."""
    channel_dir.mkdir(parents=True, exist_ok=True)
    (channel_dir / "Sec").mkdir(exist_ok=True)
    (channel_dir / "Sec" / "01 Intro.ipynb").write_text("notebook", encoding="utf-8")
    (channel_dir / ".clm-released.json").write_text("{}", encoding="utf-8")
    _git(channel_dir, "init", "-q")
    _git(channel_dir, "checkout", "-q", "-b", "master")
    if track_manifest:
        (channel_dir / ".clm-manifest.json").write_text("OLD", encoding="utf-8")
    _git(channel_dir, "add", "-A")
    _git(channel_dir, "commit", "-qm", "old-style init")


def _init_bare_remote(path: Path) -> Path:
    """Create an empty bare repo to act as a push target, returning its path."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "--bare")
    return path


class TestChannelGitEndToEnd:
    @pytest.fixture(autouse=True)
    def _no_network(self):
        """Force the deterministic local-only init path (no ``git ls-remote``).

        The e2e specs carry a real ``repository_base``, so without this the init
        path would make live ``git ls-remote https://github.com/...`` calls —
        slow and a CI flake vector. Mirrors tests/cli/test_git_ops.py.
        """
        with (
            patch("clm.cli.commands.git_ops.remote_exists", return_value=False),
            patch("clm.cli.commands.git_ops.remote_has_commits", return_value=False),
        ):
            yield

    def test_init_channel_commits_content_but_not_private_manifest(
        self, tmp_path: Path, git_identity
    ):
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        _populate_channel(tmp_path / "solutions" / "jan")

        result = CliRunner().invoke(git_group, ["init", str(spec_file), "--channel", "jan"])
        assert result.exit_code == 0, result.output

        channel = tmp_path / "solutions" / "jan"
        tracked = _ls_files(channel)
        assert "Sec/01 Intro.ipynb" in tracked
        assert ".clm-released.json" in tracked
        assert ".clm-manifest.json" not in tracked
        # The gitignore template entry is the human-`git add` / status-noise
        # guard, independent of the staging exclusion — pin it explicitly.
        assert ".clm-manifest.json" in (channel / ".gitignore").read_text(encoding="utf-8")

    def test_commit_channel_excludes_manifest_after_resync(self, tmp_path: Path, git_identity):
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        channel = tmp_path / "solutions" / "jan"
        _populate_channel(channel)
        runner = CliRunner()
        runner.invoke(git_group, ["init", str(spec_file), "--channel", "jan"])

        # A later release rewrites the manifest and adds a new topic file.
        (channel / ".clm-manifest.json").write_text('{"v": 2}', encoding="utf-8")
        (channel / "Sec" / "02 More.ipynb").write_text("more", encoding="utf-8")

        result = runner.invoke(
            git_group, ["commit", str(spec_file), "--channel", "jan", "-m", "Release more"]
        )
        assert result.exit_code == 0, result.output

        tracked = _ls_files(channel)
        assert "Sec/02 More.ipynb" in tracked
        assert ".clm-manifest.json" not in tracked

    def test_status_all_channels_reports_each_cohort(self, tmp_path: Path, git_identity):
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        _populate_channel(tmp_path / "solutions" / "jan")
        _populate_channel(tmp_path / "solutions" / "may")
        runner = CliRunner()
        runner.invoke(git_group, ["init", str(spec_file), "--all-channels"])

        result = runner.invoke(git_group, ["status", str(spec_file), "--all-channels"])
        assert result.exit_code == 0, result.output
        assert "[jan]" in result.output
        assert "[may]" in result.output

    def test_target_and_channel_flags_conflict_at_cli(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        result = CliRunner().invoke(
            git_group, ["status", str(spec_file), "--target", "x", "--channel", "jan"]
        )
        assert result.exit_code != 0
        assert "cannot be combined" in result.output

    def test_nested_manifest_is_also_excluded(self, tmp_path: Path, git_identity):
        """A manifest at any depth — not just the root — must never be tracked."""
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        channel = tmp_path / "solutions" / "jan"
        _populate_channel(channel)
        (channel / "Sec" / ".clm-manifest.json").write_text("{}", encoding="utf-8")

        result = CliRunner().invoke(git_group, ["init", str(spec_file), "--channel", "jan"])
        assert result.exit_code == 0, result.output

        tracked = _ls_files(channel)
        assert ".clm-manifest.json" not in tracked
        assert "Sec/.clm-manifest.json" not in tracked
        assert ".clm-released.json" in tracked

    def test_commit_purges_a_pre_tracked_manifest(self, tmp_path: Path, git_identity):
        """A manifest committed before the exclusion existed is self-healed away."""
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        channel = tmp_path / "solutions" / "jan"
        _manual_channel_repo(channel, track_manifest=True)
        assert ".clm-manifest.json" in _ls_files(channel)  # precondition: tracked

        result = CliRunner().invoke(
            git_group, ["commit", str(spec_file), "--channel", "jan", "-m", "Purge manifest"]
        )
        assert result.exit_code == 0, result.output

        tracked = _ls_files(channel)
        assert ".clm-manifest.json" not in tracked  # purged from the index
        assert ".clm-released.json" in tracked
        assert "Sec/01 Intro.ipynb" in tracked

    def test_commit_with_only_manifest_change_is_a_clean_noop(self, tmp_path: Path, git_identity):
        """An untracked, non-ignored manifest as the sole change must not error."""
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        channel = tmp_path / "solutions" / "jan"
        # Repo whose committed .gitignore does NOT list the manifest.
        _manual_channel_repo(channel, track_manifest=False)
        (channel / ".clm-manifest.json").write_text("{}", encoding="utf-8")  # sole change

        result = CliRunner().invoke(
            git_group, ["commit", str(spec_file), "--channel", "jan", "-m", "noop"]
        )
        assert result.exit_code == 0, result.output
        assert "Nothing to commit" in result.output
        assert "Error" not in result.output

    def test_sync_channel_pushes_content_without_manifest(self, tmp_path: Path, git_identity):
        """sync --channel commits and pushes; the manifest never reaches the remote."""
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        channel = tmp_path / "solutions" / "jan"
        _populate_channel(channel)
        runner = CliRunner()
        runner.invoke(git_group, ["init", str(spec_file), "--channel", "jan"])

        bare = _init_bare_remote(tmp_path / "remotes" / "jan.git")
        _git(channel, "remote", "add", "origin", str(bare))

        # A later release: new topic file plus a manifest rewrite.
        (channel / "Sec" / "02 More.ipynb").write_text("more", encoding="utf-8")
        (channel / ".clm-manifest.json").write_text('{"v": 2}', encoding="utf-8")

        result = runner.invoke(
            git_group, ["sync", str(spec_file), "--channel", "jan", "-m", "Release more"]
        )
        assert result.exit_code == 0, result.output

        clone = tmp_path / "clone"
        _git(tmp_path, "clone", "-q", str(bare), str(clone))
        pushed = _ls_files(clone)
        assert "Sec/01 Intro.ipynb" in pushed
        assert "Sec/02 More.ipynb" in pushed
        assert ".clm-released.json" in pushed
        assert ".clm-manifest.json" not in pushed

    def test_reset_channel_skips_when_no_remote(self, tmp_path: Path, git_identity):
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        _populate_channel(tmp_path / "solutions" / "jan")
        runner = CliRunner()
        runner.invoke(git_group, ["init", str(spec_file), "--channel", "jan"])

        result = runner.invoke(git_group, ["reset", str(spec_file), "--channel", "jan"])
        assert result.exit_code == 0, result.output
        assert "Skipped: No remote configured" in result.output
