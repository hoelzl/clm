"""Tests for several release streams sharing one destination repo (issue #325).

Covers the four pillars of the feature:

* per-stream frozen manifests (``.clm-released.<stream>.json``) with adoption
  of a legacy ``.clm-released.json`` whose channel matches;
* presence-as-frozen skeleton policy (a later stream never overwrites the
  skeleton an earlier stream froze);
* the cross-stream overlap preflight (topic-owned paths must be disjoint);
* ``clm git`` visiting a shared destination once.
"""

import hashlib
import json
from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands.git import OutputRepo, _dedupe_shared_destinations
from clm.cli.commands.release import release_group
from clm.core.provenance_manifest import MANIFEST_FILENAME
from clm.release.frozen_manifest import (
    FROZEN_FILENAME,
    FrozenManifest,
    FrozenRecord,
    frozen_manifest_filename,
    load_frozen_manifest,
)
from clm.release.ledger import Ledger
from clm.release.sync import (
    REFRESH,
    apply_sync,
    plan_sync,
    scan_evergreen,
    scan_skeleton,
    topic_path_overlap,
)

# ---------------------------------------------------------------------------
# frozen_manifest: per-stream filenames + legacy adoption
# ---------------------------------------------------------------------------


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class TestPerStreamFrozenManifest:
    def test_filename_per_stream_and_legacy_for_unnamed(self):
        assert frozen_manifest_filename("materials") == ".clm-released.materials.json"
        assert frozen_manifest_filename("") == FROZEN_FILENAME

    def test_missing_files_yield_empty_manifest_at_per_stream_path(self, tmp_path):
        loaded = load_frozen_manifest(tmp_path, stream="materials", channel="materials/jan")
        assert loaded.manifest.frozen == {}
        assert loaded.manifest.channel == "materials/jan"
        assert loaded.path == tmp_path / ".clm-released.materials.json"
        assert loaded.adopted_legacy is None
        assert loaded.ignored_legacy_channel is None

    def test_unnamed_stream_keeps_the_legacy_path(self, tmp_path):
        legacy = FrozenManifest(channel="jan", skeleton_frozen=True)
        legacy.save(tmp_path / FROZEN_FILENAME)
        loaded = load_frozen_manifest(tmp_path, stream="", channel="jan")
        assert loaded.path == tmp_path / FROZEN_FILENAME
        assert loaded.manifest.skeleton_frozen is True

    def test_matching_legacy_is_adopted(self, tmp_path):
        legacy = FrozenManifest(channel="materials/jan", skeleton_frozen=True)
        legacy.freeze("intro", FrozenRecord("abc", "t", "sha256:d"))
        legacy.save(tmp_path / FROZEN_FILENAME)

        loaded = load_frozen_manifest(tmp_path, stream="materials", channel="materials/jan")
        assert loaded.manifest.is_frozen("intro")
        assert loaded.manifest.skeleton_frozen is True
        assert loaded.path == tmp_path / ".clm-released.materials.json"
        assert loaded.adopted_legacy == tmp_path / FROZEN_FILENAME

    def test_foreign_legacy_is_left_alone(self, tmp_path):
        legacy = FrozenManifest(channel="materials/jan")
        legacy.freeze("intro", FrozenRecord("abc", "t", "sha256:d"))
        legacy.save(tmp_path / FROZEN_FILENAME)

        loaded = load_frozen_manifest(tmp_path, stream="solutions", channel="solutions/jan")
        assert not loaded.manifest.is_frozen("intro")
        assert loaded.adopted_legacy is None
        assert loaded.ignored_legacy_channel == "materials/jan"

    def test_per_stream_file_wins_over_a_matching_legacy(self, tmp_path):
        legacy = FrozenManifest(channel="materials/jan")
        legacy.freeze("stale", FrozenRecord("old", "t", "sha256:d"))
        legacy.save(tmp_path / FROZEN_FILENAME)
        current = FrozenManifest(channel="materials/jan")
        current.freeze("intro", FrozenRecord("new", "t", "sha256:d"))
        current.save(tmp_path / ".clm-released.materials.json")

        loaded = load_frozen_manifest(tmp_path, stream="materials", channel="materials/jan")
        assert loaded.manifest.is_frozen("intro")
        assert not loaded.manifest.is_frozen("stale")
        assert loaded.adopted_legacy is None


