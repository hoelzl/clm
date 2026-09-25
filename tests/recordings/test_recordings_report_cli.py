"""``clm recordings report`` / ``clm recordings ack`` — the CLI surface (#965).

The engine is covered in ``test_report.py``; these tests pin the contract an
agent scripts against: the JSON envelope on stdout only, diagnostics on
stderr, and the load-bearing exit codes (0 clean / 1 attention / 2 error).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.recordings import recordings_group
from clm.recordings import ledger as rl
from tests.recordings.test_ledger import DE0, EN0

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env={**os.environ, **_GIT_ENV},
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def course(tmp_path: Path) -> tuple[Path, Path]:
    """A git course root with one recorded split deck; returns (root, de_path)."""
    root = tmp_path / "course"
    topic = root / "slides" / "module_100" / "topic_010_t"
    topic.mkdir(parents=True)
    _git(root, "init", "-q")
    de = topic / "slides_t.de.py"
    de.write_text(DE0, encoding="utf-8")
    (topic / "slides_t.en.py").write_text(EN0, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "initial")
    members = rl.deck_members(de, "de")
    rl.record_part(
        rl.ledger_path_for(de),
        "slides_t",
        rl.LedgerPart(
            part=1,
            recorded_at="2026-09-25T10:00:00",
            course_id="c-de",
            lang="de",
            anchor=rl.anchor_for(_git(root, "rev-parse", "HEAD"), False),
            members=members,
            order=rl.id_order(members),
        ),
    )
    return root, de


def _edit(de: Path) -> None:
    de.write_text(DE0.replace("DE eins", "DE eins, neu"), encoding="utf-8")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def test_report_clean_exits_zero(course):
    root, _ = course
    result = CliRunner().invoke(recordings_group, ["report", str(root)])
    assert result.exit_code == 0, result.output
    assert "Re-recording backlog" in result.output
    assert "none" in result.output


def test_report_json_is_the_envelope_on_stdout_with_exit_one(course):
    root, de = course
    _edit(de)
    result = CliRunner().invoke(recordings_group, ["report", str(root), "--json"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert (payload["schema"], payload["tool"], payload["verb"]) == (1, "recordings", "report")
    assert payload["is_clean"] is False
    [deck] = payload["decks"]
    assert deck["deck"] == "slides_t"
    assert deck["severity"] == "visible"
    assert deck["ack_state"] == "unacknowledged"
    assert deck["parts"][0]["changed_members"] == {"id:m1": "visible"}
    assert deck["parts"][0]["commits_since_anchor"] == []


def test_report_all_lists_unrecorded_decks(course):
    root, _ = course
    other = root / "slides" / "module_100" / "topic_020_u"
    other.mkdir()
    (other / "slides_u.de.py").write_text(DE0, encoding="utf-8")
    (other / "slides_u.en.py").write_text(EN0, encoding="utf-8")

    without = json.loads(
        CliRunner().invoke(recordings_group, ["report", str(root), "--json"]).stdout
    )
    assert [d["deck"] for d in without["decks"]] == ["slides_t"]
    result = CliRunner().invoke(recordings_group, ["report", str(root), "--all", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [(d["deck"], d["status"]) for d in payload["decks"]] == [
        ("slides_t", "recorded"),
        ("slides_u", "unrecorded"),
    ]
    assert payload["counts"] == {"none": 1, "unrecorded": 1}


def test_report_orphaned_deck_needs_attention(course):
    root, de = course
    de.unlink()
    de.with_name("slides_t.en.py").unlink()
    result = CliRunner().invoke(recordings_group, ["report", str(root), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["decks"][0]["status"] == "orphaned"
    assert payload["needs_attention"] == ["slides/module_100/topic_010_t/slides_t"]


def test_report_malformed_ledger_is_exit_two_with_stderr_diagnostic(course):
    root, de = course
    rl.ledger_path_for(de).write_text("{broken", encoding="utf-8")
    result = CliRunner().invoke(recordings_group, ["report", str(root), "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["decks"] == []
    assert len(payload["ledger_errors"]) == 1
    assert "not valid JSON" in result.stderr


def test_report_without_ledgers_is_clean(tmp_path: Path):
    result = CliRunner().invoke(recordings_group, ["report", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "No recorded decks" in result.output


def test_report_with_manifest_adds_the_secondary_flag(course, monkeypatch):
    from clm.core.provenance_manifest import MANIFEST_FILENAME
    from clm.recordings.state import CourseRecordingState, LectureState, RecordingPart

    root, _ = course
    files = [{"path": "a", "topic_id": "t", "content_hash": "sha256:a"}]
    manifest = root / "output" / MANIFEST_FILENAME
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"files": files}), encoding="utf-8")
    state = CourseRecordingState(
        course_id="c-de",
        lectures=[
            LectureState(
                lecture_id="S::D",
                display_name="D",
                parts=[
                    RecordingPart(
                        part=1,
                        raw_file="r.mkv",
                        recorded_at="2026-09-25T10:00:00",
                        topic_id="t",
                        slide_digest="stale",
                    )
                ],
            )
        ],
    )
    monkeypatch.setattr("clm.recordings.state.load_state", lambda course_id: state)

    result = CliRunner().invoke(
        recordings_group, ["report", str(root), "--manifest", str(manifest), "--json"]
    )
    assert result.exit_code == 0, result.output  # the flag never decides attention
    payload = json.loads(result.stdout)
    assert payload["decks"][0]["parts"][0]["built_output_changed"] is True

    text = CliRunner().invoke(
        recordings_group, ["report", str(root), "--source", str(manifest.parent)]
    )
    assert "built output changed" in text.output


def test_drift_verb_is_gone():
    result = CliRunner().invoke(recordings_group, ["drift", "c1"])
    assert result.exit_code == 2
    assert "No such command" in result.output


# ---------------------------------------------------------------------------
# ack
# ---------------------------------------------------------------------------


def test_ack_then_report_is_acknowledged(course):
    root, de = course
    _edit(de)
    runner = CliRunner()
    result = runner.invoke(recordings_group, ["ack", str(de), "--note", "reword only"])
    assert result.exit_code == 0, result.output
    assert "Acknowledged" in result.output

    payload = json.loads(runner.invoke(recordings_group, ["report", str(root), "--json"]).stdout)
    [deck] = payload["decks"]
    assert deck["ack_state"] == "acknowledged"
    assert deck["ack_note"] == "reword only"
    assert deck["needs_attention"] is False
    assert payload["is_clean"] is True

    # Moving further re-surfaces the deck with the drift since the ack.
    de.write_text(
        DE0.replace("DE eins", "DE eins, neu").replace("x = 1", "x = 2"), encoding="utf-8"
    )
    result = runner.invoke(recordings_group, ["report", str(root), "--json"])
    assert result.exit_code == 1
    deck = json.loads(result.stdout)["decks"][0]
    assert deck["ack_state"] == "drifted-since-ack"
    assert deck["severity_since_ack"] == "structural"


def test_ack_json(course):
    root, de = course
    result = CliRunner().invoke(recordings_group, ["ack", str(de), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert (payload["schema"], payload["tool"], payload["verb"]) == (1, "recordings", "ack")
    assert payload["deck"] == "slides_t"
    assert payload["langs"] == ["de"]
    assert payload["written"] is True


def test_ack_without_recording_is_exit_two(tmp_path: Path):
    topic = tmp_path / "t"
    topic.mkdir()
    de = topic / "slides_x.de.py"
    de.write_text(DE0, encoding="utf-8")
    (topic / "slides_x.en.py").write_text(EN0, encoding="utf-8")
    result = CliRunner().invoke(recordings_group, ["ack", str(de), "--json"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "no recorded part" in result.stderr


def test_ack_warns_when_the_ledger_is_gitignored(course):
    root, de = course
    (root / ".gitignore").write_text("**/.clm/*\n", encoding="utf-8")
    result = CliRunner().invoke(recordings_group, ["ack", str(de), "--json"])
    assert result.exit_code == 0, result.output
    assert "recordings-ledger.json" in result.stderr
    assert "!**/.clm/recordings-ledger.json" in result.stderr
