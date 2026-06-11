"""Tests for the ``clm release`` CLI (issue #208, steps 2 + 3d)."""

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from clm.cli.commands.release import release_group
from clm.core.provenance_manifest import MANIFEST_FILENAME
from clm.release.frozen_manifest import (
    FROZEN_FILENAME,
    FrozenManifest,
    frozen_manifest_filename,
)
from clm.release.ledger import Ledger

SPEC = Path(__file__).parent.parent / "test-data" / "course-specs" / "test-spec-1.xml"
KNOWN_TOPIC = "some_topic_from_test_1"


def test_add_validates_and_appends(tmp_path):
    runner = CliRunner()
    ledger = tmp_path / "jan.txt"

    result = runner.invoke(release_group, ["add", str(SPEC), KNOWN_TOPIC, "--ledger", str(ledger)])
    assert result.exit_code == 0, result.output
    assert Ledger.load(ledger).released == [KNOWN_TOPIC]

    # Re-adding the same topic is a reported no-op.
    again = runner.invoke(release_group, ["add", str(SPEC), KNOWN_TOPIC, "--ledger", str(ledger)])
    assert again.exit_code == 0
    assert "Already released" in again.output
    assert Ledger.load(ledger).released == [KNOWN_TOPIC]


def test_add_rejects_unknown_topic(tmp_path):
    runner = CliRunner()
    ledger = tmp_path / "jan.txt"
    result = runner.invoke(
        release_group, ["add", str(SPEC), "definitely_not_a_topic", "--ledger", str(ledger)]
    )
    assert result.exit_code != 0
    assert "Unknown topic" in result.output
    # The ledger is never touched when validation fails.
    assert not ledger.exists()


def test_status_reports_released_and_pending(tmp_path):
    runner = CliRunner()
    ledger = tmp_path / "jan.txt"
    Ledger([KNOWN_TOPIC]).save(ledger)

    result = runner.invoke(release_group, ["status", str(SPEC), "--ledger", str(ledger)])
    assert result.exit_code == 0, result.output
    assert "released" in result.output
    assert KNOWN_TOPIC in result.output


def _write_source(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 1,
        "source_commit": "abc",
        "source_dirty": False,
        "built_at": "t",
        "target": "src",
        "files": [
            {
                "path": "Sec/01 Intro.ipynb",
                "topic_id": "intro",
                "section_id": "w01",
                "kind": "completed",
                "format": "notebook",
                "language": "en",
                "content_hash": "sha256:a",
            },
            {
                "path": "shared/data.csv",
                "topic_id": None,
                "section_id": None,
                "kind": None,
                "format": "dir-group",
                "language": "en",
                "content_hash": "sha256:c",
            },
        ],
    }
    (root / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    for entry in manifest["files"]:
        path = root / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry["content_hash"], encoding="utf-8")


def test_sync_copies_released_topic_and_freezes(tmp_path):
    runner = CliRunner()
    source = tmp_path / "src"
    dest = tmp_path / "jan"
    _write_source(source)
    ledger = tmp_path / "jan.txt"
    Ledger(["intro"]).save(ledger)

    result = runner.invoke(
        release_group,
        ["sync", "--ledger", str(ledger), "--source", str(source), "--dest", str(dest)],
    )
    assert result.exit_code == 0, result.output
    assert (dest / "Sec/01 Intro.ipynb").is_file()
    assert (dest / "shared/data.csv").is_file()

    # With explicit paths the channel name defaults to the destination dir name.
    frozen = FrozenManifest.load(dest / FROZEN_FILENAME, channel="jan")
    assert frozen.is_frozen("intro")
    assert frozen.skeleton_frozen is True
    assert frozen.channel == "jan"


