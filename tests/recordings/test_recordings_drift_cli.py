"""Tests for ``clm recordings drift`` (issue #208 follow-up 1b).

Exercises the drift report over a synthetic recording state and an on-disk
provenance manifest, mirroring the drift-query tests in
``tests/recordings/test_provenance.py`` but through the CLI surface.
"""

import json

from click.testing import CliRunner

from clm.cli.commands.recordings import recordings_group
from clm.core.provenance_manifest import MANIFEST_FILENAME, topic_digest_from_files
from clm.recordings.state import CourseRecordingState, LectureState, RecordingPart


def _manifest_for(topic_id, content_hashes):
    files = [
        {"path": f"f{i}", "topic_id": topic_id, "content_hash": h}
        for i, h in enumerate(content_hashes)
    ]
    return {"files": files}, topic_digest_from_files(files)


def _write_manifest(tmp_path, manifest):
    path = tmp_path / MANIFEST_FILENAME
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _state_with_part(topic_id, slide_digest):
    return CourseRecordingState(
        course_id="c1",
        lectures=[
            LectureState(
                lecture_id="Week 1::00 Intro",
                display_name="Intro",
                parts=[
                    RecordingPart(
                        part=1,
                        raw_file="r.mkv",
                        topic_id=topic_id,
                        slide_digest=slide_digest,
                    )
                ],
            )
        ],
    )


def _patch_state(monkeypatch, state):
    monkeypatch.setattr("clm.recordings.state.load_state", lambda course_id: state)


def test_drift_no_state(monkeypatch):
    monkeypatch.setattr("clm.recordings.state.load_state", lambda course_id: None)
    result = CliRunner().invoke(recordings_group, ["drift", "c1"])
    assert result.exit_code == 1
    assert "No recording state" in result.output


def test_drift_reports_changed(tmp_path, monkeypatch):
    manifest, current_digest = _manifest_for("topic-x", ["sha256:new"])
    manifest_path = _write_manifest(tmp_path, manifest)
    # Part was recorded against an older digest -> changed.
    _patch_state(monkeypatch, _state_with_part("topic-x", "sha256:old-rollup"))

    result = CliRunner().invoke(recordings_group, ["drift", "c1", "--manifest", str(manifest_path)])
    assert result.exit_code == 0, result.output
    assert "changed" in result.output
    assert "topic-x" in result.output
    assert current_digest  # sanity


def test_drift_current_only_shown_with_all(tmp_path, monkeypatch):
    manifest, current_digest = _manifest_for("topic-x", ["sha256:a", "sha256:b"])
    manifest_path = _write_manifest(tmp_path, manifest)
    # Part stamped with the exact current digest -> current (up to date).
    _patch_state(monkeypatch, _state_with_part("topic-x", current_digest))

    # Default (stale-only): nothing to report.
    stale = CliRunner().invoke(recordings_group, ["drift", "c1", "--manifest", str(manifest_path)])
    assert stale.exit_code == 0, stale.output
    assert "up to date" in stale.output

    # --all surfaces the current part.
    all_parts = CliRunner().invoke(
        recordings_group, ["drift", "c1", "--manifest", str(manifest_path), "--all"]
    )
    assert all_parts.exit_code == 0, all_parts.output
    assert "current" in all_parts.output


def test_drift_json_output(tmp_path, monkeypatch):
    manifest, _ = _manifest_for("topic-x", ["sha256:new"])
    manifest_path = _write_manifest(tmp_path, manifest)
    _patch_state(monkeypatch, _state_with_part("topic-x", "sha256:old"))

    result = CliRunner().invoke(
        recordings_group, ["drift", "c1", "--manifest", str(manifest_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["course_id"] == "c1"
    assert len(payload["drift"]) == 1
    entry = payload["drift"][0]
    assert entry["topic_id"] == "topic-x"
    assert entry["status"] == "changed"
    assert entry["part"] == 1


def test_drift_unknown_when_topic_absent_from_manifest(tmp_path, monkeypatch):
    manifest, _ = _manifest_for("other-topic", ["sha256:x"])
    manifest_path = _write_manifest(tmp_path, manifest)
    _patch_state(monkeypatch, _state_with_part("topic-x", "sha256:old"))

    # Topic not in manifest -> unknown, never reported stale.
    stale = CliRunner().invoke(recordings_group, ["drift", "c1", "--manifest", str(manifest_path)])
    assert "up to date" in stale.output

    all_parts = CliRunner().invoke(
        recordings_group, ["drift", "c1", "--manifest", str(manifest_path), "--all"]
    )
    assert "unknown" in all_parts.output


def test_drift_no_manifest_resolved(monkeypatch):
    _patch_state(monkeypatch, _state_with_part("topic-x", "sha256:old"))
    # No --manifest/--source/--spec-file and no config course -> error.
    monkeypatch.setattr(
        "clm.cli.commands.recordings._configured_spec_for_course", lambda course_id: None
    )
    result = CliRunner().invoke(recordings_group, ["drift", "c1"])
    assert result.exit_code == 1
    assert "No provenance manifest" in result.output
