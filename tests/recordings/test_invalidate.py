"""``clm recordings invalidate`` and the dashboard action: parts, files, state (#1093, #908).

The owner's decision: a take keeps reaching the ledger by default at stop, and
``invalidate`` is the explicit exception. The part semantics pinned here:

=======================================  ======================================
Case                                     Effect
=======================================  ======================================
Retake, any part                         previous take restored, numbers stand
Only take of the only part               part gone, deck "not recorded"
Only take of the last part               part gone, numbers below stand
... leaving only part 1                  single-part again, files unsuffixed
Only take of a middle part or of part 1  refused: never renumber, never a hole
=======================================  ======================================

Only temp fixtures: no real recordings folder is touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from clm.cli.commands.recordings import recordings_group
from clm.recordings import ledger as rl
from clm.recordings import state as state_module
from clm.recordings.report import build_report
from clm.recordings.state import (
    CourseRecordingState,
    LectureState,
    RecordingPart,
    TakeRecord,
    load_state,
    save_state,
)
from clm.recordings.workflow.directories import ensure_root
from clm.recordings.workflow.invalidate import (
    InvalidateRequest,
    InvalidationRefused,
    invalidate_take,
)
from clm.recordings.workflow.jobs import JobState, ProcessingJob
from clm.recordings.workflow.session import RecordingSession, SessionState
from tests.recordings.test_ledger import DE0, EN0

COURSE = "c-de"
SECTION = "Woche 01"
DECK_NAME = "03 Intro"
LECTURE = f"{SECTION}::{DECK_NAME}"
TAKE1 = "2026-10-01T10:00:00"
TAKE2 = "2026-10-21T10:15:24"
TAKE3 = "2026-10-22T09:00:00"


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """State files and dashboard markers in a temp dir, never the user's config."""
    directory = tmp_path / "state"
    monkeypatch.setattr(state_module, "get_recordings_dir", lambda: directory)
    return directory


@pytest.fixture
def deck(tmp_path: Path) -> Path:
    topic = tmp_path / "course" / "slides" / "module_100" / "topic_010_t"
    topic.mkdir(parents=True)
    de = topic / "slides_t.de.py"
    de.write_text(DE0, encoding="utf-8")
    (topic / "slides_t.en.py").write_text(EN0, encoding="utf-8")
    return de


@pytest.fixture
def root(tmp_path: Path) -> Path:
    recordings = tmp_path / "recordings"
    ensure_root(recordings)
    return recordings


def _ledger_part(stamp: str, part: int = 1, *, fp: str | None = None) -> rl.LedgerPart:
    return rl.LedgerPart(
        part=part,
        recorded_at=stamp,
        course_id=COURSE,
        lang="de",
        anchor=rl.anchor_for("abc123", False),
        members={"id:s0": fp or f"fp-{stamp}"},
        order=["id:s0"],
    )


def _record(deck: Path, *parts: rl.LedgerPart) -> Path:
    path = rl.ledger_path_for(deck)
    for part in parts:
        rl.record_part(path, "slides_t", part)
    return path


def _dir(root: Path, tier: str) -> Path:
    d = root / tier / COURSE / SECTION
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(path.name.encode())
    return path


def _state(*parts: RecordingPart) -> CourseRecordingState:
    return CourseRecordingState(
        course_id=COURSE,
        lectures=[LectureState(lecture_id=LECTURE, display_name=DECK_NAME, parts=list(parts))],
    )


def _request(deck: Path, part: int = 1, **kwargs) -> InvalidateRequest:
    defaults = {
        "course_id": COURSE,
        "part": part,
        "lang": "de",
        "ledger_path": rl.ledger_path_for(deck),
        "deck_key": "slides_t",
        "deck_path": deck,
    }
    return InvalidateRequest(**{**defaults, **kwargs})


def _parts_in_ledger(deck: Path) -> list[tuple[int, str]]:
    entry = rl.load(rl.ledger_path_for(deck)).decks["slides_t"]
    return sorted((p.part, p.recorded_at) for p in entry.parts)


