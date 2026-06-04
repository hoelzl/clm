"""Multi-cohort divergence tests for ``clm release`` (issue #208, step 4).

The release engine already supports running several cohorts off one frozen
source on independent schedules; these tests pin the *behavior* that makes that
safe — each cohort freezes/ships only its own released topics, a frozen topic is
never re-propagated when the source later changes (so cohorts that reached a
topic at different times legitimately diverge), and ``--refreeze`` is the only
override. Driven end-to-end through the ``clm release sync`` CLI.
"""

import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.release import release_group
from clm.core.provenance_manifest import MANIFEST_FILENAME
from clm.release.frozen_manifest import FROZEN_FILENAME, FrozenManifest
from clm.release.ledger import Ledger


def _hash(body: str) -> str:
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _topic_path(topic_id: str) -> str:
    return f"Sec/{topic_id}.ipynb"


SKELETON_PATH = "shared/data.csv"
SKELETON_BODY = "skeleton-bytes"


def _write_source_entries(
    root: Path, *, source_commit: str, entries: list[tuple[str, str | None, str]]
) -> None:
    """Low-level source writer. ``entries`` is ``[(path, topic_id, body), ...]``;
    the manifest lists files in exactly this order (so callers can control the
    digest-input order). ``topic_id=None`` marks the skeleton/dir-group. Each
    ``content_hash`` is derived from its body. Overwrites ``root`` wholesale.
    """
    files = [
        {
            "path": path,
            "topic_id": topic_id,
            "section_id": "w01" if topic_id else None,
            "kind": "completed" if topic_id else None,
            "format": "notebook" if topic_id else "dir-group",
            "language": "en",
            "content_hash": _hash(body),
        }
        for path, topic_id, body in entries
    ]
    manifest = {
        "version": 1,
        "source_commit": source_commit,
        "source_dirty": False,
        "built_at": "t",
        "target": "src",
        "files": files,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    for path, _topic_id, body in entries:
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")


def _build_source(
    root: Path,
    *,
    source_commit: str,
    topics: dict[str, str],
    skeleton_body: str = SKELETON_BODY,
) -> None:
    """Write a frozen-source build: ``.clm-manifest.json`` + each file on disk.

    ``topics`` maps ``topic_id -> body``; each topic gets one ``completed``
    notebook whose ``content_hash`` is derived from its body, so a changed body
    yields both different copied bytes and a different rolled-up ``topic_digest``
    — exactly what a real rebuild produces. A shared (topic-less) skeleton file
    is always included (its bytes parametrizable to model a skeleton change).
    Overwrites ``root`` wholesale, modelling a rebuild.
    """
    entries: list[tuple[str, str | None, str]] = [
        (_topic_path(topic_id), topic_id, body) for topic_id, body in topics.items()
    ]
    entries.append((SKELETON_PATH, None, skeleton_body))
    _write_source_entries(root, source_commit=source_commit, entries=entries)


def _sync(runner: CliRunner, *, ledger: Path, source: Path, dest: Path, extra=()):
    return runner.invoke(
        release_group,
        ["sync", "--ledger", str(ledger), "--source", str(source), "--dest", str(dest), *extra],
    )


def _frozen(dest: Path, channel: str) -> FrozenManifest:
    return FrozenManifest.load(dest / FROZEN_FILENAME, channel=channel)


def _topic_body(dest: Path, topic_id: str) -> str:
    return (dest / _topic_path(topic_id)).read_text(encoding="utf-8")


class TestMultiCohortDivergence:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_each_cohort_freezes_only_its_own_released_topics(self, tmp_path, runner):
        source = tmp_path / "src"
        _build_source(
            source, source_commit="C1", topics={"intro": "intro-v1", "advanced": "adv-v1"}
        )

        jan, may = tmp_path / "jan", tmp_path / "may"
        jan_ledger, may_ledger = tmp_path / "jan.txt", tmp_path / "may.txt"
        Ledger(["intro"]).save(jan_ledger)
        Ledger(["intro", "advanced"]).save(may_ledger)

        assert _sync(runner, ledger=jan_ledger, source=source, dest=jan).exit_code == 0
        assert _sync(runner, ledger=may_ledger, source=source, dest=may).exit_code == 0

        # jan released only intro; advanced is neither shipped nor frozen.
        assert (jan / _topic_path("intro")).is_file()
        assert not (jan / _topic_path("advanced")).exists()
        assert _frozen(jan, "jan").is_frozen("intro")
        assert not _frozen(jan, "jan").is_frozen("advanced")

        # may released both.
        assert (may / _topic_path("intro")).is_file()
        assert (may / _topic_path("advanced")).is_file()
        assert _frozen(may, "may").is_frozen("advanced")

        # Both cohorts get the shared skeleton.
        assert (jan / SKELETON_PATH).is_file()
        assert (may / SKELETON_PATH).is_file()

    def test_frozen_topic_is_not_repropagated_when_source_changes(self, tmp_path, runner):
        """The core guarantee: a topic a cohort already received is pinned. Two
        cohorts that reach the same topic across a source change diverge."""
        source = tmp_path / "src"
        _build_source(source, source_commit="C1", topics={"intro": "intro-v1"})

        jan, may = tmp_path / "jan", tmp_path / "may"
        jan_ledger, may_ledger = tmp_path / "jan.txt", tmp_path / "may.txt"
        Ledger(["intro"]).save(jan_ledger)
        Ledger(["intro"]).save(may_ledger)

        # jan reaches intro while the source is at v1.
        assert _sync(runner, ledger=jan_ledger, source=source, dest=jan).exit_code == 0
        assert _topic_body(jan, "intro") == "intro-v1"

        # The course is edited and rebuilt: intro is now v2 at a new commit.
        _build_source(source, source_commit="C2", topics={"intro": "intro-v2"})

        # jan re-syncs but intro is already frozen -> NOT re-propagated.
        again = _sync(runner, ledger=jan_ledger, source=source, dest=jan)
        assert again.exit_code == 0
        assert "already frozen (skipped)" in again.output
        assert _topic_body(jan, "intro") == "intro-v1"  # still the v1 bytes
        assert _frozen(jan, "jan").frozen["intro"].source_commit == "C1"

        # may reaches intro only now, after the change -> freezes v2.
        assert _sync(runner, ledger=may_ledger, source=source, dest=may).exit_code == 0
        assert _topic_body(may, "intro") == "intro-v2"
        assert _frozen(may, "may").frozen["intro"].source_commit == "C2"

        # The two cohorts legitimately diverge on the same topic.
        assert _topic_body(jan, "intro") != _topic_body(may, "intro")
        jan_rec = _frozen(jan, "jan").frozen["intro"]
        may_rec = _frozen(may, "may").frozen["intro"]
        assert jan_rec.source_commit != may_rec.source_commit
        assert jan_rec.topic_digest != may_rec.topic_digest

    def test_refreeze_is_the_only_override(self, tmp_path, runner):
        source = tmp_path / "src"
        _build_source(source, source_commit="C1", topics={"intro": "intro-v1"})
        jan = tmp_path / "jan"
        ledger = tmp_path / "jan.txt"
        Ledger(["intro"]).save(ledger)

        assert _sync(runner, ledger=ledger, source=source, dest=jan).exit_code == 0
        _build_source(source, source_commit="C2", topics={"intro": "intro-v2"})

        # A plain re-sync keeps v1 (proven above); --refreeze forces the update.
        refreeze = _sync(
            runner, ledger=ledger, source=source, dest=jan, extra=("--refreeze", "intro")
        )
        assert refreeze.exit_code == 0
        assert "re-frozen" in refreeze.output
        assert _topic_body(jan, "intro") == "intro-v2"
        assert _frozen(jan, "jan").frozen["intro"].source_commit == "C2"

    def test_skeleton_is_pinned_across_a_source_change(self, tmp_path, runner):
        """The skeleton is copied once at channel init, then frozen — a later
        source change does not re-propagate it (same pin guarantee as topics)."""
        source = tmp_path / "src"
        _build_source(source, source_commit="C1", topics={"intro": "v1"}, skeleton_body="skel-v1")

        jan, may = tmp_path / "jan", tmp_path / "may"
        jan_ledger, may_ledger = tmp_path / "jan.txt", tmp_path / "may.txt"
        Ledger(["intro"]).save(jan_ledger)
        Ledger(["intro"]).save(may_ledger)

        assert _sync(runner, ledger=jan_ledger, source=source, dest=jan).exit_code == 0
        assert (jan / SKELETON_PATH).read_text(encoding="utf-8") == "skel-v1"

        # Rebuild with a changed skeleton; jan re-syncs but its skeleton is pinned.
        _build_source(source, source_commit="C2", topics={"intro": "v2"}, skeleton_body="skel-v2")
        assert _sync(runner, ledger=jan_ledger, source=source, dest=jan).exit_code == 0
        assert (jan / SKELETON_PATH).read_text(encoding="utf-8") == "skel-v1"  # not re-copied

        # may reaches it only now -> gets the new skeleton. Cohorts diverge.
        assert _sync(runner, ledger=may_ledger, source=source, dest=may).exit_code == 0
        assert (may / SKELETON_PATH).read_text(encoding="utf-8") == "skel-v2"

    def test_unchanged_resync_freezes_nothing_new(self, tmp_path, runner):
        source = tmp_path / "src"
        _build_source(source, source_commit="C1", topics={"intro": "intro-v1"})
        jan = tmp_path / "jan"
        ledger = tmp_path / "jan.txt"
        Ledger(["intro"]).save(ledger)

        first = _sync(runner, ledger=ledger, source=source, dest=jan)
        assert first.exit_code == 0
        assert "1 newly frozen" in first.output

        # No ledger or source change -> second sync is a pure no-op.
        second = _sync(runner, ledger=ledger, source=source, dest=jan)
        assert second.exit_code == 0
        assert "Copied 0 file(s)" in second.output
        assert "0 newly frozen" in second.output
        assert "1 already frozen (skipped)" in second.output


SPEC_TWO_CHANNELS = """
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
    <channel name="may" path="solutions/may" ledger="release/may.txt"/>
  </release-channels>
</course>
""".strip()


def test_two_channels_resolve_and_diverge_from_one_spec(tmp_path):
    """Two cohorts declared in one spec, resolved by ``--channel`` and run on
    different schedules across a source change, end up with divergent content."""
    runner = CliRunner()
    course_root = tmp_path
    specs_dir = course_root / "course-specs"
    specs_dir.mkdir()
    spec_file = specs_dir / "course.xml"
    spec_file.write_text(SPEC_TWO_CHANNELS, encoding="utf-8")

    source = course_root / "output" / "src"
    Ledger(["intro"]).save(course_root / "release" / "jan.txt")
    Ledger(["intro"]).save(course_root / "release" / "may.txt")

    # jan reaches intro at v1.
    _build_source(source, source_commit="C1", topics={"intro": "intro-v1"})
    assert runner.invoke(release_group, ["sync", str(spec_file), "--channel", "jan"]).exit_code == 0

    # Source is rebuilt; may reaches intro at v2; jan re-syncs but stays pinned.
    _build_source(source, source_commit="C2", topics={"intro": "intro-v2"})
    assert runner.invoke(release_group, ["sync", str(spec_file), "--channel", "may"]).exit_code == 0
    assert runner.invoke(release_group, ["sync", str(spec_file), "--channel", "jan"]).exit_code == 0

    jan_dest = course_root / "solutions" / "jan"
    may_dest = course_root / "solutions" / "may"
    assert _topic_body(jan_dest, "intro") == "intro-v1"
    assert _topic_body(may_dest, "intro") == "intro-v2"
    assert _frozen(jan_dest, "jan").frozen["intro"].source_commit == "C1"
    assert _frozen(may_dest, "may").frozen["intro"].source_commit == "C2"


def test_topic_digest_is_order_independent_over_multiple_files(tmp_path):
    """``_topic_digest`` rolls up a topic's per-file hashes order-independently,
    so two builds that list a multi-file topic's files in different manifest
    order freeze to the same digest (clean, stable ``.clm-released.json``)."""
    runner = CliRunner()
    files = [("Sec/a.ipynb", "multi", "body-a"), ("Sec/b.ipynb", "multi", "body-b")]
    s1, s2 = tmp_path / "s1", tmp_path / "s2"
    _write_source_entries(s1, source_commit="C", entries=files)
    _write_source_entries(s2, source_commit="C", entries=list(reversed(files)))

    ledger = tmp_path / "l.txt"
    Ledger(["multi"]).save(ledger)
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    assert _sync(runner, ledger=ledger, source=s1, dest=d1).exit_code == 0
    assert _sync(runner, ledger=ledger, source=s2, dest=d2).exit_code == 0

    # Both files of the multi-file topic shipped.
    assert (d1 / "Sec/a.ipynb").is_file() and (d1 / "Sec/b.ipynb").is_file()
    # Identical digest despite reversed manifest order.
    assert (
        _frozen(d1, "d1").frozen["multi"].topic_digest
        == _frozen(d2, "d2").frozen["multi"].topic_digest
    )


def test_released_but_unbuilt_topic_is_skipped_then_frozen_once_built(tmp_path):
    """A cohort may release a topic the current build does not yet include; sync
    ships+freezes nothing for it (so it retries), then freezes it once built."""
    runner = CliRunner()
    source = tmp_path / "src"
    _build_source(source, source_commit="C1", topics={"intro": "v1"})  # no "future" topic
    dest = tmp_path / "jan"
    ledger = tmp_path / "jan.txt"
    Ledger(["intro", "future"]).save(ledger)

    first = _sync(runner, ledger=ledger, source=source, dest=dest)
    assert first.exit_code == 0
    assert _frozen(dest, "jan").is_frozen("intro")
    assert not _frozen(dest, "jan").is_frozen("future")  # unbuilt -> not frozen
    assert not (dest / _topic_path("future")).exists()

    # The next build includes 'future'; a re-sync now freezes it (intro stays pinned).
    _build_source(source, source_commit="C2", topics={"intro": "v1", "future": "fut-v1"})
    second = _sync(runner, ledger=ledger, source=source, dest=dest)
    assert second.exit_code == 0
    assert (dest / _topic_path("future")).read_text(encoding="utf-8") == "fut-v1"
    assert _frozen(dest, "jan").is_frozen("future")
