"""Tests for the release sync/promote algorithm (issue #208, step 2)."""

from clm.release.frozen_manifest import FrozenManifest, FrozenRecord
from clm.release.sync import COPY, REFREEZE, SKIP_FROZEN, apply_sync, plan_sync

INTRO_PATH = "En/Course/Notebooks/Completed/Sec/01 Intro.ipynb"
FUNCS_PATH = "En/Course/Notebooks/Completed/Sec/02 Funcs.ipynb"
SKELETON_PATH = "shared/data.csv"


def _manifest(source_commit="abc"):
    return {
        "version": 1,
        "source_commit": source_commit,
        "source_dirty": False,
        "built_at": "t",
        "target": "solutions-source",
        "files": [
            {
                "path": INTRO_PATH,
                "topic_id": "intro",
                "section_id": "w01",
                "kind": "completed",
                "format": "notebook",
                "language": "en",
                "content_hash": "sha256:aaa",
            },
            {
                "path": FUNCS_PATH,
                "topic_id": "functions",
                "section_id": "w01",
                "kind": "completed",
                "format": "notebook",
                "language": "en",
                "content_hash": "sha256:bbb",
            },
            {
                "path": SKELETON_PATH,
                "topic_id": None,
                "section_id": None,
                "kind": None,
                "format": "dir-group",
                "language": "en",
                "content_hash": "sha256:ccc",
            },
        ],
    }


def _materialize_source(tmp_path, manifest):
    source = tmp_path / "src"
    for entry in manifest["files"]:
        p = source / entry["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(entry["content_hash"], encoding="utf-8")
    return source


def test_sync_copies_released_topic_and_skeleton_then_freezes(tmp_path):
    manifest = _manifest()
    source = _materialize_source(tmp_path, manifest)
    dest = tmp_path / "jan"
    frozen = FrozenManifest(channel="jan")

    plan = plan_sync(manifest=manifest, ledger_released=["intro"], frozen=frozen)
    assert plan.copy_skeleton is True
    assert [t.action for t in plan.topics] == [COPY]

    result = apply_sync(
        plan=plan,
        manifest=manifest,
        source_root=source,
        dest_root=dest,
        frozen=frozen,
        copied_at="2026-01-08",
    )

    assert result.copied_topics == ("intro",)
    assert result.skeleton_copied is True
    assert (dest / INTRO_PATH).is_file()
    assert (dest / SKELETON_PATH).is_file()
    # An unreleased topic is never copied.
    assert not (dest / FUNCS_PATH).exists()

    assert frozen.is_frozen("intro")
    assert frozen.skeleton_frozen is True
    assert frozen.frozen["intro"].source_commit == "abc"
    assert frozen.frozen["intro"].topic_digest.startswith("sha256:")


def test_frozen_topic_is_skipped_even_when_source_changed(tmp_path):
    manifest = _manifest(source_commit="new")
    source = _materialize_source(tmp_path, manifest)
    dest = tmp_path / "jan"
    frozen = FrozenManifest(
        channel="jan",
        frozen={"intro": FrozenRecord("old", "2026-01-01", "sha256:old")},
        skeleton_frozen=True,
    )

    plan = plan_sync(manifest=manifest, ledger_released=["intro"], frozen=frozen)
    assert plan.copy_skeleton is False
    assert plan.topics[0].action == SKIP_FROZEN

    result = apply_sync(
        plan=plan,
        manifest=manifest,
        source_root=source,
        dest_root=dest,
        frozen=frozen,
        copied_at="2026-03-01",
    )

    assert result.skipped_topics == ("intro",)
    assert not (dest / INTRO_PATH).exists()  # not re-copied
    # Freeze record untouched -> students keep what they were given.
    assert frozen.frozen["intro"].source_commit == "old"


def test_refreeze_recopies_and_updates_record(tmp_path):
    manifest = _manifest(source_commit="new")
    source = _materialize_source(tmp_path, manifest)
    dest = tmp_path / "jan"
    frozen = FrozenManifest(
        channel="jan",
        frozen={"intro": FrozenRecord("old", "2026-01-01", "sha256:old")},
        skeleton_frozen=True,
    )

    plan = plan_sync(
        manifest=manifest, ledger_released=["intro"], frozen=frozen, refreeze=["intro"]
    )
    assert plan.topics[0].action == REFREEZE

    result = apply_sync(
        plan=plan,
        manifest=manifest,
        source_root=source,
        dest_root=dest,
        frozen=frozen,
        copied_at="2026-03-01",
    )

    assert result.refrozen_topics == ("intro",)
    assert (dest / INTRO_PATH).is_file()
    assert frozen.frozen["intro"].source_commit == "new"
    assert frozen.frozen["intro"].copied_at == "2026-03-01"


def test_sync_is_idempotent_after_first_run(tmp_path):
    manifest = _manifest()
    source = _materialize_source(tmp_path, manifest)
    dest = tmp_path / "jan"
    frozen = FrozenManifest(channel="jan")

    first = plan_sync(manifest=manifest, ledger_released=["intro"], frozen=frozen)
    apply_sync(
        plan=first,
        manifest=manifest,
        source_root=source,
        dest_root=dest,
        frozen=frozen,
        copied_at="t1",
    )

    # Re-planning with the same (now-updated) frozen manifest is a no-op.
    second = plan_sync(manifest=manifest, ledger_released=["intro"], frozen=frozen)
    assert second.copy_skeleton is False
    assert second.topics[0].action == SKIP_FROZEN


def test_released_but_unbuilt_topic_is_not_frozen(tmp_path):
    manifest = _manifest()
    source = _materialize_source(tmp_path, manifest)
    dest = tmp_path / "jan"
    frozen = FrozenManifest(channel="jan")

    plan = plan_sync(manifest=manifest, ledger_released=["ghost"], frozen=frozen)
    # Visible in the plan as a 0-file copy.
    assert plan.topics[0].action == COPY
    assert plan.topics[0].file_count == 0

    result = apply_sync(
        plan=plan,
        manifest=manifest,
        source_root=source,
        dest_root=dest,
        frozen=frozen,
        copied_at="t1",
    )
    assert result.copied_topics == ()
    assert not frozen.is_frozen("ghost")