class TestPartSemantics:
    def test_retake_restores_the_previous_good_take(self, deck: Path):
        """#1093's retake case: the good take's fingerprints come back."""
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        result = invalidate_take(_request(deck))
        assert result.effect == "restored"
        assert result.recorded_at == TAKE2
        assert result.restored_recorded_at == TAKE1
        assert _parts_in_ledger(deck) == [(1, TAKE1)]
        entry = rl.load(rl.ledger_path_for(deck)).decks["slides_t"]
        assert entry.parts[0].members == {"id:s0": f"fp-{TAKE1}"}
        assert [t.recorded_at for t in entry.invalidated] == [TAKE2]

    def test_the_only_part_goes_back_to_not_recorded(self, deck: Path):
        _record(deck, _ledger_part(TAKE1))
        result = invalidate_take(_request(deck))
        assert result.effect == "removed"
        assert not result.single_part
        assert _parts_in_ledger(deck) == []

    def test_last_part_goes_and_lower_numbers_stand(self, deck: Path):
        _record(deck, _ledger_part(TAKE1, 1), _ledger_part(TAKE1, 2), _ledger_part(TAKE1, 3))
        result = invalidate_take(_request(deck, 3))
        assert result.effect == "removed"
        assert not result.single_part
        assert _parts_in_ledger(deck) == [(1, TAKE1), (2, TAKE1)]

    def test_removing_all_later_parts_makes_it_single_part(self, deck: Path):
        """#908, walked down from the highest part."""
        _record(deck, _ledger_part(TAKE1, 1), _ledger_part(TAKE1, 2), _ledger_part(TAKE1, 3))
        assert not invalidate_take(_request(deck, 3)).single_part
        assert invalidate_take(_request(deck, 2)).single_part
        assert _parts_in_ledger(deck) == [(1, TAKE1)]

    @pytest.mark.parametrize("part", [1, 2])
    def test_only_take_with_later_parts_is_refused(self, deck: Path, part: int):
        path = _record(deck, _ledger_part(TAKE1, 1), _ledger_part(TAKE1, 2), _ledger_part(TAKE1, 3))
        before = path.read_bytes()
        with pytest.raises(InvalidationRefused, match="never renumbered"):
            invalidate_take(_request(deck, part))
        assert path.read_bytes() == before

    @pytest.mark.parametrize("part", [1, 2])
    def test_retake_of_a_lower_part_is_fine_and_keeps_numbers(self, deck: Path, part: int):
        _record(
            deck,
            _ledger_part(TAKE1, 1),
            _ledger_part(TAKE1, 2),
            _ledger_part(TAKE1, 3),
            _ledger_part(TAKE2, part),
        )
        assert invalidate_take(_request(deck, part)).effect == "restored"
        assert _parts_in_ledger(deck) == [(1, TAKE1), (2, TAKE1), (3, TAKE1)]

    def test_the_only_part_of_one_language_ignores_the_other_language(self, deck: Path):
        en = _ledger_part(TAKE1, 2).model_copy(update={"lang": "en"})
        _record(deck, _ledger_part(TAKE1, 1), en)
        assert invalidate_take(_request(deck, 1)).effect == "removed"

    def test_later_part_known_only_to_the_state_file_refuses(self, deck: Path):
        """A deck that did not resolve at record time has no ledger row for its part."""
        _record(deck, _ledger_part(TAKE1, 1))
        state = _state(
            RecordingPart(part=1, raw_file="a", recorded_at=TAKE1),
            RecordingPart(part=2, raw_file="b", recorded_at=TAKE2),
        )
        with pytest.raises(InvalidationRefused, match="never renumbered"):
            invalidate_take(_request(deck, 1, state=state))

    def test_later_part_known_only_to_the_disk_refuses(self, deck: Path, root: Path):
        _record(deck, _ledger_part(TAKE1, 1))
        state = _state(RecordingPart(part=1, raw_file="a", recorded_at=TAKE1))
        _file(_dir(root, "to-process") / f"{DECK_NAME} (part 2)--RAW.mkv")
        with pytest.raises(InvalidationRefused, match="never renumbered"):
            invalidate_take(_request(deck, 1, state=state, recordings_root=root))

    def test_superseded_take_can_be_invalidated_by_stamp(self, deck: Path):
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2), _ledger_part(TAKE3))
        result = invalidate_take(_request(deck, recorded_at=TAKE2))
        assert result.effect == "history"
        assert _parts_in_ledger(deck) == [(1, TAKE3)]
        # ...so walking back now skips the bad one.
        assert invalidate_take(_request(deck)).restored_recorded_at == TAKE1

    def test_stale_stamp_is_refused(self, deck: Path):
        """A repeated call with the stamp must not hit the restored take."""
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        invalidate_take(_request(deck, recorded_at=TAKE2))
        with pytest.raises(InvalidationRefused, match=TAKE1):
            invalidate_take(_request(deck, recorded_at=TAKE2))
        assert _parts_in_ledger(deck) == [(1, TAKE1)]

    def test_dry_run_changes_nothing(self, deck: Path):
        path = _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        before = path.read_bytes()
        result = invalidate_take(_request(deck, dry_run=True))
        assert result.effect == "restored"
        assert path.read_bytes() == before


class TestReportAfterwards:
    def test_report_shows_the_restored_take_not_the_invalid_one(self, deck: Path):
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        invalidate_take(_request(deck, note="wrong slide"))
        report = build_report(deck)
        [row] = report.decks
        assert [p.recorded_at for p in row.parts] == [TAKE1]
        assert [t["note"] for t in row.invalidated] == ["wrong slide"]

    def test_only_take_reads_unrecorded(self, deck: Path):
        """The concrete case of #1093: `recorded`/`visible` until invalidated."""
        _record(deck, _ledger_part(TAKE1))
        course_root = deck.parents[3]
        assert build_report(course_root).decks[0].status == "recorded"
        invalidate_take(_request(deck))
        assert build_report(course_root).decks == []
        [row] = build_report(course_root, include_unrecorded=True).decks
        assert row.status == "unrecorded"
        assert not row.needs_attention
        assert [t["recorded_at"] for t in row.invalidated] == [TAKE1]


_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **_GIT_ENV},
    ).stdout.strip()


class TestLegacyLedgerFallback:
    """A ledger from before ``superseded``: the retake already overwrote the entry."""

    def test_earlier_take_is_rebuilt_from_the_state_file(self, deck: Path):
        repo = deck.parent
        _git(repo, "init", "-q")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "initial")
        commit = _git(repo, "rev-parse", "HEAD")
        _record(deck, _ledger_part(TAKE2))
        state = _state(
            RecordingPart(
                part=1,
                raw_file="new",
                recorded_at=TAKE2,
                active_take=2,
                takes=[TakeRecord(take=1, raw_file="old", recorded_at=TAKE1, git_commit=commit)],
            )
        )
        result = invalidate_take(_request(deck, state=state))
        assert result.restored_recorded_at == TAKE1
        [restored] = rl.load(rl.ledger_path_for(deck)).decks["slides_t"].parts
        assert restored.recorded_at == TAKE1
        assert restored.evidence == "anchor"
        assert restored.members == rl.deck_members(deck, "de")
        assert "state file" in " ".join(result.warnings)

    def test_without_a_commit_it_is_unverifiable_rather_than_unrecorded(self, deck: Path):
        _record(deck, _ledger_part(TAKE2))
        state = _state(
            RecordingPart(
                part=1,
                raw_file="new",
                recorded_at=TAKE2,
                active_take=2,
                takes=[TakeRecord(take=1, raw_file="old", recorded_at=TAKE1)],
            )
        )
        invalidate_take(_request(deck, state=state))
        [row] = build_report(deck).decks
        assert row.status == "recorded"
        assert row.parts[0].members_status == "unverifiable"


