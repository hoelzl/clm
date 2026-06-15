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

from clm.cli.commands.git import (
    OutputRepo,
    _select_repos,
    find_output_repos,
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
    with patch("clm.cli.commands.git.get_config", return_value=mock_config):
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


SPEC_TWO_STREAMS = """<?xml version="1.0"?>
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
    <output-target name="shared"><path>./output/shared</path></output-target>
    <output-target name="completed"><path>./output/completed</path></output-target>
  </output-targets>
  <release-channels name="materials" source-target="shared">
    <channel name="2026-04" path="./release/materials/2026-04" ledger="release/materials-2026-04.txt"/>
  </release-channels>
  <release-channels name="solutions" source-target="completed">
    <channel name="2026-04" path="./release/solutions/2026-04" ledger="release/solutions-2026-04.txt"/>
    <channel name="2026-10" path="./release/solutions/2026-10" ledger="release/solutions-2026-10.txt"/>
  </release-channels>
</course>
"""


class TestMultiStreamChannelRepos:
    """Several <release-channels> blocks — one per release stream (issue #291)."""

    def test_all_channels_enumerates_every_stream(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_TWO_STREAMS)
        repos = find_release_channel_repos(spec_file)
        assert [r.target_name for r in repos] == [
            "materials/2026-04",
            "solutions/2026-04",
            "solutions/2026-10",
        ]

    def test_qualified_filter_selects_one_stream_channel(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_TWO_STREAMS)
        repos = find_release_channel_repos(spec_file, "solutions/2026-04")
        assert [r.path for r in repos] == [tmp_path / "release" / "solutions" / "2026-04"]

    def test_bare_filter_resolves_when_unique(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_TWO_STREAMS)
        repos = find_release_channel_repos(spec_file, "2026-10")
        assert [r.target_name for r in repos] == ["solutions/2026-10"]

    def test_ambiguous_bare_filter_is_a_clear_cli_error(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_TWO_STREAMS)
        with pytest.raises(click.ClickException, match="several streams"):
            _select_repos(spec_file, target=None, channel="2026-04", all_channels=False)

    def test_remote_urls_carry_the_stream_suffix(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_TWO_STREAMS)
        by_name = {r.target_name: r.remote_url for r in find_release_channel_repos(spec_file)}
        assert by_name["materials/2026-04"] == "https://github.com/Org/ml-course-2026-04-materials"
        assert by_name["solutions/2026-04"] == "https://github.com/Org/ml-course-2026-04-solutions"


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

    def test_default_mode_uses_output_targets_minus_release_sources(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        repos = _select_repos(spec_file, target=None, channel=None, all_channels=False)
        # The only output target is the release source-target, which is a
        # private build input — default enumeration skips it (issue #292).
        assert repos == []

    def test_all_unions_output_targets_and_channels(self, tmp_path: Path):
        """``--all`` returns every distributed output target *plus* every channel."""
        spec_file = _write_spec(tmp_path, SPEC_DISTRIBUTE)
        repos = _select_repos(
            spec_file, target=None, channel=None, all_channels=False, all_repos=True
        )
        # trainer + legacy are the distributed targets (completed is the release
        # source, auto-excluded); jan is the sole release channel.
        assert {r.target_name for r in repos} == {"trainer", "legacy", "jan"}

    def test_all_degrades_to_output_targets_without_channels(self, tmp_path: Path):
        """A course with no <release-channels> falls back to the plain target set."""
        spec_file = _write_spec(tmp_path, SPEC_NO_CHANNELS)
        repos = _select_repos(
            spec_file, target=None, channel=None, all_channels=False, all_repos=True
        )
        # Default output structure: public × de/en (speaker is opt-in), no
        # channels to add.
        assert {r.target_name for r in repos} == {"public"}
        assert all(r.source == "output" for r in repos)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"target": "trainer"},
            {"channel": "jan"},
            {"all_channels": True},
        ],
    )
    def test_all_is_mutually_exclusive(self, tmp_path: Path, kwargs):
        spec_file = _write_spec(tmp_path, SPEC_DISTRIBUTE)
        base = {"target": None, "channel": None, "all_channels": False}
        with pytest.raises(click.UsageError, match="--all cannot be combined"):
            _select_repos(spec_file, **{**base, **kwargs}, all_repos=True)

    def test_all_channels_visits_a_shared_destination_once(self, tmp_path: Path):
        """Channels of different streams releasing into one working tree are
        one repo to ``clm git`` (issue #325)."""
        shared = SPEC_TWO_STREAMS.replace(
            "./release/solutions/2026-04", "./release/materials/2026-04"
        )
        spec_file = _write_spec(tmp_path, shared)
        repos = _select_repos(spec_file, target=None, channel=None, all_channels=True)
        assert [r.target_name for r in repos] == ["materials/2026-04", "solutions/2026-10"]
        merged = repos[0]
        assert merged.shared_refs == ["solutions/2026-04"]
        assert merged.display_name == "materials/2026-04 (+ solutions/2026-04)"
        # The first stream's URL derivation wins for the shared repo.
        assert merged.remote_url == "https://github.com/Org/ml-course-2026-04-materials"


# ---------------------------------------------------------------------------
# distribute="false" / release-source auto-exclusion (issue #292)
# ---------------------------------------------------------------------------

SPEC_DISTRIBUTE = """<?xml version="1.0"?>
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
  <output-targets>
    <output-target name="trainer"><path>./output/trainer</path></output-target>
    <output-target name="shared" distribute="false"><path>./output/shared</path></output-target>
    <output-target name="completed"><path>./output/completed</path></output-target>
    <output-target name="legacy" distribute="true"><path>./output/legacy</path></output-target>
  </output-targets>
  <release-channels source-target="completed">
    <channel name="jan" path="./solutions/jan" ledger="release/jan.txt"/>
  </release-channels>
</course>
"""


