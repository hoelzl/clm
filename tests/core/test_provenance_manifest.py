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
    manifest_topic_digest,
    topic_digest_from_files,
    write_provenance_manifests,
)

BUILT_AT = "2026-06-03T00:00:00+00:00"


def test_enumerate_yields_outputs_with_topic_ownership(course_1):
    target = course_1.output_targets[0]
    expected = list(enumerate_expected_outputs(course_1, target))
    assert expected, "course_1 should enumerate outputs"
    saw_notebook = False
    # Every record carries topic ownership; notebooks carry a real
    # (format, kind), while copied assets (data/image/dir-group) carry an
    # asset format and kind=None.
    for _path, record in expected:
        assert record["topic_id"]
        assert record["language"] in {"de", "en"}
        if record["format"] in {"data", "image", "dir-group"}:
            assert record["kind"] is None
        else:
            assert record["format"] in {"html", "notebook", "code"}
            assert record["kind"]
            saw_notebook = True
    assert saw_notebook


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


def _course_from_test_spec_1(out_root):
    """Build a Course from the on-disk test-spec-1 fixture, which (unlike the
    minimal inline ``course_1``) contains a real DataFile asset."""
    from pathlib import Path

    from clm.core.course import Course
    from clm.core.course_spec import CourseSpec

    test_data = Path(__file__).parent.parent / "test-data"
    spec = CourseSpec.from_file(test_data / "course-specs" / "test-spec-1.xml")
    return Course.from_spec(spec, test_data, out_root)


def test_manifest_records_data_file_assets(tmp_path):
    course = _course_from_test_spec_1(tmp_path / "out")
    target = course.output_targets[0]

    data_records = [
        (p, r) for p, r in enumerate_expected_outputs(course, target) if r["format"] == "data"
    ]
    assert data_records, "test-spec-1 has a DataFile asset"

    # Materialize one of the data-asset outputs; the manifest must record it
    # with topic ownership and a null kind.
    out_path, record = data_records[0]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("payload\n", encoding="utf-8")

    manifest = build_provenance_manifest(
        course,
        target,
        source_commit=None,
        source_dirty=None,
        built_at=BUILT_AT,
    )
    data_entries = [f for f in manifest["files"] if f["format"] == "data"]
    assert len(data_entries) == 1
    entry = data_entries[0]
    assert entry["topic_id"] == record["topic_id"]
    assert entry["kind"] is None
    assert entry["content_hash"].startswith("sha256:")


def test_manifest_records_duplicated_image_assets(tmp_path):
    course = _course_from_test_spec_1(tmp_path / "out")
    target = course.output_targets[0]

    image_records = [
        (p, r) for p, r in enumerate_expected_outputs(course, target) if r["format"] == "image"
    ]
    assert image_records, "test-spec-1 has duplicated image assets"

    out_path, record = image_records[0]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    manifest = build_provenance_manifest(
        course, target, source_commit=None, source_dirty=None, built_at=BUILT_AT
    )
    image_entries = [f for f in manifest["files"] if f["format"] == "image"]
    assert len(image_entries) == 1
    assert image_entries[0]["topic_id"] == record["topic_id"]
    assert image_entries[0]["kind"] is None
    assert image_entries[0]["content_hash"].startswith("sha256:")


def test_manifest_records_dir_group_outputs_with_ownership(tmp_path):
    course = _course_from_test_spec_1(tmp_path / "out")
    target = course.output_targets[0]

    # The topic-scoped dir-group is owned by a topic; place a file in one of
    # its output directories and assert the manifest records it with that
    # ownership.
    owned = [dg for dg in course.dir_groups if dg.spec is not None and dg.spec.topic_id]
    assert owned, "test-spec-1 has a topic-scoped dir-group"
    dir_group = owned[0]

    out_dirs = dir_group.output_dirs(
        False, "de", target.output_root, skip_toplevel=target.is_explicit
    )
    assert out_dirs, "dir-group should resolve at least one output directory"
    placed_dir = out_dirs[0]
    placed_dir.mkdir(parents=True, exist_ok=True)
    (placed_dir / "example.txt").write_text("hi", encoding="utf-8")

    manifest = build_provenance_manifest(
        course, target, source_commit=None, source_dirty=None, built_at=BUILT_AT
    )
    dg_entries = [f for f in manifest["files"] if f["format"] == "dir-group"]
    entry = next(e for e in dg_entries if e["path"].endswith("example.txt"))
    assert entry["topic_id"] == dir_group.spec.topic_id
    assert entry["section_id"] == dir_group.spec.section_id
    assert entry["kind"] is None
    assert entry["content_hash"].startswith("sha256:")


# ---------------------------------------------------------------------------
# topic_digest_from_files / manifest_topic_digest (issue #208 step 5 join key)
# ---------------------------------------------------------------------------


def _entry(path: str, topic_id: str | None, content_hash: str) -> dict:
    return {"path": path, "topic_id": topic_id, "content_hash": content_hash}


def test_topic_digest_from_files_is_order_independent():
    a = [_entry("a", "t", "sha256:1"), _entry("b", "t", "sha256:2")]
    assert topic_digest_from_files(a) == topic_digest_from_files(list(reversed(a)))


def test_topic_digest_from_files_changes_with_content():
    base = [_entry("a", "t", "sha256:1")]
    changed = [_entry("a", "t", "sha256:2")]
    assert topic_digest_from_files(base) != topic_digest_from_files(changed)
    assert topic_digest_from_files(base).startswith("sha256:")


def test_topic_digest_from_files_is_stable_against_a_frozen_value():
    """Pin the exact rollup output. A change to the algorithm (separators,
    sort, prefix) would silently invalidate every shipped frozen
    ``.clm-released.json`` digest and the digest stamped on recordings — this
    catches that, decoupled from the implementation."""
    files = [_entry("a", "t", "sha256:b"), _entry("b", "t", "sha256:a")]  # unsorted on purpose
    assert (
        topic_digest_from_files(files)
        == "sha256:5d02884ff145014d7bbd2e791dfad6096dbc4208330b699b82742d4b403d1f56"
    )


def test_manifest_topic_digest_absent_topic_is_none():
    manifest = {"files": [_entry("Sec/a.ipynb", "intro", "sha256:a")]}
    assert manifest_topic_digest(manifest, "nope") is None


def test_manifest_topic_digest_matches_files_rollup():
    files = [_entry("Sec/a.ipynb", "intro", "sha256:a"), _entry("Sec/b.ipynb", "intro", "sha256:b")]
    manifest = {"files": [*files, _entry("shared/x", None, "sha256:s")]}
    assert manifest_topic_digest(manifest, "intro") == topic_digest_from_files(files)


def test_manifest_topic_digest_stable_across_manifest_file_order():
    files = [_entry("Sec/a.ipynb", "intro", "sha256:a"), _entry("Sec/b.ipynb", "intro", "sha256:b")]
    m1 = {"files": files}
    m2 = {"files": list(reversed(files))}
    assert manifest_topic_digest(m1, "intro") == manifest_topic_digest(m2, "intro")