class TestStateFile:
    def _with_takes(self) -> CourseRecordingState:
        return _state(
            RecordingPart(
                part=1,
                raw_file="t3",
                recorded_at=TAKE3,
                active_take=3,
                git_commit="c3",
                takes=[
                    TakeRecord(take=1, raw_file="t1", recorded_at=TAKE1, git_commit="c1"),
                    TakeRecord(take=2, raw_file="t2", recorded_at=TAKE2, git_commit="c2"),
                ],
            )
        )

    def test_active_take_is_kept_in_the_audit_and_the_previous_is_promoted(self, deck: Path):
        _record(deck, _ledger_part(TAKE2), _ledger_part(TAKE3))
        state = self._with_takes()
        result = invalidate_take(_request(deck, state=state, note="n"))
        assert result.state_updated
        lecture = state.lectures[0]
        part = lecture.parts[0]
        assert (part.active_take, part.raw_file, part.git_commit) == (2, "t2", "c2")
        assert part.recorded_at == TAKE2
        assert [t.take for t in part.takes] == [1]
        [record] = lecture.invalidated
        assert (record.take, record.part, record.recorded_at) == (3, 1, TAKE3)
        assert record.note == "n"
        assert record.invalidated_at

    def test_state_follows_the_ledgers_choice_of_take(self, deck: Path):
        """The ledger restored take 1, so the state must not promote take 2."""
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE3))
        state = self._with_takes()
        invalidate_take(_request(deck, state=state))
        assert state.lectures[0].parts[0].active_take == 1

    def test_superseded_take_just_leaves_the_history(self, deck: Path):
        _record(deck, _ledger_part(TAKE2), _ledger_part(TAKE3))
        state = self._with_takes()
        invalidate_take(_request(deck, state=state, recorded_at=TAKE2))
        part = state.lectures[0].parts[0]
        assert part.active_take == 3
        assert [t.take for t in part.takes] == [1]
        assert [r.take for r in state.lectures[0].invalidated] == [2]

    def test_only_take_removes_the_part_and_keeps_the_audit(self, deck: Path):
        _record(deck, _ledger_part(TAKE1))
        state = _state(RecordingPart(part=1, raw_file="t1", recorded_at=TAKE1))
        invalidate_take(_request(deck, state=state))
        lecture = state.lectures[0]
        assert lecture.parts == []
        assert not lecture.is_recorded
        assert [r.raw_file for r in lecture.invalidated] == ["t1"]

    def test_old_state_file_loads_and_the_audit_round_trips(self, tmp_path: Path):
        path = tmp_path / "s.json"
        path.write_text(
            json.dumps(
                {
                    "course_id": COURSE,
                    "lectures": [{"lecture_id": "l", "display_name": "L", "parts": []}],
                }
            ),
            encoding="utf-8",
        )
        state = CourseRecordingState.load(path)
        assert state.lectures[0].invalidated == []

    def test_stamp_in_two_lectures_is_refused_as_ambiguous(self, deck: Path):
        _record(deck, _ledger_part(TAKE1))
        state = CourseRecordingState(
            course_id=COURSE,
            lectures=[
                LectureState(
                    lecture_id=f"s::{name}",
                    display_name=name,
                    parts=[RecordingPart(part=1, raw_file=name, recorded_at=TAKE1)],
                )
                for name in ("a", "b")
            ],
        )
        with pytest.raises(InvalidationRefused, match="more than one lecture"):
            invalidate_take(_request(deck, state=state))


