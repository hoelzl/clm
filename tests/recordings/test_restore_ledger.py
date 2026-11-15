"""The dashboard's Restore updates the committed recordings ledger (#1196).

Before the fix, Restore swapped the files and the state file but left the
ledger naming the take that was active before, so ``report`` judged a take
that no longer stood. Restore now does to the ledger what ``invalidate``
(#1093) does: the restored take becomes the part's entry, the replaced one
moves to ``superseded``, and the ack follows ``invalidate``'s rule.

Only temp fixtures: no real recordings folder is touched.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from clm.recordings import ledger as rl
from clm.recordings import state as state_module
from clm.recordings.report import build_report
from clm.recordings.state import RecordingPart, TakeRecord
from clm.recordings.workflow.directories import ensure_root
from clm.recordings.workflow.invalidate import invalidate_take
from clm.recordings.workflow.session import RecordingSession
from tests.recordings.test_invalidate import (
    COURSE,
    DECK_NAME,
    LECTURE,
    SECTION,
    TAKE1,
    TAKE2,
    TAKE3,
    _dir,
    _file,
    _ledger_part,
    _parts_in_ledger,
    _record,
    _request,
    _state,
)
from tests.recordings.test_ledger import DE0, EN0


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


def _entry(deck: Path) -> rl.DeckEntry:
    return rl.load(rl.ledger_path_for(deck)).decks["slides_t"]


def _ack(deck: Path, at: str) -> None:
    path = rl.ledger_path_for(deck)
    ledger = rl.load(path)
    ledger.decks["slides_t"].ack = rl.DeckAck(at=at, members={"de:id:s0": "x"}, order=[])
    rl.save(ledger, path)


# ---------------------------------------------------------------------------
# restore_part: the ledger move itself
# ---------------------------------------------------------------------------


class TestRestorePart:
    def test_swaps_the_active_entry_with_the_superseded_one(self, deck: Path):
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        entry = _entry(deck)
        outcome = rl.restore_part(entry, COURSE, 1, "de", TAKE1)
        assert outcome is not None
        assert outcome.restored.recorded_at == TAKE1
        assert outcome.demoted is not None and outcome.demoted.recorded_at == TAKE2
        assert [p.recorded_at for p in entry.parts] == [TAKE1]
        assert [p.recorded_at for p in entry.superseded] == [TAKE2]

    def test_already_active_is_a_no_op(self, deck: Path):
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        entry = _entry(deck)
        assert rl.restore_part(entry, COURSE, 1, "de", TAKE2) is None
        assert [p.recorded_at for p in entry.parts] == [TAKE2]

    def test_take_unknown_to_the_ledger_comes_back_as_the_fallback(self, deck: Path):
        _record(deck, _ledger_part(TAKE2))
        entry = _entry(deck)
        fallback = _ledger_part(TAKE1, fp="from-state")
        outcome = rl.restore_part(entry, COURSE, 1, "de", TAKE1, fallback=fallback)
        assert outcome is not None and outcome.restored is fallback
        assert [p.recorded_at for p in entry.parts] == [TAKE1]
        assert [p.recorded_at for p in entry.superseded] == [TAKE2]

    def test_untracked_part_stays_untracked(self, deck: Path):
        """A part the ledger never held is not invented by a Restore."""
        _record(deck, _ledger_part(TAKE1, part=1))
        entry = _entry(deck)
        assert (
            rl.restore_part(entry, COURSE, 2, "de", TAKE2, fallback=_ledger_part(TAKE2, 2)) is None
        )
        assert [(p.part, p.recorded_at) for p in entry.parts] == [(1, TAKE1)]

    def test_other_identities_are_untouched(self, deck: Path):
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2), _ledger_part(TAKE3, part=2))
        entry = _entry(deck)
        rl.restore_part(entry, COURSE, 1, "de", TAKE1)
        assert sorted((p.part, p.recorded_at) for p in entry.parts) == [(1, TAKE1), (2, TAKE3)]

    def test_restore_then_retake_then_invalidate_round_trips(self, deck: Path):
        """The ledger and the history stay consistent across the three verbs."""
        path = _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        ledger = rl.load(path)
        rl.restore_part(ledger.decks["slides_t"], COURSE, 1, "de", TAKE1)
        rl.save(ledger, path)
        rl.record_part(path, "slides_t", _ledger_part(TAKE3))
        entry = _entry(deck)
        assert [p.recorded_at for p in entry.parts] == [TAKE3]
        assert sorted(p.recorded_at for p in entry.superseded) == [TAKE1, TAKE2]


class TestAck:
    """#1172's rule: an ack given after the replaced take was recorded judged that take."""

    def test_ack_after_the_replaced_take_is_dropped(self, deck: Path):
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        _ack(deck, "2026-10-21T12:00:00")
        entry = _entry(deck)
        outcome = rl.restore_part(entry, COURSE, 1, "de", TAKE1)
        assert outcome is not None and outcome.ack_dropped
        assert entry.ack is None

    def test_ack_before_the_replaced_take_stays(self, deck: Path):
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        _ack(deck, "2026-10-05T12:00:00")
        entry = _entry(deck)
        outcome = rl.restore_part(entry, COURSE, 1, "de", TAKE1)
        assert outcome is not None and not outcome.ack_dropped
        assert entry.ack is not None


# ---------------------------------------------------------------------------
# The dashboard path: RecordingSession.restore_take and the web route
# ---------------------------------------------------------------------------


