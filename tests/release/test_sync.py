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


def test_sync_refuses_vcs_paths_from_a_polluted_manifest(tmp_path, caplog):
    """Defense in depth (issue #302): a manifest from an older build that
    walked a stray ``.git`` into the skeleton must never overwrite the
    destination repo's own ``.git``."""
    manifest = _manifest()
    manifest["files"] += [
        {
            "path": ".git/index",
            "topic_id": None,
            "section_id": None,
            "kind": None,
            "format": "dir-group",
            "language": "de",
            "content_hash": "sha256:ddd",
        },
        {
            "path": "Sec/.svn/entries",
            "topic_id": "intro",
            "section_id": "w01",
            "kind": None,
            "format": "dir-group",
            "language": "en",
            "content_hash": "sha256:eee",
        },
    ]
    source = _materialize_source(tmp_path, manifest)
    dest = tmp_path / "jan"
    (dest / ".git").mkdir(parents=True)
    (dest / ".git" / "index").write_bytes(b"REAL")
    frozen = FrozenManifest(channel="jan")

    plan = plan_sync(manifest=manifest, ledger_released=["intro"], frozen=frozen)
    result = apply_sync(
        plan=plan,
        manifest=manifest,
        source_root=source,
        dest_root=dest,
        frozen=frozen,
        copied_at="t1",
    )

    # The legitimate skeleton + topic files were copied; the VCS entries were not.
    assert (dest / INTRO_PATH).is_file()
    assert (dest / SKELETON_PATH).is_file()
    assert not (dest / "Sec" / ".svn").exists()
    assert (dest / ".git" / "index").read_bytes() == b"REAL"
    assert result.files_copied == 2
    assert "refused to copy 1 VCS metadata file" in caplog.text


# ---------------------------------------------------------------------------
# Failed topics in a partial manifest (issue #295)
# ---------------------------------------------------------------------------


def _partial_manifest() -> dict:
    return {
        "source_commit": "abc",
        "partial": True,
        "failed_topics": ["flaky"],
        "files": [
            {"path": "Sec/01 Good.ipynb", "topic_id": "good", "content_hash": "sha256:a"},
            # Stale leftovers from a previous build of the failed topic must
            # not appear in a partial manifest, but even if they did, the plan
            # gate (not the file list) is what refuses promotion.
        ],
    }


def test_plan_refuses_released_topics_that_failed_in_the_source_build():
    plan = plan_sync(
        manifest=_partial_manifest(),
        ledger_released=["good", "flaky"],
        frozen=FrozenManifest(channel="jan"),
    )
    actions = {t.topic_id: t.action for t in plan.topics}
    assert actions == {"good": "copy", "flaky": "skip-failed"}
    assert [t.topic_id for t in plan.failed] == ["flaky"]
    assert [t.topic_id for t in plan.to_copy] == ["good"]


def test_already_frozen_failed_topic_stays_an_ordinary_frozen_skip():
    frozen = FrozenManifest(channel="jan")
    frozen.freeze("flaky", FrozenRecord(source_commit="old", copied_at="t", topic_digest="d"))
    plan = plan_sync(
        manifest=_partial_manifest(),
        ledger_released=["flaky"],
        frozen=frozen,
    )
    assert plan.topics[0].action == "skip-frozen"


def test_refreeze_of_a_failed_topic_is_refused():
    frozen = FrozenManifest(channel="jan")
    frozen.freeze("flaky", FrozenRecord(source_commit="old", copied_at="t", topic_digest="d"))
    plan = plan_sync(
        manifest=_partial_manifest(),
        ledger_released=["flaky"],
        frozen=frozen,
        refreeze={"flaky"},
    )
    assert plan.topics[0].action == "skip-failed"


def test_apply_skips_failed_topics_without_freezing(tmp_path):
    source = tmp_path / "src"
    (source / "Sec").mkdir(parents=True)
    (source / "Sec" / "01 Good.ipynb").write_text("good", encoding="utf-8")
    dest = tmp_path / "dest"
    frozen = FrozenManifest(channel="jan")

    manifest = _partial_manifest()
    plan = plan_sync(manifest=manifest, ledger_released=["good", "flaky"], frozen=frozen)
    result = apply_sync(
        plan=plan,
        manifest=manifest,
        source_root=source,
        dest_root=dest,
        frozen=frozen,
        copied_at="2026-06-10T00:00:00Z",
    )

    assert result.copied_topics == ("good",)
    assert result.failed_topics == ("flaky",)
    assert frozen.is_frozen("good")
    assert not frozen.is_frozen("flaky")