class TestFiles:
    def test_single_part_only_take_moves_to_superseded(self, deck: Path, root: Path):
        _record(deck, _ledger_part(TAKE1))
        raw = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv")
        wav = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.wav")
        state = _state(RecordingPart(part=1, raw_file=str(raw), recorded_at=TAKE1))
        result = invalidate_take(_request(deck, state=state, recordings_root=root))
        gone = _dir(root, "superseded")
        assert not raw.exists() and not wav.exists()
        assert (gone / raw.name).read_bytes() == raw.name.encode()
        assert (gone / wav.name).exists()
        assert {b for _, b in result.moves} == {gone / raw.name, gone / wav.name}
        assert state.lectures[0].invalidated[0].raw_file == str(gone / raw.name)

    def test_collision_in_superseded_is_not_overwritten(self, deck: Path, root: Path):
        _record(deck, _ledger_part(TAKE1))
        raw = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv")
        earlier = _dir(root, "superseded") / raw.name
        earlier.write_bytes(b"earlier")
        invalidate_take(
            _request(
                deck,
                recordings_root=root,
                state=_state(RecordingPart(part=1, raw_file=str(raw), recorded_at=TAKE1)),
            )
        )
        assert earlier.read_bytes() == b"earlier"
        assert (_dir(root, "superseded") / f"{DECK_NAME}--RAW (2).mkv").exists()

    def test_retake_swaps_the_processed_previous_take_back(self, deck: Path, root: Path):
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        new_raw = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv")
        old_raw = _file(_dir(root, "takes") / f"{DECK_NAME} (take 1)--RAW.mkv")
        old_final = _file(_dir(root, "takes") / f"{DECK_NAME} (take 1).mp4")
        state = _state(
            RecordingPart(
                part=1,
                raw_file=str(new_raw),
                recorded_at=TAKE2,
                active_take=2,
                takes=[
                    TakeRecord(
                        take=1,
                        raw_file=str(old_raw),
                        processed_file=str(old_final),
                        recorded_at=TAKE1,
                        status="processed",
                    )
                ],
            )
        )
        invalidate_take(_request(deck, state=state, recordings_root=root))
        back_raw = _dir(root, "archive") / new_raw.name
        back_final = _dir(root, "final") / f"{DECK_NAME}.mp4"
        assert back_raw.read_bytes() == old_raw.name.encode()
        assert back_final.read_bytes() == old_final.name.encode()
        assert (_dir(root, "superseded") / new_raw.name).exists()
        assert not new_raw.exists() and not old_raw.exists() and not old_final.exists()
        part = state.lectures[0].parts[0]
        assert part.raw_file == str(back_raw)
        assert part.processed_file == str(back_final)
        assert part.status == "processed"

    def test_unprocessed_previous_take_goes_back_to_to_process(self, deck: Path, root: Path):
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        new_raw = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv")
        old_raw = _file(_dir(root, "takes") / f"{DECK_NAME} (take 1)--RAW.mkv")
        state = _state(
            RecordingPart(
                part=1,
                raw_file=str(new_raw),
                recorded_at=TAKE2,
                active_take=2,
                takes=[TakeRecord(take=1, raw_file=str(old_raw), recorded_at=TAKE1)],
            )
        )
        invalidate_take(_request(deck, state=state, recordings_root=root))
        assert new_raw.read_bytes() == old_raw.name.encode()

    def test_superseded_take_files_leave_takes(self, deck: Path, root: Path):
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        active = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv")
        old = _file(_dir(root, "takes") / f"{DECK_NAME} (take 1)--RAW.mkv")
        state = _state(
            RecordingPart(
                part=1,
                raw_file=str(active),
                recorded_at=TAKE2,
                active_take=2,
                takes=[TakeRecord(take=1, raw_file=str(old), recorded_at=TAKE1)],
            )
        )
        invalidate_take(_request(deck, state=state, recordings_root=root, recorded_at=TAKE1))
        assert active.exists()
        assert not old.exists()
        assert (_dir(root, "superseded") / old.name).exists()

    def _multipart(self, deck: Path, root: Path, lang_label: str = "part"):
        _record(deck, _ledger_part(TAKE1, 1), _ledger_part(TAKE2, 2))
        p1_raw = _file(_dir(root, "archive") / f"{DECK_NAME} ({lang_label} 1)--RAW.mkv")
        p1_wav = _file(_dir(root, "archive") / f"{DECK_NAME} ({lang_label} 1)--RAW.wav")
        p1_final = _file(_dir(root, "final") / f"{DECK_NAME} ({lang_label} 1).mp4")
        p1_edl = _file(_dir(root, "final") / f"{DECK_NAME} ({lang_label} 1).edl")
        p1_take = _file(_dir(root, "takes") / f"{DECK_NAME} ({lang_label} 1, take 1)--RAW.mkv")
        p2_raw = _file(_dir(root, "to-process") / f"{DECK_NAME} ({lang_label} 2)--RAW.mkv")
        state = _state(
            RecordingPart(
                part=1,
                raw_file=str(p1_raw),
                processed_file=str(p1_final),
                recorded_at=TAKE1,
                active_take=2,
                takes=[
                    TakeRecord(take=1, raw_file=str(p1_take), recorded_at="2026-09-01T00:00:00")
                ],
            ),
            RecordingPart(part=2, raw_file=str(p2_raw), recorded_at=TAKE2),
        )
        return state, p1_raw, p1_wav, p1_final, p1_edl, p1_take, p2_raw

    @pytest.mark.parametrize("label", ["part", "Teil"])
    def test_908_back_to_single_part_renames_everything(self, deck: Path, root: Path, label: str):
        state, p1_raw, p1_wav, p1_final, p1_edl, p1_take, p2_raw = self._multipart(
            deck, root, label
        )
        result = invalidate_take(_request(deck, 2, state=state, recordings_root=root))
        assert result.single_part
        assert (_dir(root, "superseded") / p2_raw.name).exists()
        assert (_dir(root, "archive") / f"{DECK_NAME}--RAW.mkv").exists()
        assert (_dir(root, "archive") / f"{DECK_NAME}--RAW.wav").exists()
        assert (_dir(root, "final") / f"{DECK_NAME}.mp4").exists()
        assert (_dir(root, "final") / f"{DECK_NAME}.edl").exists()
        assert (_dir(root, "takes") / f"{DECK_NAME} (take 1)--RAW.mkv").exists()
        for old in (p1_raw, p1_wav, p1_final, p1_edl, p1_take, p2_raw):
            assert not old.exists()
        part = state.lectures[0].parts[0]
        assert part.raw_file == str(_dir(root, "archive") / f"{DECK_NAME}--RAW.mkv")
        assert part.processed_file == str(_dir(root, "final") / f"{DECK_NAME}.mp4")
        assert part.takes[0].raw_file == str(_dir(root, "takes") / f"{DECK_NAME} (take 1)--RAW.mkv")

    def test_908_the_next_part_cascades_as_usual(self, deck: Path, root: Path, mock_obs_session):
        """Back to single-part is the exact inverse of the record-time cascade."""
        from clm.recordings.workflow.session import _cascade_unsuffixed_to_part1

        state, *_ = self._multipart(deck, root)
        invalidate_take(_request(deck, 2, state=state, recordings_root=root))
        _cascade_unsuffixed_to_part1(
            recordings_root=root,
            target_dir=_dir(root, "to-process"),
            deck_name=DECK_NAME,
            raw_suffix="--RAW",
            lang="en",
        )
        assert (_dir(root, "archive") / f"{DECK_NAME} (part 1)--RAW.mkv").exists()
        assert (_dir(root, "takes") / f"{DECK_NAME} (part 1, take 1)--RAW.mkv").exists()

    def test_name_in_the_way_keeps_the_part_1_names_and_warns(self, deck: Path, root: Path):
        state, p1_raw, *_ = self._multipart(deck, root)
        _file(_dir(root, "takes") / f"{DECK_NAME} (take 1)--RAW.mkv")
        result = invalidate_take(_request(deck, 2, state=state, recordings_root=root))
        assert result.single_part
        assert p1_raw.exists()
        assert any("in the way" in w for w in result.warnings)

    def test_unsuffixed_and_part_1_on_disk_is_ambiguous(self, deck: Path, root: Path):
        path = _record(deck, _ledger_part(TAKE1))
        before = path.read_bytes()
        raw = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv")
        _file(_dir(root, "to-process") / f"{DECK_NAME} (part 1)--RAW.mkv")
        state = _state(RecordingPart(part=1, raw_file=str(raw), recorded_at=TAKE1))
        with pytest.raises(InvalidationRefused, match="ambiguous"):
            invalidate_take(_request(deck, state=state, recordings_root=root))
        assert path.read_bytes() == before
        assert raw.exists()

    def test_running_job_for_the_deck_is_refused(self, deck: Path, root: Path):
        path = _record(deck, _ledger_part(TAKE1))
        before = path.read_bytes()
        raw = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv")
        state = _state(RecordingPart(part=1, raw_file=str(raw), recorded_at=TAKE1))
        job = ProcessingJob(
            backend_name="auphonic",
            raw_path=raw,
            final_path=_dir(root, "final") / f"{DECK_NAME}.mp4",
            relative_dir=Path(COURSE) / SECTION,
            state=JobState.UPLOADING,
        )
        with pytest.raises(InvalidationRefused, match="still running"):
            invalidate_take(_request(deck, state=state, recordings_root=root, active_jobs=[job]))
        assert path.read_bytes() == before
        assert raw.exists()

    def test_finished_job_does_not_block(self, deck: Path, root: Path):
        _record(deck, _ledger_part(TAKE1))
        raw = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv")
        state = _state(RecordingPart(part=1, raw_file=str(raw), recorded_at=TAKE1))
        job = ProcessingJob(
            backend_name="auphonic",
            raw_path=raw,
            final_path=_dir(root, "final") / f"{DECK_NAME}.mp4",
            relative_dir=Path(COURSE) / SECTION,
            state=JobState.COMPLETED,
        )
        invalidate_take(_request(deck, state=state, recordings_root=root, active_jobs=[job]))

    def test_failed_move_rolls_back_and_leaves_the_ledger(self, deck: Path, root: Path):
        path = _record(deck, _ledger_part(TAKE1))
        before = path.read_bytes()
        raw = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv")
        wav = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.wav")
        state = _state(RecordingPart(part=1, raw_file=str(raw), recorded_at=TAKE1))
        calls: list[str] = []

        def flaky(src, dst):
            calls.append(Path(src).suffix)
            if len(calls) == 2:
                raise PermissionError("locked")
            os.replace(src, dst)

        with patch("clm.recordings.workflow.invalidate.safe_move", side_effect=flaky):
            with pytest.raises(InvalidationRefused, match="nothing changed"):
                invalidate_take(_request(deck, state=state, recordings_root=root))
        assert raw.exists() and wav.exists()
        assert path.read_bytes() == before
        assert state.lectures[0].parts[0].raw_file == str(raw)

    def test_no_root_touches_no_file_and_says_so(self, deck: Path, root: Path):
        _record(deck, _ledger_part(TAKE1))
        raw = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv")
        state = _state(RecordingPart(part=1, raw_file=str(raw), recorded_at=TAKE1))
        result = invalidate_take(_request(deck, state=state))
        assert raw.exists()
        assert result.moves == []
        assert any("not touched" in w for w in result.warnings)


