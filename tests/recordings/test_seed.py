"""``clm recordings seed-ledger`` — ledger entries from a local state file (#1005).

A throwaway git course repo with one split deck; the state file names the
deck the way the dashboard does ("<section name>::<deck name>"). Parts with
a stamped commit anchor there; parts without one anchor on the last commit
before ``recorded_at``; the members are the deck's fingerprints at the
anchor, read from a detached worktree of that commit.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest
from click.testing import CliRunner

from clm.cli.commands.recordings import recordings_group
from clm.recordings import ledger as rl
from clm.recordings.seed import commit_before, lang_of_course_id, seed_from_state
from clm.recordings.state import CourseRecordingState, LectureState, RecordingPart
from tests.recordings.test_ledger import DE0, EN0

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}

SPEC_XML = dedent(
    """\
    <course>
      <name><de>Kurs</de><en>Course</en></name>
      <prog-lang>python</prog-lang>
      <description><de></de><en></en></description>
      <certificate><de></de><en></en></certificate>
      <project-slug>kurs</project-slug>
      <sections>
        <section>
          <name><de>Woche 01: Start</de><en>Week 01: Start</en></name>
          <topics><topic>t</topic></topics>
        </section>
      </sections>
    </course>
    """
)


def _git(repo: Path, *args: str, date: str | None = None) -> str:
    env = {**os.environ, **_GIT_ENV}
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


class _Course:
    def __init__(self, root: Path):
        self.root = root
        self.topic = root / "slides" / "module_100" / "topic_010_t"
        self.topic.mkdir(parents=True)
        (root / "course-specs").mkdir()
        self.spec = root / "course-specs" / "kurs.xml"
        self.spec.write_text(SPEC_XML, encoding="utf-8")
        self.de = self.topic / "slides_t.de.py"
        self.en = self.topic / "slides_t.en.py"
        self.write(DE0, EN0)
        _git(root, "init", "-q")

    def write(self, de: str, en: str) -> None:
        self.de.write_text(de, encoding="utf-8")
        self.en.write_text(en, encoding="utf-8")

    def commit(self, msg: str, date: str) -> str:
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", msg, date=date)
        return _git(self.root, "rev-parse", "HEAD")

    def lecture_id(self, lang: str = "de") -> str:
        from clm.core.course import Course
        from clm.core.course_spec import CourseSpec

        course = Course.from_spec(CourseSpec.from_file(self.spec), self.root, output_root=None)
        section = course.sections[0]
        nb = next(n for n in section.notebooks if n.output_language_filter in (None, lang))
        return f"{section.name[lang]}::{nb.file_name(lang, '')}"

    def seed(self, state: CourseRecordingState, **kw):
        return seed_from_state(state, spec_file=self.spec, course_root=self.root, lang="de", **kw)


def _state(lecture_id: str, *parts: RecordingPart) -> CourseRecordingState:
    return CourseRecordingState(
        course_id="kurs-de",
        lectures=[LectureState(lecture_id=lecture_id, display_name="d", parts=list(parts))],
    )


@pytest.fixture
def course(tmp_path: Path) -> _Course:
    return _Course(tmp_path / "course")


def test_lang_of_course_id():
    assert lang_of_course_id("kurs-de") == "de"
    assert lang_of_course_id("kurs-en") == "en"
    assert lang_of_course_id("kurs") is None


def test_stamped_commit_anchors_and_reads_members_at_the_anchor(course: _Course):
    v1 = course.commit("v1", "2026-04-18T10:00:00")
    members_v1 = rl.deck_members(course.de, "de")
    course.write(DE0.replace("DE eins", "DE eins, neu"), EN0)
    course.commit("v2", "2026-05-01T10:00:00")
    assert rl.deck_members(course.de, "de") != members_v1

    state = _state(
        course.lecture_id(),
        RecordingPart(
            part=1,
            raw_file="r.mkv",
            recorded_at="2026-04-18T12:00:00",
            git_commit=v1,
            git_dirty=True,
        ),
    )
    result = course.seed(state)

    [seed] = result.parts
    assert seed.status == "seeded"
    assert seed.anchor == {"kind": "commit-dirty", "commit": v1, "dirty": True}
    assert seed.deck == "slides/module_100/topic_010_t/slides_t.de.py"
    assert result.ledgers_written == ["slides/module_100/topic_010_t/.clm/recordings-ledger.json"]
    entry = rl.load(rl.ledger_path_for(course.de)).decks["slides_t"].parts[0]
    assert entry.members == members_v1  # the deck as it was, not as it is
    assert entry.order == rl.id_order(members_v1)
    assert (entry.course_id, entry.part, entry.lang, entry.recorded_at) == (
        "kurs-de",
        1,
        "de",
        "2026-04-18T12:00:00",
    )
    # No worktree left behind.
    assert "clm-seed" not in _git(course.root, "worktree", "list")


def test_unstamped_part_anchors_on_the_last_commit_before_recorded_at(course: _Course):
    v1 = course.commit("v1", "2026-04-18T10:00:00")
    course.write(DE0.replace("DE eins", "DE eins, neu"), EN0)
    course.commit("v2", "2026-05-01T10:00:00")
    assert commit_before(course.root, "2026-04-20T00:00:00") == v1

    state = _state(
        course.lecture_id(),
        RecordingPart(part=1, raw_file="r.mkv", recorded_at="2026-04-20T12:00:00"),
    )
    [seed] = course.seed(state).parts
    assert seed.status == "seeded"
    assert seed.anchor == {"kind": "time", "commit": v1, "dirty": False}


def test_seeding_never_overwrites_record_time_evidence(course: _Course):
    v1 = course.commit("v1", "2026-04-18T10:00:00")
    # The dashboard wrote this entry from the tree that was on screen.
    exact = {"id:s0": "seen-on-screen"}
    rl.record_part(
        rl.ledger_path_for(course.de),
        "slides_t",
        rl.LedgerPart(
            part=1,
            recorded_at="t",
            course_id="kurs-de",
            lang="de",
            anchor=rl.anchor_for(v1, True),
            members=exact,
            order=["id:s0"],
        ),
    )
    state = _state(
        course.lecture_id(),
        RecordingPart(part=1, raw_file="r.mkv", recorded_at="t", git_commit=v1, git_dirty=True),
    )

    [seed] = course.seed(state).parts
    assert seed.status == "unchanged"
    assert seed.reason == "kept the record-time entry"
    kept = rl.load(rl.ledger_path_for(course.de)).decks["slides_t"].parts[0]
    assert kept.members == exact and kept.evidence == "recorded"


def test_seeded_entries_carry_anchor_evidence(course: _Course):
    v1 = course.commit("v1", "2026-04-18T10:00:00")
    state = _state(
        course.lecture_id(),
        RecordingPart(part=1, raw_file="r.mkv", recorded_at="t", git_commit=v1, git_dirty=True),
    )
    course.seed(state)
    entry = rl.load(rl.ledger_path_for(course.de)).decks["slides_t"].parts[0]
    assert entry.evidence == "anchor"
    assert rl.resolve_part_members(entry, course.de)[1] == "approximate"


def test_garbage_recorded_at_is_unresolved_not_head(course: _Course):
    course.commit("v1", "2026-04-18T10:00:00")
    assert commit_before(course.root, "t") is None
    state = _state(
        course.lecture_id(), RecordingPart(part=1, raw_file="r.mkv", recorded_at="yesterday")
    )
    [seed] = course.seed(state).parts
    assert seed.status == "unresolved"


def test_malformed_ledger_marks_the_part_unresolved(course: _Course):
    v1 = course.commit("v1", "2026-04-18T10:00:00")
    path = rl.ledger_path_for(course.de)
    path.parent.mkdir()
    path.write_text("{broken", encoding="utf-8")
    state = _state(
        course.lecture_id(), RecordingPart(part=1, raw_file="r.mkv", recorded_at="t", git_commit=v1)
    )
    [seed] = course.seed(state).parts
    assert seed.status == "unresolved"
    assert "not valid JSON" in (seed.reason or "")
    assert path.read_text(encoding="utf-8") == "{broken"


def test_course_in_a_repo_subdirectory(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    course = _Course.__new__(_Course)
    course.root = repo / "courses" / "python"
    course.topic = course.root / "slides" / "module_100" / "topic_010_t"
    course.topic.mkdir(parents=True)
    (course.root / "course-specs").mkdir()
    course.spec = course.root / "course-specs" / "kurs.xml"
    course.spec.write_text(SPEC_XML, encoding="utf-8")
    course.de = course.topic / "slides_t.de.py"
    course.en = course.topic / "slides_t.en.py"
    course.write(DE0, EN0)
    v1 = course.commit("v1", "2026-04-18T10:00:00")

    state = _state(
        course.lecture_id(), RecordingPart(part=1, raw_file="r.mkv", recorded_at="t", git_commit=v1)
    )
    [seed] = course.seed(state).parts
    assert seed.status == "seeded", seed.reason
    assert seed.deck == "slides/module_100/topic_010_t/slides_t.de.py"


def test_spec_outside_course_root_is_a_clear_error(course: _Course, tmp_path: Path):
    course.commit("v1", "2026-04-18T10:00:00")
    elsewhere = tmp_path / "elsewhere.xml"
    elsewhere.write_text(SPEC_XML, encoding="utf-8")
    with pytest.raises(ValueError, match="not under the course root"):
        seed_from_state(_state("S::D"), spec_file=elsewhere, course_root=course.root, lang="de")


def test_seeding_is_idempotent_and_dry_run_writes_nothing(course: _Course):
    v1 = course.commit("v1", "2026-04-18T10:00:00")
    state = _state(
        course.lecture_id(), RecordingPart(part=1, raw_file="r.mkv", recorded_at="t", git_commit=v1)
    )

    dry = course.seed(state, dry_run=True)
    assert [p.status for p in dry.parts] == ["seeded"]
    assert not rl.ledger_path_for(course.de).exists()

    assert [p.status for p in course.seed(state).parts] == ["seeded"]
    again = course.seed(state)
    assert [p.status for p in again.parts] == ["unchanged"]
    assert again.ledgers_written == []


def test_unresolvable_deck_is_listed_not_guessed(course: _Course):
    v1 = course.commit("v1", "2026-04-18T10:00:00")
    state = _state(
        "Woche 01: Start::99 Nie aufgenommen",
        RecordingPart(part=1, raw_file="r.mkv", recorded_at="t", git_commit=v1),
    )
    result = course.seed(state)
    [seed] = result.parts
    assert seed.status == "unresolved"
    assert "not found at" in (seed.reason or "")
    assert result.unresolved == [seed]
    assert not rl.ledger_path_for(course.de).exists()


def test_deck_renamed_since_the_anchor_is_mapped_by_topic(course: _Course):
    """Resolved at the anchor under its old name, found in the current tree by topic id."""
    v1 = course.commit("v1", "2026-04-18T10:00:00")
    lecture_id = course.lecture_id()
    state = _state(
        lecture_id, RecordingPart(part=1, raw_file="r.mkv", recorded_at="t", git_commit=v1)
    )
    # The topic directory moves (renumbered); the deck file keeps its name.
    new_topic = course.topic.parent / "topic_020_t"
    course.topic.rename(new_topic)
    course.commit("renumber", "2026-05-01T10:00:00")

    [seed] = course.seed(state).parts
    assert seed.status == "seeded"
    assert seed.deck == "slides/module_100/topic_020_t/slides_t.de.py"

    # Deleted since the anchor: unresolved, with the anchor-time path named.
    shutil.rmtree(new_topic)
    course.commit("delete", "2026-06-01T10:00:00")
    [seed] = course.seed(state).parts
    assert seed.status == "unresolved"
    assert "not in the current tree" in (seed.reason or "")


def test_cli_seed_ledger_json_and_exit_codes(course: _Course, monkeypatch):
    v1 = course.commit("v1", "2026-04-18T10:00:00")
    state = _state(
        course.lecture_id(),
        RecordingPart(part=1, raw_file="r.mkv", recorded_at="t", git_commit=v1),
        RecordingPart(
            part=2, raw_file="r2.mkv", recorded_at="2020-01-01T00:00:00"
        ),  # before any commit
    )
    monkeypatch.setattr("clm.recordings.state.load_state", lambda course_id: state)
    runner = CliRunner()

    result = runner.invoke(
        recordings_group,
        ["seed-ledger", "kurs-de", "--spec-file", str(course.spec), "--dry-run", "--json"],
    )
    assert result.exit_code == 1, result.output  # one part unresolved
    payload = json.loads(result.stdout)
    assert (payload["schema"], payload["tool"], payload["verb"]) == (1, "recordings", "seed-ledger")
    assert payload["dry_run"] is True
    assert payload["counts"] == {"seeded": 1, "unresolved": 1}
    assert payload["parts"][1]["reason"].startswith("no commit stamped")
    assert not rl.ledger_path_for(course.de).exists()

    result = runner.invoke(
        recordings_group, ["seed-ledger", "kurs-de", "--spec-file", str(course.spec)]
    )
    assert result.exit_code == 1, result.output
    assert rl.ledger_path_for(course.de).exists()
    assert "1 seeded" in result.output and "1 unresolved" in result.output


def test_cli_seed_ledger_without_state_is_exit_two(monkeypatch):
    monkeypatch.setattr("clm.recordings.state.load_state", lambda course_id: None)
    result = CliRunner().invoke(recordings_group, ["seed-ledger", "nope-de", "--json"])
    assert result.exit_code == 2
    assert "No recording state" in result.stderr