def test_sync_dry_run_copies_nothing(tmp_path):
    runner = CliRunner()
    source = tmp_path / "src"
    dest = tmp_path / "jan"
    _write_source(source)
    ledger = tmp_path / "jan.txt"
    Ledger(["intro"]).save(ledger)

    result = runner.invoke(
        release_group,
        [
            "sync",
            "--ledger",
            str(ledger),
            "--source",
            str(source),
            "--dest",
            str(dest),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert not (dest / "Sec/01 Intro.ipynb").exists()


def test_sync_errors_without_manifest(tmp_path):
    runner = CliRunner()
    source = tmp_path / "src"
    source.mkdir()
    ledger = tmp_path / "jan.txt"
    Ledger(["intro"]).save(ledger)

    result = runner.invoke(
        release_group,
        ["sync", "--ledger", str(ledger), "--source", str(source), "--dest", str(tmp_path / "jan")],
    )
    assert result.exit_code != 0
    assert "No provenance manifest" in result.output


SPEC_WITH_CHANNELS = """
<course>
  <name><de>T</de><en>T</en></name>
  <prog-lang>python</prog-lang>
  <sections>
    <section>
      <name><de>S</de><en>S</en></name>
      <topics><topic>intro</topic></topics>
    </section>
  </sections>
  <output-targets>
    <output-target name="src">
      <path>output/src</path>
      <kinds><kind>completed</kind></kinds>
    </output-target>
  </output-targets>
  <release-channels source-target="src">
    <channel name="jan" path="solutions/jan" ledger="release/jan.txt"/>
  </release-channels>
</course>
""".strip()


def test_channel_resolves_ledger_source_and_dest_from_spec(tmp_path):
    runner = CliRunner()
    course_root = tmp_path
    # Spec must live in a subdir: resolve_course_paths uses its grandparent.
    specs_dir = course_root / "course-specs"
    specs_dir.mkdir()
    spec_file = specs_dir / "course.xml"
    spec_file.write_text(SPEC_WITH_CHANNELS, encoding="utf-8")

    # Built frozen source for the "src" output target.
    _write_source(course_root / "output" / "src")

    add = runner.invoke(release_group, ["add", str(spec_file), "intro", "--channel", "jan"])
    assert add.exit_code == 0, add.output
    assert Ledger.load(course_root / "release" / "jan.txt").released == ["intro"]

    sync = runner.invoke(release_group, ["sync", str(spec_file), "--channel", "jan"])
    assert sync.exit_code == 0, sync.output

    dest = course_root / "solutions" / "jan"
    assert (dest / "Sec/01 Intro.ipynb").is_file()
    frozen = FrozenManifest.load(dest / FROZEN_FILENAME, channel="jan")
    assert frozen.is_frozen("intro")
    assert frozen.skeleton_frozen is True


SPEC_TWO_STREAMS = """
<course>
  <name><de>T</de><en>T</en></name>
  <prog-lang>python</prog-lang>
  <sections>
    <section>
      <name><de>S</de><en>S</en></name>
      <topics><topic>intro</topic></topics>
    </section>
  </sections>
  <output-targets>
    <output-target name="shared">
      <path>output/shared</path>
      <kinds><kind>code-along</kind></kinds>
    </output-target>
    <output-target name="completed">
      <path>output/completed</path>
      <kinds><kind>completed</kind></kinds>
    </output-target>
  </output-targets>
  <release-channels name="materials" source-target="shared">
    <channel name="2026-04" path="release/materials/2026-04" ledger="release/materials-2026-04.txt"/>
  </release-channels>
  <release-channels name="solutions" source-target="completed">
    <channel name="2026-04" path="release/solutions/2026-04" ledger="release/solutions-2026-04.txt"/>
  </release-channels>
</course>
""".strip()


def test_two_streams_release_independently_from_their_own_sources(tmp_path):
    """The keystone scenario of issue #291: one cohort, two declarative streams.

    materials/2026-04 promotes from the `shared` build target, solutions/2026-04
    from the `completed` target, each gated by its own ledger — without ever
    falling back to explicit --ledger/--source/--dest plumbing.
    """
    runner = CliRunner()
    course_root = tmp_path
    spec_file = _write_spec(tmp_path, SPEC_TWO_STREAMS)
    _write_source(course_root / "output" / "shared")
    _write_source(course_root / "output" / "completed")

    # Release the topic on the materials stream only.
    add = runner.invoke(
        release_group, ["add", str(spec_file), "intro", "--channel", "materials/2026-04"]
    )
    assert add.exit_code == 0, add.output
    assert Ledger.load(course_root / "release" / "materials-2026-04.txt").released == ["intro"]
    assert not (course_root / "release" / "solutions-2026-04.txt").exists()

    sync = runner.invoke(release_group, ["sync", str(spec_file), "--channel", "materials/2026-04"])
    assert sync.exit_code == 0, sync.output
    materials_dest = course_root / "release" / "materials" / "2026-04"
    assert (materials_dest / "Sec/01 Intro.ipynb").is_file()
    # The other stream's destination is untouched: its ledger is empty.
    assert not (course_root / "release" / "solutions" / "2026-04" / "Sec").exists()

    # Now release on the solutions stream; it promotes from ITS source target.
    runner.invoke(release_group, ["add", str(spec_file), "intro", "--channel", "solutions/2026-04"])
    sync2 = runner.invoke(release_group, ["sync", str(spec_file), "--channel", "solutions/2026-04"])
    assert sync2.exit_code == 0, sync2.output
    solutions_dest = course_root / "release" / "solutions" / "2026-04"
    assert (solutions_dest / "Sec/01 Intro.ipynb").is_file()

    # Each destination froze under its canonical stream/channel address, in
    # the stream's own frozen-manifest file (issue #325).
    frozen = FrozenManifest.load(
        solutions_dest / frozen_manifest_filename("solutions"), channel="?"
    )
    assert frozen.channel == "solutions/2026-04"
    assert not (solutions_dest / FROZEN_FILENAME).exists()


def test_ambiguous_bare_channel_is_a_clear_error(tmp_path):
    runner = CliRunner()
    spec_file = _write_spec(tmp_path, SPEC_TWO_STREAMS)
    result = runner.invoke(release_group, ["add", str(spec_file), "intro", "--channel", "2026-04"])
    assert result.exit_code != 0
    assert "several streams" in result.output
    assert "materials/2026-04" in result.output


# ---------------------------------------------------------------------------
# `clm release week` (follow-up 3) — section-scoped ledger append
# ---------------------------------------------------------------------------

# test-spec-1 sections: "Week 1" (3 topics) and "Week 2" (1 topic).
WEEK1_TOPICS = {"some_topic_from_test_1", "a_topic_from_test_2", "punctuation_test"}
WEEK2_TOPICS = {"another_topic_from_test_1"}

# A multi-section spec whose middle section is disabled, to exercise the
# disabled-inclusive section index (an enabled="false" section still consumes
# its 1-based index — selecting "3" must reach the third *authored* section).
SPEC_WITH_DISABLED = """
<course>
  <name><de>T</de><en>T</en></name>
  <prog-lang>python</prog-lang>
  <sections>
    <section id="w01">
      <name><de>Woche 1</de><en>Week 1</en></name>
      <topics><topic>w1_a</topic><topic>w1_b</topic></topics>
    </section>
    <section id="w02" enabled="false">
      <name><de>Woche 2</de><en>Week 2</en></name>
      <topics><topic>w2_a</topic></topics>
    </section>
    <section id="w03">
      <name><de>Woche 3</de><en>Week 3</en></name>
      <topics><topic>w3_a</topic><topic>w3_b</topic></topics>
    </section>
  </sections>
</course>
""".strip()


def _write_spec(tmp_path: Path, body: str) -> Path:
    """Write a spec under a ``course-specs`` subdir (so resolve_course_paths,
    used by --channel resolution, treats the grandparent as the course root)."""
    specs_dir = tmp_path / "course-specs"
    specs_dir.mkdir(exist_ok=True)
    spec_file = specs_dir / "course.xml"
    spec_file.write_text(body, encoding="utf-8")
    return spec_file


def test_week_releases_a_section_by_name(tmp_path):
    runner = CliRunner()
    ledger = tmp_path / "jan.txt"

    result = runner.invoke(
        release_group, ["week", str(SPEC), "name:Week 1", "--ledger", str(ledger)]
    )
    assert result.exit_code == 0, result.output
    assert set(Ledger.load(ledger).released) == WEEK1_TOPICS
    # The other section's topic is untouched.
    assert "another_topic_from_test_1" not in Ledger.load(ledger).released


def test_week_releases_a_section_by_index(tmp_path):
    runner = CliRunner()
    ledger = tmp_path / "jan.txt"
    # Bare 1-based index selects the second authored section ("Week 2").
    result = runner.invoke(release_group, ["week", str(SPEC), "2", "--ledger", str(ledger)])
    assert result.exit_code == 0, result.output
    assert set(Ledger.load(ledger).released) == WEEK2_TOPICS


def test_week_index_is_disabled_inclusive(tmp_path):
    """Selecting index ``3`` must reach the third *authored* section even
    though the second is disabled — the index space is disabled-inclusive."""
    runner = CliRunner()
    spec = _write_spec(tmp_path, SPEC_WITH_DISABLED)
    ledger = tmp_path / "jan.txt"

    result = runner.invoke(release_group, ["week", str(spec), "3", "--ledger", str(ledger)])
    assert result.exit_code == 0, result.output
    # Index 3 → "Week 3", NOT the disabled "Week 2".
    assert set(Ledger.load(ledger).released) == {"w3_a", "w3_b"}


def test_week_warns_and_skips_a_disabled_section(tmp_path):
    """When the selection includes both an enabled and a disabled section, the
    disabled one is reported and skipped; the enabled one still releases."""
    runner = CliRunner()
    spec = _write_spec(tmp_path, SPEC_WITH_DISABLED)
    ledger = tmp_path / "jan.txt"

    result = runner.invoke(
        release_group, ["week", str(spec), "id:w01", "id:w02", "--ledger", str(ledger)]
    )
    assert result.exit_code == 0, result.output
    assert "skipping disabled section 'w02'" in result.output
    released = set(Ledger.load(ledger).released)
    assert released == {"w1_a", "w1_b"}
    assert "w2_a" not in released


def test_week_all_disabled_selection_errors(tmp_path):
    runner = CliRunner()
    spec = _write_spec(tmp_path, SPEC_WITH_DISABLED)
    ledger = tmp_path / "jan.txt"

    result = runner.invoke(release_group, ["week", str(spec), "id:w02", "--ledger", str(ledger)])
    assert result.exit_code != 0
    assert "disabled" in result.output
    assert not ledger.exists()


def test_week_unknown_selector_errors(tmp_path):
    runner = CliRunner()
    ledger = tmp_path / "jan.txt"
    result = runner.invoke(
        release_group, ["week", str(SPEC), "name:NoSuchWeek", "--ledger", str(ledger)]
    )
    assert result.exit_code != 0
    assert "did not match" in result.output
    assert not ledger.exists()


def test_week_reports_already_released_on_rerun(tmp_path):
    runner = CliRunner()
    ledger = tmp_path / "jan.txt"
    args = ["week", str(SPEC), "name:Week 1", "--ledger", str(ledger)]

    first = runner.invoke(release_group, args)
    assert first.exit_code == 0, first.output
    second = runner.invoke(release_group, args)
    assert second.exit_code == 0, second.output
    assert "Already released" in second.output
    # Re-running does not duplicate ledger entries.
    assert sorted(Ledger.load(ledger).released) == sorted(WEEK1_TOPICS)


def test_week_resolves_ledger_from_channel(tmp_path):
    runner = CliRunner()
    spec_file = _write_spec(tmp_path, SPEC_WITH_CHANNELS)

    # SPEC_WITH_CHANNELS has one section ("S") with topic "intro".
    result = runner.invoke(release_group, ["week", str(spec_file), "name:S", "--channel", "jan"])
    assert result.exit_code == 0, result.output
    assert Ledger.load(tmp_path / "release" / "jan.txt").released == ["intro"]


# ---------------------------------------------------------------------------
# `clm release sync --push` (step 3d) — real-git end-to-end
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _ls_files(repo: Path) -> set[str]:
    out = _git(repo, "ls-files")
    return {line.strip().replace("\\", "/") for line in out.stdout.splitlines() if line.strip()}


def _last_subject(repo: Path) -> str:
    return _git(repo, "log", "-1", "--format=%s").stdout.strip()


def _init_cohort_repo(
    dest: Path, *, remote: Path | None = None, gitignore_manifest: bool = True
) -> None:
    """Init ``dest`` like ``clm git init`` would: master branch + one commit.

    Mirrors the realistic state a cohort repo is in when ``clm release sync
    --push`` runs (the repo was created once, ahead of the first release).
    ``gitignore_manifest=False`` omits the ``.clm-manifest.json`` ``.gitignore``
    entry so a test can isolate the *staging* exclusion (the ``:(exclude)``
    pathspec / ``git rm --cached`` self-heal) rather than have ``.gitignore``
    silently mask it.
    """
    dest.mkdir(parents=True, exist_ok=True)
    _git(dest, "init", "-q")
    _git(dest, "checkout", "-q", "-b", "master")
    body = ".clm-manifest.json\n" if gitignore_manifest else "*.tmp\n"
    (dest / ".gitignore").write_text(body, encoding="utf-8")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-qm", "init cohort repo")
    if remote is not None:
        _git(dest, "remote", "add", "origin", str(remote))


def _init_bare_remote(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "--bare")
    return path


# Real-`git` end-to-end (commit + push to a local remote); ~2-4s/test. Runs in
# CI's integration step, excluded from the per-commit fast suite.
@pytest.mark.integration
class TestSyncPush:
    @pytest.fixture(autouse=True)
    def _git_identity(self, monkeypatch):
        for key, value in {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }.items():
            monkeypatch.setenv(key, value)

    def test_push_commits_promoted_files_and_pushes_to_remote(self, tmp_path):
        runner = CliRunner()
        source = tmp_path / "src"
        dest = tmp_path / "jan"
        _write_source(source)
        ledger = tmp_path / "jan.txt"
        Ledger(["intro"]).save(ledger)
        remote = _init_bare_remote(tmp_path / "jan.git")
        _init_cohort_repo(dest, remote=remote)

        result = runner.invoke(
            release_group,
            [
                "sync",
                "--ledger",
                str(ledger),
                "--source",
                str(source),
                "--dest",
                str(dest),
                "--push",
            ],
        )
        assert result.exit_code == 0, result.output

        tracked = _ls_files(dest)
        # Promoted content and the frozen manifest travel with the cohort.
        assert "Sec/01 Intro.ipynb" in tracked
        assert "shared/data.csv" in tracked
        assert FROZEN_FILENAME in tracked
        # Default message summarizes the freeze; channel name defaults to dest dir.
        assert _last_subject(dest) == "Release to jan: 1 new"
        # The commit reached the bare remote.
        assert _git(remote, "log", "-1", "--format=%s").stdout.strip() == "Release to jan: 1 new"

    def test_push_message_override_wins(self, tmp_path):
        runner = CliRunner()
        source = tmp_path / "src"
        dest = tmp_path / "jan"
        _write_source(source)
        ledger = tmp_path / "jan.txt"
        Ledger(["intro"]).save(ledger)
        _init_cohort_repo(dest)  # local-only: no remote

        result = runner.invoke(
            release_group,
            [
                "sync",
                "--ledger",
                str(ledger),
                "--source",
                str(source),
                "--dest",
                str(dest),
                "--push",
                "-m",
                "Custom release note",
            ],
        )
        assert result.exit_code == 0, result.output
        assert _last_subject(dest) == "Custom release note"
        assert "Skipped push: No remote configured" in result.output

    def _sync_push(self, runner, *, ledger, source, dest, extra=()):
        return runner.invoke(
            release_group,
            [
                "sync",
                "--ledger",
                str(ledger),
                "--source",
                str(source),
                "--dest",
                str(dest),
                "--push",
                *extra,
            ],
        )

    def test_push_excludes_private_manifest(self, tmp_path):
        """The release push path's *staging* exclusion (not .gitignore) keeps a
        stray ``.clm-manifest.json`` out of the cohort commit, while
        ``.clm-released.json`` ships. The cohort's .gitignore deliberately does
        NOT list the manifest, so this isolates the :(exclude) chokepoint — a
        regression of _stage_all_excluding_sidecars would be caught here."""
        runner = CliRunner()
        source = tmp_path / "src"
        dest = tmp_path / "jan"
        _write_source(source)
        ledger = tmp_path / "jan.txt"
        Ledger(["intro"]).save(ledger)
        _init_cohort_repo(dest, gitignore_manifest=False)
        # A stray private manifest sitting in the cohort working tree, NOT gitignored.
        (dest / MANIFEST_FILENAME).write_text('{"v": 1}', encoding="utf-8")

        result = self._sync_push(runner, ledger=ledger, source=source, dest=dest)
        assert result.exit_code == 0, result.output
        tracked = _ls_files(dest)
        assert MANIFEST_FILENAME not in tracked
        assert FROZEN_FILENAME in tracked

    def test_push_self_heals_a_pre_tracked_manifest(self, tmp_path):
        """A manifest a pre-exclusion commit already tracked is purged from the
        index on the next --push (git rm --cached self-heal)."""
        runner = CliRunner()
        source = tmp_path / "src"
        dest = tmp_path / "jan"
        _write_source(source)
        ledger = tmp_path / "jan.txt"
        Ledger(["intro"]).save(ledger)
        _init_cohort_repo(dest, gitignore_manifest=False)
        # Old-style: the manifest was committed before the exclusion existed.
        (dest / MANIFEST_FILENAME).write_text("OLD", encoding="utf-8")
        _git(dest, "add", MANIFEST_FILENAME)
        _git(dest, "commit", "-qm", "pre-exclusion tracked manifest")
        assert MANIFEST_FILENAME in _ls_files(dest)  # precondition

        result = self._sync_push(runner, ledger=ledger, source=source, dest=dest)
        assert result.exit_code == 0, result.output
        assert MANIFEST_FILENAME not in _ls_files(dest)  # purged from the index

    def test_push_without_git_repo_errors_after_promoting(self, tmp_path):
        runner = CliRunner()
        source = tmp_path / "src"
        dest = tmp_path / "jan"  # plain directory, never initialized as a repo
        _write_source(source)
        ledger = tmp_path / "jan.txt"
        Ledger(["intro"]).save(ledger)

        result = self._sync_push(runner, ledger=ledger, source=source, dest=dest)
        assert result.exit_code != 0
        assert "No git repository" in result.output
        # Explicit-dest mode has no <release-channels>, so the hint must NOT
        # suggest the unrunnable `clm git init --channel`; it points at `git init`.
        assert "git init" in result.output
        assert "--channel" not in result.output
        # Promotion still happened; only the push step failed.
        assert (dest / "Sec/01 Intro.ipynb").is_file()
        assert not (dest / ".git").exists()

    def test_push_is_idempotent_on_rerun(self, tmp_path):
        """A second --push with nothing newly promoted is a clean no-op
        (index-scoped has_staged_changes), exit 0, no second commit."""
        runner = CliRunner()
        source = tmp_path / "src"
        dest = tmp_path / "jan"
        _write_source(source)
        ledger = tmp_path / "jan.txt"
        Ledger(["intro"]).save(ledger)
        _init_cohort_repo(dest)

        first = self._sync_push(runner, ledger=ledger, source=source, dest=dest)
        assert first.exit_code == 0, first.output
        commits_after_first = _git(dest, "rev-list", "--count", "HEAD").stdout.strip()

        second = self._sync_push(runner, ledger=ledger, source=source, dest=dest)
        assert second.exit_code == 0, second.output
        assert "No changes to commit" in second.output
        assert _git(dest, "rev-list", "--count", "HEAD").stdout.strip() == commits_after_first

    def test_refreeze_push_commits_again(self, tmp_path):
        """`--refreeze TOPIC --push` re-copies + re-freezes the topic and lands a
        new commit whose default message reports the refreeze."""
        runner = CliRunner()
        source = tmp_path / "src"
        dest = tmp_path / "jan"
        _write_source(source)
        ledger = tmp_path / "jan.txt"
        Ledger(["intro"]).save(ledger)
        _init_cohort_repo(dest)

        first = self._sync_push(runner, ledger=ledger, source=source, dest=dest)
        assert first.exit_code == 0, first.output
        count_before = int(_git(dest, "rev-list", "--count", "HEAD").stdout.strip())

        # Change the source bytes so the refreeze actually rewrites content.
        (source / "Sec" / "01 Intro.ipynb").write_text("patched", encoding="utf-8")
        again = self._sync_push(
            runner, ledger=ledger, source=source, dest=dest, extra=("--refreeze", "intro")
        )
        assert again.exit_code == 0, again.output
        assert "refrozen" in _last_subject(dest)
        assert int(_git(dest, "rev-list", "--count", "HEAD").stdout.strip()) == count_before + 1

    def test_channel_push_resolves_repo_from_spec(self, tmp_path):
        runner = CliRunner()
        course_root = tmp_path
        specs_dir = course_root / "course-specs"
        specs_dir.mkdir()
        spec_file = specs_dir / "course.xml"
        spec_file.write_text(SPEC_WITH_CHANNELS, encoding="utf-8")
        _write_source(course_root / "output" / "src")
        Ledger(["intro"]).save(course_root / "release" / "jan.txt")

        dest = course_root / "solutions" / "jan"
        remote = _init_bare_remote(course_root / "jan.git")
        _init_cohort_repo(dest, remote=remote)

        clean_config = MagicMock()
        clean_config.git.remote_template = ""
        clean_config.git.remote_path = ""
        with patch("clm.cli.commands.git.get_config", return_value=clean_config):
            result = runner.invoke(
                release_group, ["sync", str(spec_file), "--channel", "jan", "--push"]
            )
        assert result.exit_code == 0, result.output
        tracked = _ls_files(dest)
        assert "Sec/01 Intro.ipynb" in tracked
        assert FROZEN_FILENAME in tracked
        assert _last_subject(dest) == "Release to jan: 1 new"
        assert _git(remote, "log", "-1", "--format=%s").stdout.strip() == "Release to jan: 1 new"


# ---------------------------------------------------------------------------
# Language-scoped channels (issue #293)
# ---------------------------------------------------------------------------

SPEC_LANG_CHANNELS = """
<course>
  <name><de>T</de><en>T</en></name>
  <prog-lang>python</prog-lang>
  <project-slug>ml</project-slug>
  <sections>
    <section>
      <name><de>S</de><en>S</en></name>
      <topics><topic>intro</topic></topics>
    </section>
  </sections>
  <output-targets>
    <output-target name="src">
      <path>output/src</path>
      <kinds><kind>completed</kind></kinds>
    </output-target>
  </output-targets>
  <release-channels source-target="src">
    <channel name="jan-de" lang="de" path="solutions/jan-de" ledger="release/jan-de.txt"/>
    <channel name="jan-en" lang="en" path="solutions/jan-en" ledger="release/jan-en.txt"/>
    <channel name="jan-all" path="solutions/jan-all" ledger="release/jan-all.txt"/>
  </release-channels>
</course>
""".strip()


def _write_two_language_source(root: Path) -> None:
    """A built `src` target with de (ml-de) and en (ml-en) language roots."""
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 1,
        "source_commit": "abc",
        "source_dirty": False,
        "built_at": "t",
        "target": "src",
        "files": [
            {
                "path": "ml-de/Folien/01 Intro.ipynb",
                "topic_id": "intro",
                "section_id": "w01",
                "kind": "completed",
                "format": "notebook",
                "language": "de",
                "content_hash": "sha256:a",
            },
            {
                "path": "ml-en/Slides/01 Intro.ipynb",
                "topic_id": "intro",
                "section_id": "w01",
                "kind": "completed",
                "format": "notebook",
                "language": "en",
                "content_hash": "sha256:b",
            },
        ],
    }
    (root / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    for entry in manifest["files"]:
        path = root / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry["content_hash"], encoding="utf-8")


def test_lang_channel_promotes_one_language_rerooted(tmp_path):
    runner = CliRunner()
    spec_file = _write_spec(tmp_path, SPEC_LANG_CHANNELS)
    _write_two_language_source(tmp_path / "output" / "src")

    runner.invoke(release_group, ["add", str(spec_file), "intro", "--channel", "jan-de"])
    sync = runner.invoke(release_group, ["sync", str(spec_file), "--channel", "jan-de"])
    assert sync.exit_code == 0, sync.output

    dest = tmp_path / "solutions" / "jan-de"
    # Re-rooted at the language directory: no ml-de/ segment in the repo.
    assert (dest / "Folien/01 Intro.ipynb").is_file()
    assert not (dest / "ml-de").exists()
    # The other language never leaks into the scoped channel.
    assert not (dest / "Slides").exists()
    assert not (dest / "ml-en").exists()


def test_unscoped_channel_still_receives_every_language_root(tmp_path):
    runner = CliRunner()
    spec_file = _write_spec(tmp_path, SPEC_LANG_CHANNELS)
    _write_two_language_source(tmp_path / "output" / "src")

    runner.invoke(release_group, ["add", str(spec_file), "intro", "--channel", "jan-all"])
    sync = runner.invoke(release_group, ["sync", str(spec_file), "--channel", "jan-all"])
    assert sync.exit_code == 0, sync.output

    dest = tmp_path / "solutions" / "jan-all"
    assert (dest / "ml-de/Folien/01 Intro.ipynb").is_file()
    assert (dest / "ml-en/Slides/01 Intro.ipynb").is_file()


def test_language_option_overrides_channel_scope(tmp_path):
    runner = CliRunner()
    spec_file = _write_spec(tmp_path, SPEC_LANG_CHANNELS)
    _write_two_language_source(tmp_path / "output" / "src")

    runner.invoke(release_group, ["add", str(spec_file), "intro", "--channel", "jan-all"])
    sync = runner.invoke(
        release_group,
        ["sync", str(spec_file), "--channel", "jan-all", "--language", "en"],
    )
    assert sync.exit_code == 0, sync.output

    dest = tmp_path / "solutions" / "jan-all"
    assert (dest / "Slides/01 Intro.ipynb").is_file()
    assert not (dest / "ml-de").exists()


def test_language_without_spec_file_errors(tmp_path):
    runner = CliRunner()
    source = tmp_path / "src"
    _write_source(source)
    ledger = tmp_path / "jan.txt"
    Ledger(["intro"]).save(ledger)
    result = runner.invoke(
        release_group,
        [
            "sync",
            "--ledger",
            str(ledger),
            "--source",
            str(source),
            "--dest",
            str(tmp_path / "dest"),
            "--language",
            "de",
        ],
    )
    assert result.exit_code != 0
    assert "requires the SPEC_FILE" in result.output


def test_missing_language_root_is_a_clear_error(tmp_path):
    runner = CliRunner()
    spec_file = _write_spec(tmp_path, SPEC_LANG_CHANNELS)
    source = tmp_path / "output" / "src"
    _write_two_language_source(source)
    import shutil

    shutil.rmtree(source / "ml-de")

    runner.invoke(release_group, ["add", str(spec_file), "intro", "--channel", "jan-de"])
    sync = runner.invoke(release_group, ["sync", str(spec_file), "--channel", "jan-de"])
    assert sync.exit_code != 0
    assert "Language root not found" in sync.output


# ---------------------------------------------------------------------------
# Evergreen skeleton files (never frozen; re-copied when content changes)
# ---------------------------------------------------------------------------

SPEC_WITH_EVERGREEN = SPEC_WITH_CHANNELS.replace(
    '<release-channels source-target="src">',
    '<release-channels source-target="src">\n    <evergreen>NEWS.md</evergreen>',
)


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _set_news(root: Path, content: str, *, path: str = "NEWS.md", language: str = "en") -> None:
    """Write/update a NEWS skeleton file in a built source, with a real hash.

    Evergreen compares the destination's sha256 against the manifest's
    ``content_hash``, so the entry must carry the true digest of the bytes —
    unlike the other fixtures' fake hashes.
    """
    manifest = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    manifest["files"] = [e for e in manifest["files"] if e["path"] != path] + [
        {
            "path": path,
            "topic_id": None,
            "section_id": None,
            "kind": None,
            "format": "dir-group",
            "language": language,
            "content_hash": _sha(content),
        }
    ]
    (root / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_channel_evergreen_refreshes_news_across_syncs(tmp_path):
    runner = CliRunner()
    spec_file = _write_spec(tmp_path, SPEC_WITH_EVERGREEN)
    source = tmp_path / "output" / "src"
    _write_source(source)
    _set_news(source, "news v1")

    runner.invoke(release_group, ["add", str(spec_file), "intro", "--channel", "jan"])
    first = runner.invoke(release_group, ["sync", str(spec_file), "--channel", "jan"])
    assert first.exit_code == 0, first.output
    dest = tmp_path / "solutions" / "jan"
    # First sync ships NEWS with the skeleton; no refresh pass yet.
    assert (dest / "NEWS.md").read_text(encoding="utf-8") == "news v1"
    assert "refresh" not in first.output

    # The course publishes a new NEWS; the next sync refreshes it while the
    # frozen topic stays untouched.
    _set_news(source, "news v2")
    second = runner.invoke(release_group, ["sync", str(spec_file), "--channel", "jan"])
    assert second.exit_code == 0, second.output
    assert "refresh" in second.output
    assert "NEWS.md" in second.output
    assert "skip-frozen" in second.output
    assert (dest / "NEWS.md").read_text(encoding="utf-8") == "news v2"
    frozen = FrozenManifest.load(dest / FROZEN_FILENAME, channel="jan")
    assert frozen.is_frozen("intro")
    assert frozen.skeleton_frozen is True

    # Unchanged content -> up-to-date, nothing copied.
    third = runner.invoke(release_group, ["sync", str(spec_file), "--channel", "jan"])
    assert third.exit_code == 0, third.output
    assert "up-to-date" in third.output
    assert "Evergreen: refreshed" not in third.output


def test_evergreen_flag_in_explicit_paths_mode(tmp_path):
    runner = CliRunner()
    source = tmp_path / "src"
    dest = tmp_path / "jan"
    _write_source(source)
    _set_news(source, "news v1")
    ledger = tmp_path / "jan.txt"
    Ledger(["intro"]).save(ledger)
    args = ["sync", "--ledger", str(ledger), "--source", str(source), "--dest", str(dest)]

    first = runner.invoke(release_group, [*args, "--evergreen", "NEWS.md"])
    assert first.exit_code == 0, first.output

    _set_news(source, "news v2")
    second = runner.invoke(release_group, [*args, "--evergreen", "NEWS.md"])
    assert second.exit_code == 0, second.output
    assert (dest / "NEWS.md").read_text(encoding="utf-8") == "news v2"
    assert "Evergreen: refreshed 1 file(s): NEWS.md" in second.output

    # Without the flag (and no spec patterns) the file stays frozen.
    _set_news(source, "news v3")
    third = runner.invoke(release_group, args)
    assert third.exit_code == 0, third.output
    assert (dest / "NEWS.md").read_text(encoding="utf-8") == "news v2"


def test_evergreen_pattern_matching_topic_files_warns_and_ignores(tmp_path):
    runner = CliRunner()
    source = tmp_path / "src"
    dest = tmp_path / "jan"
    _write_source(source)
    ledger = tmp_path / "jan.txt"
    Ledger(["intro"]).save(ledger)
    args = ["sync", "--ledger", str(ledger), "--source", str(source), "--dest", str(dest)]

    first = runner.invoke(release_group, args)
    assert first.exit_code == 0, first.output

    # The pattern matches a topic-owned notebook: warned about, never planned.
    (source / "Sec" / "01 Intro.ipynb").write_text("patched", encoding="utf-8")
    second = runner.invoke(release_group, [*args, "--evergreen", "Sec/*"])
    assert second.exit_code == 0, second.output
    assert "topic-owned" in second.output
    assert "--refreeze" in second.output
    assert (dest / "Sec/01 Intro.ipynb").read_text(encoding="utf-8") == "sha256:a"


def test_evergreen_dry_run_shows_refresh_and_copies_nothing(tmp_path):
    runner = CliRunner()
    source = tmp_path / "src"
    dest = tmp_path / "jan"
    _write_source(source)
    _set_news(source, "news v1")
    ledger = tmp_path / "jan.txt"
    Ledger(["intro"]).save(ledger)
    args = ["sync", "--ledger", str(ledger), "--source", str(source), "--dest", str(dest)]

    first = runner.invoke(release_group, [*args, "--evergreen", "NEWS.md"])
    assert first.exit_code == 0, first.output

    _set_news(source, "news v2")
    dry = runner.invoke(release_group, [*args, "--evergreen", "NEWS.md", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "refresh" in dry.output
    assert "NEWS.md" in dry.output
    assert (dest / "NEWS.md").read_text(encoding="utf-8") == "news v1"


def test_lang_channel_evergreen_matches_rerooted_path(tmp_path):
    """Patterns are destination-relative: for a lang-scoped channel the NEWS
    at ``ml-de/NEWS.md`` in the source matches the pattern ``NEWS.md``."""
    runner = CliRunner()
    spec = SPEC_LANG_CHANNELS.replace(
        '<release-channels source-target="src">',
        '<release-channels source-target="src">\n    <evergreen>NEWS.md</evergreen>',
    )
    spec_file = _write_spec(tmp_path, spec)
    source = tmp_path / "output" / "src"
    _write_two_language_source(source)
    _set_news(source, "de news v1", path="ml-de/NEWS.md", language="de")

    runner.invoke(release_group, ["add", str(spec_file), "intro", "--channel", "jan-de"])
    first = runner.invoke(release_group, ["sync", str(spec_file), "--channel", "jan-de"])
    assert first.exit_code == 0, first.output
    dest = tmp_path / "solutions" / "jan-de"
    assert (dest / "NEWS.md").read_text(encoding="utf-8") == "de news v1"

    _set_news(source, "de news v2", path="ml-de/NEWS.md", language="de")
    second = runner.invoke(release_group, ["sync", str(spec_file), "--channel", "jan-de"])
    assert second.exit_code == 0, second.output
    assert (dest / "NEWS.md").read_text(encoding="utf-8") == "de news v2"


# ---------------------------------------------------------------------------
# Partial manifest from an errored build (issue #295)
# ---------------------------------------------------------------------------


def test_sync_promotes_green_topics_and_refuses_failed_ones(tmp_path):
    runner = CliRunner()
    source = tmp_path / "src"
    _write_source(source)
    # Mark the build partial with a failed topic that is also released.
    manifest = json.loads((source / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    manifest["partial"] = True
    manifest["failed_topics"] = ["flaky"]
    (source / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")

    ledger = tmp_path / "jan.txt"
    Ledger(["intro", "flaky"]).save(ledger)
    dest = tmp_path / "jan"

    result = runner.invoke(
        release_group,
        ["sync", "--ledger", str(ledger), "--source", str(source), "--dest", str(dest)],
    )
    assert result.exit_code == 0, result.output
    # The green topic was promoted and frozen...
    assert (dest / "Sec/01 Intro.ipynb").is_file()
    frozen = FrozenManifest.load(dest / FROZEN_FILENAME, channel="jan")
    assert frozen.is_frozen("intro")
    # ...the failed one was refused, loudly, and stays unfrozen (retried later).
    assert not frozen.is_frozen("flaky")
    assert "skip-failed" in result.output
    assert "NOT promoted" in result.output
    assert "partial" in result.output