@pytest.fixture
def mock_obs_session() -> MagicMock:
    obs = MagicMock()
    obs.connected = True
    return obs


class TestSeedDoesNotResurrect:
    def test_seed_skips_an_invalidated_take(self, deck: Path):
        """Another machine's state file still lists the take."""
        from attrs import define

        from clm.recordings.seed import SeedPart, _seed_one

        _record(deck, _ledger_part(TAKE1))
        invalidate_take(_request(deck))

        @define
        class Pending:
            section: str
            deck: str
            part: RecordingPart
            anchor: rl.RecordingAnchor
            seed: SeedPart

        seed = SeedPart(
            lecture_id=LECTURE, part=1, recorded_at=TAKE1, status="unresolved", anchor={}
        )
        pending = Pending(
            "s",
            "d",
            RecordingPart(part=1, raw_file="x", recorded_at=TAKE1),
            rl.anchor_for("abc123", False),
            seed,
        )
        course_root = deck.parents[3]
        course = MagicMock()
        course.resolve_deck_location.return_value = ("sec", "topic", deck)
        found = _seed_one(
            pending,  # type: ignore[arg-type]
            state=CourseRecordingState(course_id=COURSE),
            lang="de",
            course_at=course,
            current_course=course,
            anchor_root=course_root,
            course_root=course_root,
            topic_map={},
            dry_run=False,
            written=set(),
        )
        assert found
        assert seed.status == "unchanged"
        assert seed.reason == "take was invalidated"
        assert _parts_in_ledger(deck) == []