def _history(root: Path) -> tuple[RecordingPart, dict[str, Path]]:
    """Active take 2 (archive/final), take 1 in takes/; stamps TAKE2 / TAKE1."""
    arc, fin, takes = _dir(root, "archive"), _dir(root, "final"), _dir(root, "takes")
    files = {
        "a_raw": _file(arc / f"{DECK_NAME}--RAW.mkv"),
        "a_fin": _file(fin / f"{DECK_NAME}.mp4"),
        "t1_raw": _file(takes / f"{DECK_NAME} (take 1)--RAW.mkv"),
        "t1_fin": _file(takes / f"{DECK_NAME} (take 1).mp4"),
    }
    part = RecordingPart(
        part=1,
        active_take=2,
        raw_file=str(files["a_raw"]),
        processed_file=str(files["a_fin"]),
        status="processed",
        recorded_at=TAKE2,
        takes=[
            TakeRecord(
                take=1,
                raw_file=str(files["t1_raw"]),
                processed_file=str(files["t1_fin"]),
                status="processed",
                recorded_at=TAKE1,
                superseded_at=TAKE2,
            )
        ],
    )
    return part, files


def _session(root: Path, state) -> RecordingSession:
    return RecordingSession(
        MagicMock(),
        root,
        stability_interval=0.01,
        stability_checks=1,
        short_take_seconds=0.0,
        retake_window_seconds=0.0,
        state=state,
    )


class TestSessionRestore:
    def test_restore_moves_the_ledger_to_the_restored_take(self, deck: Path, root: Path):
        """Regression test for #1196: the ledger followed neither files nor state."""
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        part, _ = _history(root)
        state = _state(part)
        _session(root, state).restore_take(
            COURSE, SECTION, DECK_NAME, 1, lang="de", lecture_id=LECTURE, deck_path=deck
        )
        assert state.lectures[0].parts[0].recorded_at == TAKE1
        entry = _entry(deck)
        assert [p.recorded_at for p in entry.parts] == [TAKE1]
        assert [p.recorded_at for p in entry.superseded] == [TAKE2]

    def test_report_judges_the_restored_take(self, deck: Path, root: Path):
        """`report`'s drift is the restored take's: matching fingerprints read as no drift."""
        head = rl.deck_members(deck, "de")
        good = _ledger_part(TAKE1).model_copy(update={"members": head, "order": rl.id_order(head)})
        _record(deck, good, _ledger_part(TAKE2))
        assert build_report(deck).decks[0].severity != "none"
        part, _ = _history(root)
        _session(root, _state(part)).restore_take(
            COURSE, SECTION, DECK_NAME, 1, lang="de", lecture_id=LECTURE, deck_path=deck
        )
        [row] = build_report(deck).decks
        assert [p.recorded_at for p in row.parts] == [TAKE1]
        assert row.severity == "none"

    def test_legacy_ledger_without_history_rebuilds_the_take_from_state(
        self, deck: Path, root: Path
    ):
        _record(deck, _ledger_part(TAKE2))
        part, _ = _history(root)
        _session(root, _state(part)).restore_take(
            COURSE, SECTION, DECK_NAME, 1, lang="de", lecture_id=LECTURE, deck_path=deck
        )
        [restored] = _entry(deck).parts
        assert restored.recorded_at == TAKE1
        assert restored.evidence == "anchor"
        assert restored.anchor.kind == "unanchored"  # the take carried no commit

    def test_without_deck_path_the_ledger_is_untouched(self, deck: Path, root: Path):
        path = _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        before = path.read_bytes()
        part, _ = _history(root)
        _session(root, _state(part)).restore_take(
            COURSE, SECTION, DECK_NAME, 1, lang="de", lecture_id=LECTURE
        )
        assert path.read_bytes() == before

    def test_no_ledger_file_is_not_created(self, deck: Path, root: Path):
        part, _ = _history(root)
        _session(root, _state(part)).restore_take(
            COURSE, SECTION, DECK_NAME, 1, lang="de", lecture_id=LECTURE, deck_path=deck
        )
        assert not rl.ledger_path_for(deck).exists()

    def test_unreadable_ledger_is_left_alone_and_restore_still_succeeds(
        self, deck: Path, root: Path
    ):
        path = rl.ledger_path_for(deck)
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        part, _ = _history(root)
        state = _state(part)
        _session(root, state).restore_take(
            COURSE, SECTION, DECK_NAME, 1, lang="de", lecture_id=LECTURE, deck_path=deck
        )
        assert path.read_text(encoding="utf-8") == "{not json"
        assert state.lectures[0].parts[0].active_take == 1

    def test_invalidate_after_restore_agrees_with_the_ledger(self, deck: Path, root: Path):
        """The stale-ledger warning #1172 had to give after a Restore is gone."""
        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        part, _ = _history(root)
        state = _state(part)
        _session(root, state).restore_take(
            COURSE, SECTION, DECK_NAME, 1, lang="de", lecture_id=LECTURE, deck_path=deck
        )
        result = invalidate_take(_request(deck, state=state, lecture_id=LECTURE))
        assert not any("left as it is" in w for w in result.warnings)
        assert result.restored_recorded_at == TAKE2
        assert _parts_in_ledger(deck) == [(1, TAKE2)]


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

    def test_restore_route_passes_the_deck_and_updates_the_ledger(
        self, app, root: Path, deck: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from fastapi.testclient import TestClient

        from clm.recordings.web import routes

        _record(deck, _ledger_part(TAKE1), _ledger_part(TAKE2))
        part, _ = _history(root)
        app.state.recording_states[COURSE] = _state(part)
        app.state.lang = "de"
        monkeypatch.setattr(routes, "_get_lang", lambda request: "de")
        monkeypatch.setattr(
            routes,
            "_build_deck_provenance",
            lambda request, section, deck_name, lang: SimpleNamespace(deck_path=deck),
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(f"/decks/{COURSE}/{SECTION}/{DECK_NAME}/takes/1/restore?part=0")
        assert resp.status_code == 200, resp.text
        assert _parts_in_ledger(deck) == [(1, TAKE1)]