# ---------------------------------------------------------------------------
# sync: presence-as-frozen skeleton + topic overlap
# ---------------------------------------------------------------------------

TOPIC_PATH = "Sec/01 Intro.ipynb"
SKELETON_PATH = "shared/data.csv"


def _manifest(*, topic_path=TOPIC_PATH, skeleton_text="skeleton v1", topic_hash="sha256:a"):
    return {
        "version": 1,
        "source_commit": "abc",
        "files": [
            {"path": topic_path, "topic_id": "intro", "content_hash": topic_hash},
            {"path": SKELETON_PATH, "topic_id": None, "content_hash": _sha(skeleton_text)},
        ],
    }


def _materialize(root: Path, manifest, *, skeleton_text="skeleton v1"):
    for entry in manifest["files"]:
        path = root / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        text = skeleton_text if entry["topic_id"] is None else entry["content_hash"]
        path.write_text(text, encoding="utf-8")
    return root


class TestSkeletonPresenceAsFrozen:
    def test_scan_classifies_missing_and_present_and_skips_vcs(self, tmp_path):
        manifest = _manifest()
        manifest["files"].append(
            {"path": ".git/index", "topic_id": None, "content_hash": "sha256:x"}
        )
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / SKELETON_PATH).parent.mkdir(parents=True)
        (dest / SKELETON_PATH).write_text("already here", encoding="utf-8")

        scan = scan_skeleton(manifest=manifest, dest_root=dest)
        assert scan.present == (SKELETON_PATH,)
        assert scan.missing == ()

        empty = scan_skeleton(manifest=manifest, dest_root=tmp_path / "fresh")
        assert empty.missing == (SKELETON_PATH,)
        assert empty.present == ()

    def test_apply_keeps_present_skeleton_files_and_copies_missing(self, tmp_path):
        manifest = _manifest(skeleton_text="newer build")
        source = _materialize(tmp_path / "src", manifest, skeleton_text="newer build")
        dest = tmp_path / "dest"
        (dest / SKELETON_PATH).parent.mkdir(parents=True)
        (dest / SKELETON_PATH).write_text("first stream's bytes", encoding="utf-8")
        frozen = FrozenManifest(channel="solutions/jan")

        scan = scan_skeleton(manifest=manifest, dest_root=dest)
        plan = plan_sync(manifest=manifest, ledger_released=["intro"], frozen=frozen, skeleton=scan)
        assert plan.copy_skeleton is True
        assert plan.skeleton_to_copy == ()
        assert plan.skeleton_file_count == 0
        assert plan.skeleton_present_count == 1

        result = apply_sync(
            plan=plan,
            manifest=manifest,
            source_root=source,
            dest_root=dest,
            frozen=frozen,
            copied_at="t1",
        )
        # The present skeleton file is kept verbatim; the topic still copies.
        assert (dest / SKELETON_PATH).read_text(encoding="utf-8") == "first stream's bytes"
        assert (dest / TOPIC_PATH).is_file()
        assert frozen.skeleton_frozen is True
        assert result.files_copied == 1

    def test_without_a_scan_the_full_skeleton_copies_as_before(self, tmp_path):
        manifest = _manifest()
        source = _materialize(tmp_path / "src", manifest)
        dest = tmp_path / "dest"
        frozen = FrozenManifest(channel="jan")
        plan = plan_sync(manifest=manifest, ledger_released=[], frozen=frozen)
        assert plan.skeleton_to_copy is None
        apply_sync(
            plan=plan,
            manifest=manifest,
            source_root=source,
            dest_root=dest,
            frozen=frozen,
            copied_at="t1",
        )
        assert (dest / SKELETON_PATH).is_file()

    def test_stale_present_evergreen_file_refreshes_on_a_first_sync(self, tmp_path):
        """A present-but-outdated evergreen file is not trapped by
        presence-as-frozen: the refresh pass updates it even while this
        stream's first sync copies the rest of the skeleton."""
        manifest = _manifest(skeleton_text="news v2")
        source = _materialize(tmp_path / "src", manifest, skeleton_text="news v2")
        dest = tmp_path / "dest"
        (dest / SKELETON_PATH).parent.mkdir(parents=True)
        (dest / SKELETON_PATH).write_text("news v1", encoding="utf-8")
        frozen = FrozenManifest(channel="solutions/jan")

        evergreen = scan_evergreen(manifest=manifest, patterns=[SKELETON_PATH], dest_root=dest)
        skeleton = scan_skeleton(manifest=manifest, dest_root=dest)
        plan = plan_sync(
            manifest=manifest,
            ledger_released=[],
            frozen=frozen,
            evergreen=evergreen.plans,
            skeleton=skeleton,
        )
        assert [p.path for p in plan.evergreen_refresh] == [SKELETON_PATH]

        result = apply_sync(
            plan=plan,
            manifest=manifest,
            source_root=source,
            dest_root=dest,
            frozen=frozen,
            copied_at="t1",
        )
        assert result.refreshed_files == (SKELETON_PATH,)
        assert (dest / SKELETON_PATH).read_text(encoding="utf-8") == "news v2"

    def test_full_first_sync_still_satisfies_evergreen_via_the_skeleton_copy(self, tmp_path):
        manifest = _manifest()
        source = _materialize(tmp_path / "src", manifest)
        dest = tmp_path / "dest"
        frozen = FrozenManifest(channel="jan")
        evergreen = scan_evergreen(manifest=manifest, patterns=[SKELETON_PATH], dest_root=dest)
        skeleton = scan_skeleton(manifest=manifest, dest_root=dest)
        plan = plan_sync(
            manifest=manifest,
            ledger_released=[],
            frozen=frozen,
            evergreen=evergreen.plans,
            skeleton=skeleton,
        )
        # The missing evergreen file arrives with the skeleton copy itself.
        assert plan.evergreen_refresh == ()
        result = apply_sync(
            plan=plan,
            manifest=manifest,
            source_root=source,
            dest_root=dest,
            frozen=frozen,
            copied_at="t1",
        )
        assert result.refreshed_files == ()
        assert (dest / SKELETON_PATH).is_file()