class TestCli:
    def _run(self, *args: str):
        return CliRunner().invoke(recordings_group, ["invalidate", *args])

    def test_json_envelope(self, deck: Path, monkeypatch: pytest.MonkeyPatch):
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        result = self._run(str(deck), "--json", "--note", "aborted")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["schema"] == 1
        assert payload["verb"] == "invalidate"
        assert payload["effect"] == "restored"
        assert payload["recorded_at"] == TAKE2
        assert payload["restored_recorded_at"] == TAKE1
        assert payload["note"] == "aborted"
        assert _parts_in_ledger(deck) == [(1, TAKE1)]

    def test_text_output_names_the_restored_take(self, deck: Path):
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        result = self._run(str(deck))
        assert result.exit_code == 0, result.output
        assert TAKE1 in result.output.replace("\n", "")

    def test_either_half_addresses_the_deck(self, deck: Path):
        _record(deck, _ledger_part(TAKE1))
        assert self._run(str(deck.with_name("slides_t.en.py"))).exit_code == 0
        assert _parts_in_ledger(deck) == []

    def test_no_recording_is_exit_two(self, deck: Path):
        result = self._run(str(deck))
        assert result.exit_code == 2

    def test_several_parts_without_part_is_refused(self, deck: Path):
        _record(deck, _ledger_part(TAKE1, 1), _ledger_part(TAKE1, 2))
        result = self._run(str(deck), "--json")
        assert result.exit_code == 2
        assert "--part" in result.stderr
        assert len(_parts_in_ledger(deck)) == 2

    def test_two_languages_or_courses_are_refused_until_named(self, deck: Path):
        en = _ledger_part(TAKE1).model_copy(update={"lang": "en", "course_id": "c-en"})
        _record(deck, _ledger_part(TAKE1), en)
        assert self._run(str(deck)).exit_code == 2
        assert self._run(str(deck), "--lang", "en").exit_code == 0
        assert _parts_in_ledger(deck) == [(1, TAKE1)]

    def test_part_zero_is_a_usage_error(self, deck: Path):
        assert self._run(str(deck), "--part", "0").exit_code == 2

    def test_refused_middle_part_is_exit_two_with_a_reason(self, deck: Path):
        _record(deck, _ledger_part(TAKE1, 1), _ledger_part(TAKE1, 2), _ledger_part(TAKE1, 3))
        result = self._run(str(deck), "--part", "2", "--json")
        assert result.exit_code == 2
        assert "renumbered" in result.stderr.replace("\n", "")

    def test_unreadable_ledger_is_exit_two_and_not_overwritten(self, deck: Path):
        path = rl.ledger_path_for(deck)
        path.parent.mkdir(parents=True)
        path.write_text("{broken", encoding="utf-8")
        assert self._run(str(deck)).exit_code == 2
        assert path.read_text(encoding="utf-8") == "{broken"

    def test_state_file_and_files_are_updated(self, deck: Path, root: Path):
        _record(deck, _ledger_part(TAKE1))
        raw = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv")
        save_state(_state(RecordingPart(part=1, raw_file=str(raw), recorded_at=TAKE1)))
        result = self._run(str(deck), "--root", str(root), "--json")
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["state_updated"] is True
        assert not raw.exists()
        reloaded = load_state(COURSE)
        assert reloaded is not None
        assert reloaded.lectures[0].parts == []
        assert len(reloaded.lectures[0].invalidated) == 1

    def test_dry_run_leaves_every_store(self, deck: Path, root: Path):
        path = _record(deck, _ledger_part(TAKE1))
        before = path.read_bytes()
        raw = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv")
        save_state(_state(RecordingPart(part=1, raw_file=str(raw), recorded_at=TAKE1)))
        result = self._run(str(deck), "--root", str(root), "--dry-run", "--json")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["dry_run"] is True
        assert payload["files"]
        assert raw.exists()
        assert path.read_bytes() == before
        assert load_state(COURSE).lectures[0].parts  # type: ignore[union-attr]

    def test_course_id_with_a_path_in_it_never_reaches_the_state_dir(
        self, deck: Path, isolated_state_dir: Path
    ):
        """The id comes from a committed file; it must not name a file elsewhere."""
        evil = _ledger_part(TAKE1).model_copy(update={"course_id": "../evil"})
        _record(deck, evil)
        result = self._run(str(deck))
        assert result.exit_code == 0, result.output
        assert not (isolated_state_dir.parent / "evil.json").exists()


class TestCliSubprocess:
    """The real console path: Rich markup from a committed file must not blow up."""

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "clm.cli.main", "recordings", "invalidate", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "COLUMNS": "200"},
            timeout=300,
        )

    def test_human_output_and_refusal(self, deck: Path, tmp_path: Path):
        # Read-only (--dry-run): a subprocess cannot be pointed at a temp state dir.
        _record(
            deck,
            _ledger_part("2026-10-01T10:00:00[/red]", 1),
            _ledger_part(TAKE2, 1),
            _ledger_part(TAKE1, 2),
        )
        common = ("--root", str(tmp_path / "no-root"), "--dry-run", "--course", COURSE)
        done = self._run(str(deck), "--part", "1", *common)
        assert done.returncode == 0, done.stdout + done.stderr
        assert "2026-10-01T10:00:00[/red]" in done.stdout

        refused = self._run(str(deck), *common)
        assert refused.returncode == 2
        assert "--part" in refused.stdout


class TestDashboardRace:
    """An in-progress session must not race the CLI (the dashboard holds state in memory)."""

    def test_cli_refuses_while_a_dashboard_runs(self, deck: Path, tmp_path: Path):
        from clm.recordings.dashboard_presence import dashboard_marker

        path = _record(deck, _ledger_part(TAKE1))
        before = path.read_bytes()
        with dashboard_marker("http://127.0.0.1:8008", tmp_path):
            result = CliRunner().invoke(recordings_group, ["invalidate", str(deck), "--json"])
        assert result.exit_code == 2
        assert "dashboard is running" in result.stderr.replace("\n", " ")
        assert path.read_bytes() == before

    def test_dry_run_is_allowed_while_a_dashboard_runs(self, deck: Path, tmp_path: Path):
        from clm.recordings.dashboard_presence import dashboard_marker

        _record(deck, _ledger_part(TAKE1))
        with dashboard_marker("http://127.0.0.1:8008", tmp_path):
            result = CliRunner().invoke(recordings_group, ["invalidate", str(deck), "--dry-run"])
        assert result.exit_code == 0, result.output

    def test_marker_is_gone_after_the_dashboard(self, tmp_path: Path, isolated_state_dir: Path):
        from clm.recordings.dashboard_presence import dashboard_marker, live_dashboards

        with dashboard_marker("u", tmp_path):
            assert [m["url"] for m in live_dashboards()] == ["u"]
        assert live_dashboards() == []
        assert list(isolated_state_dir.iterdir()) == []

    def test_stale_marker_of_a_dead_process_does_not_block(
        self, deck: Path, isolated_state_dir: Path
    ):
        """A killed dashboard must not lock the verb out forever."""
        from clm.recordings.dashboard_presence import live_dashboards

        isolated_state_dir.mkdir(parents=True)
        stale = isolated_state_dir / "dashboard-999999.marker.json"
        stale.write_text(json.dumps({"pid": 2**30, "started": 1.0, "url": "u"}), encoding="utf-8")
        reused = isolated_state_dir / f"dashboard-{os.getpid()}.marker.json"
        reused.write_text(
            json.dumps({"pid": os.getpid(), "started": 1.0, "url": "reused pid"}), encoding="utf-8"
        )
        garbage = isolated_state_dir / "dashboard-1.marker.json"
        garbage.write_text("{", encoding="utf-8")
        assert live_dashboards() == []
        assert not stale.exists() and not reused.exists() and not garbage.exists()

    def test_serve_runs_uvicorn_inside_the_marker(self, tmp_path: Path, isolated_state_dir: Path):
        from clm.recordings.dashboard_presence import live_dashboards

        seen: list[list[dict]] = []
        with (
            patch("uvicorn.run", side_effect=lambda *a, **k: seen.append(live_dashboards())),
            patch("clm.recordings.web.app.create_app", return_value=MagicMock()),
        ):
            result = CliRunner().invoke(
                recordings_group, ["serve", str(tmp_path / "r"), "--no-browser", "--port", "8123"]
            )
        assert result.exit_code == 0, result.output
        assert [m["url"] for m in seen[0]] == ["http://127.0.0.1:8123"]
        assert live_dashboards() == []


