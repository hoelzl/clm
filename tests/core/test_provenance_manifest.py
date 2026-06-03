"""Tests for the build provenance manifest (issue #208, step 1).

These exercise the manifest builder against a real ``Course`` (the shared
``course_1`` fixture) without running a build: we discover the output paths
the build *would* write via the same enumeration the manifest uses, then
materialize a subset on disk and assert the manifest records exactly those.
"""

import json

from clm.core.provenance_manifest import (
    MANIFEST_FILENAME,
    MANIFEST_VERSION,
    build_provenance_manifest,
    enumerate_expected_outputs,
    write_provenance_manifests,
)

BUILT_AT = "2026-06-03T00:00:00+00:00"


def test_enumerate_yields_notebook_outputs(course_1):
    target = course_1.output_targets[0]
    expected = list(enumerate_expected_outputs(course_1, target))
    assert expected, "course_1 should enumerate notebook outputs"
    # Every record carries topic ownership and an output triple.
    for _path, record in expected:
        assert record["topic_id"]
        assert record["language"] in {"de", "en"}
        assert record["format"] in {"html", "notebook", "code"}
        assert record["kind"]


def test_manifest_records_only_existing_files(course_1):
    target = course_1.output_targets[0]
    expected = list(enumerate_expected_outputs(course_1, target))

    # Materialize exactly one of the enumerated outputs on disk.
    out_path, record = expected[0]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("print('hello')\n", encoding="utf-8")

    manifest = build_provenance_manifest(
        course_1,
        target,
        source_commit="abc123",
        source_dirty=False,
        built_at=BUILT_AT,
        spec_name="course.xml",
    )

    assert manifest["version"] == MANIFEST_VERSION
    assert manifest["source_commit"] == "abc123"
    assert manifest["source_dirty"] is False
    assert manifest["target"] == target.name

    # The existence filter keeps only the file we created, even though many
    # (lang, format, kind) combinations were enumerated.
    files = manifest["files"]
    assert len(files) == 1
    entry = files[0]
    assert entry["path"] == out_path.relative_to(target.output_root).as_posix()
    assert entry["topic_id"] == record["topic_id"]
    assert entry["section_id"] == record["section_id"]
    assert entry["language"] == record["language"]
    assert entry["format"] == record["format"]
    assert entry["kind"] == record["kind"]
    assert entry["content_hash"].startswith("sha256:")


def test_write_provenance_manifests_writes_one_per_target(course_1):
    target = course_1.output_targets[0]
    target.output_root.mkdir(parents=True, exist_ok=True)

    written = write_provenance_manifests(
        course_1,
        source_commit=None,
        source_dirty=None,
        built_at=BUILT_AT,
        spec_name="course.xml",
    )

    assert len(written) == 1
    manifest_path = written[0]
    assert manifest_path.name == MANIFEST_FILENAME
    assert manifest_path.parent == target.output_root

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["version"] == MANIFEST_VERSION
    assert data["source_commit"] is None
    assert isinstance(data["files"], list)


def test_write_skips_targets_without_output_root(course_1, tmp_path):
    # Point the target at a non-existent root: nothing is written, no error.
    target = course_1.output_targets[0]
    object.__setattr__(target, "output_root", tmp_path / "does-not-exist")
    written = write_provenance_manifests(
        course_1,
        source_commit=None,
        source_dirty=None,
        built_at=BUILT_AT,
    )
    assert written == []