class TestTopicPathOverlap:
    def test_disjoint_topic_paths_have_no_overlap(self):
        a = _manifest(topic_path="Sec/01 Intro-CodeAlong.ipynb")
        b = _manifest(topic_path="Sec/01 Intro-Completed.ipynb")
        overlap = topic_path_overlap(a, b)
        assert overlap.conflicting == ()
        assert overlap.identical == ()

    def test_hash_differing_shared_path_conflicts_and_skeleton_is_ignored(self):
        a = _manifest(topic_hash="sha256:a")
        b = _manifest(topic_hash="sha256:b")
        overlap = topic_path_overlap(a, b)
        assert overlap.conflicting == (TOPIC_PATH,)
        assert overlap.identical == ()

    def test_byte_identical_shared_path_is_allowed(self):
        """A topic's static files (project scaffolding, data) are built
        verbatim into every target — identical bytes may be claimed by both
        streams; copy order does not matter."""
        overlap = topic_path_overlap(_manifest(), _manifest())
        assert overlap.conflicting == ()
        assert overlap.identical == (TOPIC_PATH,)

    def test_missing_hash_cannot_prove_identity_so_it_conflicts(self):
        a = _manifest(topic_hash=None)
        b = _manifest(topic_hash=None)
        overlap = topic_path_overlap(a, b)
        assert overlap.conflicting == (TOPIC_PATH,)
        assert overlap.identical == ()


# ---------------------------------------------------------------------------
# CLI integration: two streams, one destination
# ---------------------------------------------------------------------------