class TestSession:
    def _session(self, obs: MagicMock, root: Path, state: CourseRecordingState) -> RecordingSession:
        return RecordingSession(
            obs,
            root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=600.0,
            state=state,
        )

    def _event(
        self, session: RecordingSession, *, active: bool, state: str, path: str | None = None
    ):
        from clm.recordings.workflow.obs import RecordingEvent

        session._handle_record_event(
            RecordingEvent(output_active=active, output_state=state, output_path=path)
        )

    @pytest.mark.parametrize(
        "busy",
        [SessionState.RECORDING, SessionState.PAUSED, SessionState.RENAMING],
    )
    def test_busy_session_refuses(self, mock_obs_session, root: Path, deck: Path, busy):
        path = _record(deck, _ledger_part(TAKE1))
        before = path.read_bytes()
        raw = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv")
        state = _state(RecordingPart(part=1, raw_file=str(raw), recorded_at=TAKE1))
        session = self._session(mock_obs_session, root, state)
        session._state = busy
        with pytest.raises(RuntimeError, match=busy.value):
            session.invalidate_take(
                COURSE, SECTION, DECK_NAME, lang="de", lecture_id=LECTURE, deck_path=deck
            )
        assert raw.exists()
        assert path.read_bytes() == before

    def test_invalidate_after_stop_closes_the_retake_window(
        self, mock_obs_session, root: Path, deck: Path, monkeypatch
    ):
        """The real flow: record, stop, regret inside the retake window."""
        recorded: list[object] = []
        state = CourseRecordingState(course_id=COURSE)
        state.ensure_lecture(LECTURE, DECK_NAME)
        session = RecordingSession(
            mock_obs_session,
            root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=600.0,
            state=state,
            on_state_mutation=recorded.append,
        )
        from clm.recordings.record_provenance import RecordProvenance

        session.arm(
            COURSE,
            SECTION,
            DECK_NAME,
            lang="de",
            lecture_id=LECTURE,
            provenance=RecordProvenance(deck_path=deck, members=rl.deck_members(deck, "de")),
        )
        obs_file = _file(root.parent / "obs" / "out.mkv")
        self._event(session, active=True, state="OBS_WEBSOCKET_OUTPUT_STARTED")
        self._event(
            session,
            active=False,
            state="OBS_WEBSOCKET_OUTPUT_STOPPED",
            path=str(obs_file),
        )
        _wait_for(lambda: session.state is SessionState.ARMED_AFTER_TAKE)
        raw = _dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv"
        assert raw.exists()
        assert len(_parts_in_ledger(deck)) == 1  # the default: it reached the ledger

        result = session.invalidate_take(
            COURSE, SECTION, DECK_NAME, lang="de", lecture_id=LECTURE, deck_path=deck, note="typo"
        )

        assert result.effect == "removed"
        assert _parts_in_ledger(deck) == []
        assert not raw.exists()
        assert (_dir(root, "superseded") / raw.name).exists()
        assert state.lectures[0].parts == []
        assert session.state is SessionState.ARMED  # still armed for the next try
        assert session._retake_timer is None
        assert recorded  # persisted through on_state_mutation

    def test_real_retake_then_invalidate_restores_ledger_state_and_files(
        self, mock_obs_session, root: Path, deck: Path
    ):
        state = CourseRecordingState(course_id=COURSE)
        state.ensure_lecture(LECTURE, DECK_NAME)
        session = self._session(mock_obs_session, root, state)
        from clm.recordings.record_provenance import RecordProvenance

        session.arm(
            COURSE,
            SECTION,
            DECK_NAME,
            lang="de",
            lecture_id=LECTURE,
            provenance=RecordProvenance(deck_path=deck, members=rl.deck_members(deck, "de")),
        )
        raw = _dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv"

        first = _file(root.parent / "obs" / "first.mkv")
        self._event(session, active=True, state="OBS_WEBSOCKET_OUTPUT_STARTED")
        self._event(session, active=False, state="OBS_WEBSOCKET_OUTPUT_STOPPED", path=str(first))
        _wait_for(lambda: session.state is SessionState.ARMED_AFTER_TAKE)
        # The stamps have one-second resolution: make the two takes distinct.
        state.lectures[0].parts[0].recorded_at = TAKE1
        entry_path = rl.ledger_path_for(deck)
        ledger = rl.load(entry_path)
        ledger.decks["slides_t"].parts[0].recorded_at = TAKE1
        rl.save(ledger, entry_path)
        assert raw.read_bytes() == b"first.mkv"

        second = _file(root.parent / "obs" / "second.mkv")
        self._event(session, active=True, state="OBS_WEBSOCKET_OUTPUT_STARTED")
        _wait_for(lambda: session.state is SessionState.RECORDING)
        self._event(session, active=False, state="OBS_WEBSOCKET_OUTPUT_STOPPED", path=str(second))
        _wait_for(
            lambda: (
                session.state is SessionState.ARMED_AFTER_TAKE and raw.read_bytes() == b"second.mkv"
            )
        )
        assert state.lectures[0].parts[0].active_take == 2
        assert [p.recorded_at for p in rl.load(entry_path).decks["slides_t"].superseded] == [TAKE1]

        result = session.invalidate_take(
            COURSE, SECTION, DECK_NAME, lang="de", lecture_id=LECTURE, deck_path=deck
        )

        assert result.effect == "restored"
        assert _parts_in_ledger(deck) == [(1, TAKE1)]
        assert raw.read_bytes() == b"first.mkv"
        assert (_dir(root, "superseded") / raw.name).read_bytes() == b"second.mkv"
        part = state.lectures[0].parts[0]
        assert (part.active_take, part.recorded_at, part.raw_file) == (1, TAKE1, str(raw))

    def test_pending_rename_for_the_deck_is_refused(self, mock_obs_session, root: Path, deck: Path):
        from clm.recordings.workflow.rename_queue import PendingRename

        _record(deck, _ledger_part(TAKE1))
        raw = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv")
        state = _state(RecordingPart(part=1, raw_file=str(raw), recorded_at=TAKE1))
        session = self._session(mock_obs_session, root, state)
        session.pending_renames._pending.append(
            PendingRename(raw, _dir(root, "takes") / f"{DECK_NAME} (take 1)--RAW.mkv", "t")
        )
        with pytest.raises(InvalidationRefused, match="deferred rename"):
            session.invalidate_take(
                COURSE, SECTION, DECK_NAME, lang="de", lecture_id=LECTURE, deck_path=deck
            )

    def test_no_deck_path_updates_state_and_files_and_warns(
        self, mock_obs_session, root: Path, deck: Path
    ):
        raw = _file(_dir(root, "to-process") / f"{DECK_NAME}--RAW.mkv")
        state = _state(RecordingPart(part=1, raw_file=str(raw), recorded_at=TAKE1))
        session = self._session(mock_obs_session, root, state)
        result = session.invalidate_take(COURSE, SECTION, DECK_NAME, lang="de", lecture_id=LECTURE)
        assert result.effect == "removed"
        assert not raw.exists()
        assert any("ledger" in w for w in result.warnings)


