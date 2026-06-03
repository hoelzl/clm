"""Tests for the per-channel frozen manifest (issue #208, step 2)."""

import json

from clm.release.frozen_manifest import (
    FROZEN_FILENAME,
    FROZEN_VERSION,
    FrozenManifest,
    FrozenRecord,
)


def test_load_missing_file_is_empty_with_channel(tmp_path):
    fm = FrozenManifest.load(tmp_path / FROZEN_FILENAME, channel="jan")
    assert fm.channel == "jan"
    assert fm.frozen == {}
    assert fm.skeleton_frozen is False


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "jan" / FROZEN_FILENAME
    fm = FrozenManifest(channel="jan", skeleton_frozen=True)
    fm.freeze(
        "functions",
        FrozenRecord(source_commit="abc123", copied_at="2026-01-22", topic_digest="sha256:dd"),
    )
    fm.save(path)

    reloaded = FrozenManifest.load(path, channel="ignored-because-file-wins")
    assert reloaded.channel == "jan"
    assert reloaded.skeleton_frozen is True
    assert reloaded.is_frozen("functions")
    rec = reloaded.frozen["functions"]
    assert rec.source_commit == "abc123"
    assert rec.copied_at == "2026-01-22"
    assert rec.topic_digest == "sha256:dd"


def test_saved_file_is_versioned_and_sorted(tmp_path):
    path = tmp_path / FROZEN_FILENAME
    fm = FrozenManifest(channel="jan")
    fm.freeze("zeta", FrozenRecord(None, "t", "sha256:z"))
    fm.freeze("alpha", FrozenRecord(None, "t", "sha256:a"))
    fm.save(path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == FROZEN_VERSION
    assert list(data["frozen"].keys()) == ["alpha", "zeta"]