class TestDistributeFlag:
    def test_default_enumeration_skips_explicit_and_auto_excluded_targets(self, tmp_path: Path):
        """`shared` opts out via distribute="false"; `completed` is auto-excluded
        as the release source-target; `legacy` shows distribute="true" overrides
        the auto-exclusion (here it is not a source-target, so it is moot but
        harmless)."""
        spec_file = _write_spec(tmp_path, SPEC_DISTRIBUTE)
        repos = find_output_repos(spec_file)
        assert {r.target_name for r in repos} == {"trainer", "legacy"}

    def test_explicit_target_request_wins_over_the_skip(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_DISTRIBUTE)
        assert {r.target_name for r in find_output_repos(spec_file, "shared")} == {"shared"}
        assert {r.target_name for r in find_output_repos(spec_file, "completed")} == {"completed"}

    def test_distribute_true_keeps_a_release_source_distributed(self, tmp_path: Path):
        body = SPEC_DISTRIBUTE.replace(
            '<output-target name="completed">',
            '<output-target name="completed" distribute="true">',
        )
        spec_file = _write_spec(tmp_path, body)
        repos = find_output_repos(spec_file)
        assert {r.target_name for r in repos} == {"trainer", "completed", "legacy"}

    def test_invalid_distribute_value_is_a_validation_error(self, tmp_path: Path):
        from clm.core.course_spec import CourseSpec

        body = SPEC_DISTRIBUTE.replace('distribute="false"', 'distribute="flase"')
        spec_file = _write_spec(tmp_path, body)
        errors = CourseSpec.from_file(spec_file).validate()
        assert any("Invalid distribute value" in e for e in errors)


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


# Real-`git` end-to-end (init/commit/push); ~3-6s/test. Runs in CI's integration
# step, excluded from the per-commit fast suite. See docs/developer-guide/testing.md.
@pytest.mark.integration
class TestChannelGitEndToEnd:
    @pytest.fixture(autouse=True)
    def _no_network(self):
        """Force the deterministic local-only init path (no ``git ls-remote``).

        The e2e specs carry a real ``repository_base``, so without this the init
        path would make live ``git ls-remote https://github.com/...`` calls —
        slow and a CI flake vector. Mirrors tests/cli/test_git_ops.py.
        """
        with (
            patch("clm.cli.commands.git.remote_exists", return_value=False),
            patch("clm.cli.commands.git.remote_has_commits", return_value=False),
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

    def test_all_flag_reports_targets_and_channels_together(self, tmp_path: Path, git_identity):
        """``status --all`` visits distributed output targets *and* every channel."""
        spec_file = _write_spec(tmp_path, SPEC_DISTRIBUTE)
        for rel in ("output/trainer", "output/legacy", "solutions/jan"):
            (tmp_path / rel).mkdir(parents=True, exist_ok=True)
        runner = CliRunner()
        runner.invoke(git_group, ["init", str(spec_file), "--all"])

        result = runner.invoke(git_group, ["status", str(spec_file), "--all"])
        assert result.exit_code == 0, result.output
        assert "[trainer/de]" in result.output
        assert "[legacy/de]" in result.output
        assert "[jan]" in result.output

    def test_all_flag_conflicts_with_channel_at_cli(self, tmp_path: Path):
        spec_file = _write_spec(tmp_path, SPEC_DISTRIBUTE)
        result = CliRunner().invoke(
            git_group, ["status", str(spec_file), "--all", "--all-channels"]
        )
        assert result.exit_code != 0
        assert "--all cannot be combined" in result.output

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

    def test_sync_channel_aborts_when_remote_is_ahead(self, tmp_path: Path, git_identity):
        """The remote-ahead guard (the one error path the commit_and_push_repo
        refactor reordered) still fires: a non-force sync against an ahead remote
        prints the recovery recipe and exits non-zero."""
        spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)
        channel = tmp_path / "solutions" / "jan"
        _populate_channel(channel)
        runner = CliRunner()
        runner.invoke(git_group, ["init", str(spec_file), "--channel", "jan"])

        bare = _init_bare_remote(tmp_path / "remotes" / "jan.git")
        _git(channel, "remote", "add", "origin", str(bare))
        # First sync establishes origin/master.
        first = runner.invoke(
            git_group, ["sync", str(spec_file), "--channel", "jan", "-m", "first"]
        )
        assert first.exit_code == 0, first.output

        # A second clone advances the remote so the cohort is now behind.
        clone = tmp_path / "clone"
        _git(tmp_path, "clone", "-q", str(bare), str(clone))
        (clone / "upstream.txt").write_text("ahead", encoding="utf-8")
        _git(clone, "add", "-A")
        _git(clone, "commit", "-qm", "advance remote")
        _git(clone, "push", "-q", "origin", "master")

        # The cohort has a local change but the remote moved — sync must refuse.
        (channel / "Sec" / "02 More.ipynb").write_text("more", encoding="utf-8")
        result = runner.invoke(
            git_group, ["sync", str(spec_file), "--channel", "jan", "-m", "second"]
        )
        assert result.exit_code != 0
        assert "ahead" in result.output
        assert "To resolve:" in result.output
        assert "clm git reset" in result.output
        # The blocked sync did not commit the local change.
        assert "Sec/02 More.ipynb" not in _ls_files(channel)