def _wait_for(predicate, timeout: float = 30.0) -> None:
    """Poll a timer/thread-driven state with a generous budget, never a fixed sleep."""
    import time

    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.01)


class TestWebRoute:
    @pytest.fixture()
    def app(self, root: Path):
        with patch("clm.recordings.workflow.obs.ObsClient") as mock_obs_class:
            mock_obs = MagicMock()
            mock_obs.connected = True
            mock_obs.connection_state = "connected"
            mock_obs_class.return_value = mock_obs
            mock_obs.connect.side_effect = ConnectionError("OBS not running")

            from clm.recordings.web.app import create_app

            application = create_app(
                recordings_root=root,
                obs_host="localhost",
                obs_port=4455,
                allowed_hosts=["testserver"],
            )
            application.state.session._on_state_mutation = None
            yield application

    @pytest.fixture()
    def client(self, app):
        from fastapi.testclient import TestClient

        return TestClient(app, raise_server_exceptions=False)

    def _seed(self, app, root: Path) -> Path:
        raw = _file(root / "to-process" / "c" / "s" / "Intro--RAW.mkv")
        app.state.recording_states["c"] = CourseRecordingState(
            course_id="c",
            lectures=[
                LectureState(
                    lecture_id="s::Intro",
                    display_name="Intro",
                    parts=[RecordingPart(part=1, raw_file=str(raw), recorded_at=TAKE1)],
                )
            ],
        )
        return raw

    def test_post_invalidates_the_active_take(self, app, client, root: Path):
        raw = self._seed(app, root)
        resp = client.post("/decks/c/s/Intro/invalidate?part=0")
        assert resp.status_code == 200, resp.text
        assert not raw.exists()
        assert (root / "superseded" / "c" / "s" / raw.name).exists()
        assert app.state.recording_states["c"].lectures[0].parts == []

    def test_from_lectures_returns_hx_location(self, app, client, root: Path):
        self._seed(app, root)
        resp = client.post(
            "/decks/c/s/Intro/invalidate?part=0",
            headers={"HX-Request": "true", "HX-Current-URL": "http://testserver/lectures"},
        )
        assert resp.status_code == 200
        assert "HX-Location" in resp.headers

    def test_busy_session_is_409(self, app, client, root: Path):
        raw = self._seed(app, root)
        app.state.session._state = SessionState.RECORDING
        assert client.post("/decks/c/s/Intro/invalidate?part=0").status_code == 409
        assert raw.exists()

    def test_nothing_to_invalidate_is_409(self, app, client):
        assert client.post("/decks/c/s/Nope/invalidate?part=0").status_code == 409

    def test_lower_part_with_later_parts_is_409_and_keeps_all(self, app, client, root: Path):
        raw = self._seed(app, root)
        _file(root / "to-process" / "c" / "s" / "Intro (part 2)--RAW.mkv")
        assert client.post("/decks/c/s/Intro/invalidate?part=0").status_code == 409
        assert raw.exists()

    def test_traversal_in_the_deck_name_is_rejected(self, client):
        assert client.post("/decks/c/s/..%2F..%2Fevil/invalidate").status_code in (400, 404)

    def test_panel_offers_invalidate_on_the_active_take(self, app, client, root: Path):
        self._seed(app, root)
        resp = client.get("/decks/c/s/Intro/takes?part=0")
        assert "/decks/c/s/Intro/invalidate?part=0" in resp.text
        assert "Confirm invalidate" in resp.text

    def test_panel_disables_it_only_while_busy(self, app, client, root: Path):
        self._seed(app, root)
        session = app.state.session
        session.arm("c", "s", "Intro", lecture_id="s::Intro")
        armed = client.get("/decks/c/s/Intro/takes?part=0").text
        assert 'btn-invalidate"' in armed
        fragment = armed.split('btn-invalidate"')[1].split("</button>")[0]
        assert "disabled" not in fragment

        session._state = SessionState.RECORDING
        busy = client.get("/decks/c/s/Intro/takes?part=0").text
        assert "disabled" in busy.split('btn-invalidate"')[1].split("</button>")[0]