SPEC_SHARED_DEST = """
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
    <channel name="2026-04" path="release/combined/2026-04" ledger="release/materials-2026-04.txt"/>
  </release-channels>
  <release-channels name="solutions" source-target="completed">
    <channel name="2026-04" path="release/combined/2026-04" ledger="release/solutions-2026-04.txt"/>
  </release-channels>
</course>
""".strip()

CODE_ALONG_PATH = "Sec/01 Intro-CodeAlong.ipynb"
COMPLETED_PATH = "Sec/01 Intro-Completed.ipynb"


def _write_spec(tmp_path: Path, body: str = SPEC_SHARED_DEST) -> Path:
    specs_dir = tmp_path / "course-specs"
    specs_dir.mkdir(exist_ok=True)
    spec_file = specs_dir / "course.xml"
    spec_file.write_text(body, encoding="utf-8")
    return spec_file


def _write_built_source(
    root: Path,
    *,
    topic_path: str,
    skeleton_text: str = "skeleton v1",
    topic_hash: str = "sha256:a",
):
    root.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(topic_path=topic_path, skeleton_text=skeleton_text, topic_hash=topic_hash)
    (root / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    _materialize(root, manifest, skeleton_text=skeleton_text)


def _release_and_sync(runner, spec_file, channel):
    add = runner.invoke(release_group, ["add", str(spec_file), "intro", "--channel", channel])
    assert add.exit_code == 0, add.output
    sync = runner.invoke(release_group, ["sync", str(spec_file), "--channel", channel])
    return sync


class TestSharedDestinationCli:
    def test_two_streams_release_into_one_repo(self, tmp_path):
        """The keystone scenario of issue #325: materials and solutions land in
        the same cohort working tree, each gated by its own ledger and frozen
        in its own per-stream manifest."""
        runner = CliRunner()
        spec_file = _write_spec(tmp_path)
        _write_built_source(tmp_path / "output" / "shared", topic_path=CODE_ALONG_PATH)
        _write_built_source(tmp_path / "output" / "completed", topic_path=COMPLETED_PATH)
        dest = tmp_path / "release" / "combined" / "2026-04"

        sync1 = _release_and_sync(runner, spec_file, "materials/2026-04")
        assert sync1.exit_code == 0, sync1.output
        assert (dest / CODE_ALONG_PATH).is_file()
        assert not (dest / COMPLETED_PATH).exists()  # own ledger gates it

        sync2 = _release_and_sync(runner, spec_file, "solutions/2026-04")
        assert sync2.exit_code == 0, sync2.output
        assert (dest / COMPLETED_PATH).is_file()
        assert (dest / CODE_ALONG_PATH).is_file()  # untouched

        materials = FrozenManifest.load(dest / frozen_manifest_filename("materials"), channel="?")
        solutions = FrozenManifest.load(dest / frozen_manifest_filename("solutions"), channel="?")
        assert materials.channel == "materials/2026-04"
        assert solutions.channel == "solutions/2026-04"
        # The core #325 fix: materials freezing 'intro' did NOT freeze it for
        # solutions — each stream froze its own copy.
        assert materials.is_frozen("intro")
        assert solutions.is_frozen("intro")
        assert not (dest / FROZEN_FILENAME).exists()

    def test_second_stream_keeps_the_first_streams_skeleton(self, tmp_path):
        runner = CliRunner()
        spec_file = _write_spec(tmp_path)
        _write_built_source(
            tmp_path / "output" / "shared", topic_path=CODE_ALONG_PATH, skeleton_text="v1"
        )
        # Solutions built later, from a newer source: its skeleton differs.
        _write_built_source(
            tmp_path / "output" / "completed", topic_path=COMPLETED_PATH, skeleton_text="v2"
        )
        dest = tmp_path / "release" / "combined" / "2026-04"

        assert _release_and_sync(runner, spec_file, "materials/2026-04").exit_code == 0
        sync2 = _release_and_sync(runner, spec_file, "solutions/2026-04")
        assert sync2.exit_code == 0, sync2.output

        # Presence-as-frozen: the cohort keeps the skeleton it was given.
        assert (dest / SKELETON_PATH).read_text(encoding="utf-8") == "v1"
        assert "already present (kept)" in sync2.output
        solutions = FrozenManifest.load(dest / frozen_manifest_filename("solutions"), channel="?")
        assert solutions.skeleton_frozen is True

    def test_conflicting_topic_outputs_are_refused(self, tmp_path):
        runner = CliRunner()
        spec_file = _write_spec(tmp_path)
        # Both targets claim the same topic path with DIFFERENT content
        # (e.g. colliding kinds, or targets built from different states).
        _write_built_source(
            tmp_path / "output" / "shared", topic_path="Sec/01 Intro.ipynb", topic_hash="sha256:a"
        )
        _write_built_source(
            tmp_path / "output" / "completed",
            topic_path="Sec/01 Intro.ipynb",
            topic_hash="sha256:b",
        )

        sync = _release_and_sync(runner, spec_file, "materials/2026-04")
        assert sync.exit_code != 0
        assert "Refusing to sync" in sync.output
        assert "Sec/01 Intro.ipynb" in sync.output
        # Nothing was promoted.
        assert not (tmp_path / "release" / "combined" / "2026-04").exists()

    def test_byte_identical_shared_static_files_sync_with_a_note(self, tmp_path):
        """A topic's static files (e.g. project scaffolding) are built
        verbatim into both targets — identical bytes must not block the sync
        (the real AZAV deployment shares 100+ such files)."""
        runner = CliRunner()
        spec_file = _write_spec(tmp_path)
        _write_built_source(
            tmp_path / "output" / "shared", topic_path="Projekte/scaffold.py", topic_hash="sha256:s"
        )
        _write_built_source(
            tmp_path / "output" / "completed",
            topic_path="Projekte/scaffold.py",
            topic_hash="sha256:s",
        )

        sync = _release_and_sync(runner, spec_file, "materials/2026-04")
        assert sync.exit_code == 0, sync.output
        assert "byte-identically" in sync.output
        dest = tmp_path / "release" / "combined" / "2026-04"
        assert (dest / "Projekte/scaffold.py").is_file()

    def test_unbuilt_sharer_is_noted_and_skipped(self, tmp_path):
        runner = CliRunner()
        spec_file = _write_spec(tmp_path)
        _write_built_source(tmp_path / "output" / "shared", topic_path=CODE_ALONG_PATH)
        # output/completed is not built: no manifest to preflight against.

        sync = _release_and_sync(runner, spec_file, "materials/2026-04")
        assert sync.exit_code == 0, sync.output
        assert "not built" in sync.output

    def test_legacy_frozen_manifest_is_adopted_and_migrated(self, tmp_path):
        runner = CliRunner()
        spec_file = _write_spec(tmp_path)
        _write_built_source(tmp_path / "output" / "shared", topic_path=CODE_ALONG_PATH)
        dest = tmp_path / "release" / "combined" / "2026-04"
        dest.mkdir(parents=True)
        legacy = FrozenManifest(channel="materials/2026-04", skeleton_frozen=True)
        legacy.freeze("intro", FrozenRecord("old", "t", "sha256:d"))
        legacy.save(dest / FROZEN_FILENAME)

        sync = _release_and_sync(runner, spec_file, "materials/2026-04")
        assert sync.exit_code == 0, sync.output
        # The freeze survived the adoption: the topic was NOT re-copied.
        assert "skip-frozen" in sync.output
        assert not (dest / CODE_ALONG_PATH).exists()
        assert "Migrated the legacy" in sync.output
        assert not (dest / FROZEN_FILENAME).exists()
        migrated = FrozenManifest.load(dest / frozen_manifest_filename("materials"), channel="?")
        assert migrated.is_frozen("intro")
        assert migrated.frozen["intro"].source_commit == "old"

    def test_dry_run_does_not_migrate_the_legacy_manifest(self, tmp_path):
        runner = CliRunner()
        spec_file = _write_spec(tmp_path)
        _write_built_source(tmp_path / "output" / "shared", topic_path=CODE_ALONG_PATH)
        dest = tmp_path / "release" / "combined" / "2026-04"
        dest.mkdir(parents=True)
        FrozenManifest(channel="materials/2026-04").save(dest / FROZEN_FILENAME)

        add = runner.invoke(
            release_group, ["add", str(spec_file), "intro", "--channel", "materials/2026-04"]
        )
        assert add.exit_code == 0
        sync = runner.invoke(
            release_group,
            ["sync", str(spec_file), "--channel", "materials/2026-04", "--dry-run"],
        )
        assert sync.exit_code == 0, sync.output
        assert (dest / FROZEN_FILENAME).exists()
        assert not (dest / frozen_manifest_filename("materials")).exists()

    def test_another_streams_legacy_manifest_is_left_alone(self, tmp_path):
        runner = CliRunner()
        spec_file = _write_spec(tmp_path)
        _write_built_source(tmp_path / "output" / "shared", topic_path=CODE_ALONG_PATH)
        _write_built_source(tmp_path / "output" / "completed", topic_path=COMPLETED_PATH)
        dest = tmp_path / "release" / "combined" / "2026-04"
        dest.mkdir(parents=True)
        legacy = FrozenManifest(channel="materials/2026-04", skeleton_frozen=True)
        legacy.freeze("intro", FrozenRecord("old", "t", "sha256:d"))
        legacy.save(dest / FROZEN_FILENAME)

        sync = _release_and_sync(runner, spec_file, "solutions/2026-04")
        assert sync.exit_code == 0, sync.output
        assert "leaving the legacy" in sync.output
        # Materials' freeze does not gate solutions: its copy still promotes.
        assert (dest / COMPLETED_PATH).is_file()
        assert (dest / FROZEN_FILENAME).exists()  # untouched
        solutions = FrozenManifest.load(dest / frozen_manifest_filename("solutions"), channel="?")
        assert solutions.is_frozen("intro")

    def test_status_reads_the_per_stream_manifest(self, tmp_path):
        runner = CliRunner()
        spec_file = _write_spec(tmp_path)
        _write_built_source(tmp_path / "output" / "shared", topic_path=CODE_ALONG_PATH)
        _write_built_source(tmp_path / "output" / "completed", topic_path=COMPLETED_PATH)
        assert _release_and_sync(runner, spec_file, "materials/2026-04").exit_code == 0

        status = runner.invoke(
            release_group, ["status", str(spec_file), "--channel", "materials/2026-04"]
        )
        assert status.exit_code == 0, status.output
        assert "1 frozen" in status.output

        # The other stream sharing the destination sees ITS empty state.
        other = runner.invoke(
            release_group, ["status", str(spec_file), "--channel", "solutions/2026-04"]
        )
        assert other.exit_code == 0, other.output
        assert "0 frozen" in other.output


# ---------------------------------------------------------------------------
# clm git: a shared destination is one repo
# ---------------------------------------------------------------------------


class TestGitSharedRepoDedupe:
    def test_channels_sharing_a_path_collapse_to_one_repo(self, tmp_path):
        materials = OutputRepo(
            path=tmp_path / "combined",
            target_name="materials/2026-04",
            language="",
            remote_url="https://gitlab.example.com/ca/ml-2026-04-materials",
            source="channel",
        )
        solutions = OutputRepo(
            path=tmp_path / "combined",
            target_name="solutions/2026-04",
            language="",
            remote_url="https://gitlab.example.com/ca/ml-2026-04-solutions",
            source="channel",
        )
        deduped = _dedupe_shared_destinations([materials, solutions])
        assert len(deduped) == 1
        repo = deduped[0]
        assert repo.target_name == "materials/2026-04"
        assert repo.shared_refs == ["solutions/2026-04"]
        assert "solutions/2026-04" in repo.display_name
        # First stream's derivation wins.
        assert repo.remote_url == "https://gitlab.example.com/ca/ml-2026-04-materials"

    def test_distinct_destinations_stay_separate(self, tmp_path):
        a = OutputRepo(path=tmp_path / "a", target_name="x", language="", source="channel")
        b = OutputRepo(path=tmp_path / "b", target_name="y", language="", source="channel")
        assert len(_dedupe_shared_destinations([a, b])) == 2
