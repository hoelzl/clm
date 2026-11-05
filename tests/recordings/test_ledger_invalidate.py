"""The recordings ledger keeps what a retake replaced, and can invalidate a take (#1093).

Regression tests for the ledger half of #1093/#908: ``record_part`` used to
*replace* the entry with the same identity, so an aborted retake destroyed the
good take's fingerprints with no way back but git.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clm.recordings import ledger as rl

DECK = "slides_t"


def _part(
    recorded_at: str,
    *,
    part: int = 1,
    lang: str = "de",
    course: str = "c-de",
    fp: str | None = None,
) -> rl.LedgerPart:
    return rl.LedgerPart(
        part=part,
        recorded_at=recorded_at,
        course_id=course,
        lang=lang,
        anchor=rl.anchor_for("abc123", False),
        members={"id:s0": fp or f"fp-{recorded_at}"},
        order=["id:s0"],
    )


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / ".clm" / rl.LEDGER_FILENAME


GOOD = "2026-10-01T10:00:00"
ABORTED = "2026-10-21T10:15:24"


class TestRetakeKeepsWhatItReplaced:
    def test_retake_moves_the_replaced_entry_to_superseded(self, path: Path):
        """The root cause of #1093's retake case: the good take must survive."""
        rl.record_part(path, DECK, _part(GOOD))
        rl.record_part(path, DECK, _part(ABORTED))
        entry = rl.load(path).decks[DECK]
        assert [p.recorded_at for p in entry.parts] == [ABORTED]
        assert [p.recorded_at for p in entry.superseded] == [GOOD]
        assert entry.superseded[0].members == {"id:s0": f"fp-{GOOD}"}

    def test_same_stamp_is_the_same_take_not_a_retake(self, path: Path):
        """Re-seeding one take with better evidence replaces in place."""
        rl.record_part(path, DECK, _part(GOOD, fp="old"))
        rl.record_part(path, DECK, _part(GOOD, fp="new"))
        entry = rl.load(path).decks[DECK]
        assert entry.parts[0].members == {"id:s0": "new"}
        assert entry.superseded == []

    def test_other_identities_do_not_supersede(self, path: Path):
        rl.record_part(path, DECK, _part(GOOD))
        rl.record_part(path, DECK, _part(ABORTED, course="other-de"))
        rl.record_part(path, DECK, _part(ABORTED, lang="en"))
        rl.record_part(path, DECK, _part(ABORTED, part=2))
        entry = rl.load(path).decks[DECK]
        assert len(entry.parts) == 4
        assert entry.superseded == []

    def test_history_is_capped_per_identity_oldest_dropped(self, path: Path):
        stamps = [f"2026-10-{day:02d}T10:00:00" for day in range(1, rl.MAX_SUPERSEDED_PER_PART + 4)]
        for stamp in stamps:
            rl.record_part(path, DECK, _part(stamp))
        rl.record_part(path, DECK, _part(GOOD, course="other-de"))
        rl.record_part(path, DECK, _part(ABORTED, course="other-de"))
        entry = rl.load(path).decks[DECK]
        mine = [p.recorded_at for p in entry.superseded if p.course_id == "c-de"]
        assert mine == stamps[-(rl.MAX_SUPERSEDED_PER_PART + 1) : -1]
        assert [p.recorded_at for p in entry.superseded if p.course_id == "other-de"] == [GOOD]


class TestCompatibility:
    def test_ledger_without_new_fields_stays_byte_identical(self, path: Path):
        """A ledger with no retake and no invalidation must not churn in git."""
        rl.record_part(path, DECK, _part(GOOD))
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert set(raw["decks"][DECK]) == {"parts", "ack"}
        assert raw["schema"] == 1
        before = path.read_bytes()
        assert rl.save(rl.load(path), path) is False
        assert path.read_bytes() == before

    def test_old_ledger_without_the_fields_loads(self, path: Path):
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "hash_version": rl.HASH_VERSION,
                    "decks": {
                        DECK: {
                            "ack": None,
                            "parts": [json.loads(_part(GOOD).model_dump_json())],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        entry = rl.load(path).decks[DECK]
        assert entry.superseded == []
        assert entry.invalidated == []

    def test_superseded_entry_keeps_its_own_older_hash_version(self, path: Path):
        """The ``hash_version`` rule: an envelope-only writer must not get promoted."""
        path.parent.mkdir(parents=True)
        raw_part = json.loads(_part(GOOD).model_dump_json())
        del raw_part["hash_version"]
        path.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "hash_version": rl.HASH_VERSION - 1,
                    "decks": {DECK: {"ack": None, "parts": [], "superseded": [raw_part]}},
                }
            ),
            encoding="utf-8",
        )
        assert rl.load(path).decks[DECK].superseded[0].hash_version == rl.HASH_VERSION - 1

    def test_malformed_superseded_is_a_ledger_error(self, path: Path):
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "hash_version": rl.HASH_VERSION,
                    "decks": {DECK: {"parts": [], "superseded": "nope"}},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(rl.LedgerError):
            rl.load(path)

    def test_rename_carries_history_and_audit(self, path: Path):
        rl.record_part(path, DECK, _part(GOOD))
        rl.record_part(path, DECK, _part(ABORTED))
        assert rl.rename_deck_key(path, DECK, "slides_new")
        assert rl.load(path).decks["slides_new"].superseded[0].recorded_at == GOOD


def _entry(path: Path, *parts: rl.LedgerPart) -> rl.DeckEntry:
    for part in parts:
        rl.record_part(path, DECK, part)
    return rl.load(path).decks[DECK]


class TestInvalidatePart:
    def test_invalidating_a_retake_restores_the_previous_take(self, path: Path):
        entry = _entry(path, _part(GOOD), _part(ABORTED))
        outcome = rl.invalidate_part(entry, "c-de", 1, "de", at="T", note="typo")
        assert outcome.invalidated.recorded_at == ABORTED
        assert outcome.restored is not None
        assert outcome.restored.recorded_at == GOOD
        assert [p.recorded_at for p in entry.parts] == [GOOD]
        assert entry.parts[0].members == {"id:s0": f"fp-{GOOD}"}
        assert entry.superseded == []
        assert [(t.recorded_at, t.at, t.note) for t in entry.invalidated] == [
            (ABORTED, "T", "typo")
        ]

    def test_invalidating_the_only_take_removes_the_part_and_the_ack(self, path: Path):
        entry = _entry(path, _part(GOOD))
        entry.ack = rl.DeckAck(at="2026-10-02T00:00:00", members={"de:id:s0": "x"})
        outcome = rl.invalidate_part(entry, "c-de", 1, "de")
        assert outcome.restored is None
        assert outcome.ack_dropped
        assert entry.parts == []
        assert entry.ack is None

    def test_walking_back_through_several_retakes(self, path: Path):
        third = "2026-10-22T10:00:00"
        entry = _entry(path, _part(GOOD), _part(ABORTED), _part(third))
        assert rl.invalidate_part(entry, "c-de", 1, "de").restored.recorded_at == ABORTED
        assert rl.invalidate_part(entry, "c-de", 1, "de").restored.recorded_at == GOOD
        assert rl.invalidate_part(entry, "c-de", 1, "de").restored is None
        assert [t.recorded_at for t in entry.invalidated] == [third, ABORTED, GOOD]

    def test_invalidating_a_superseded_take_leaves_the_active_entry_alone(self, path: Path):
        third = "2026-10-22T10:00:00"
        entry = _entry(path, _part(GOOD), _part(ABORTED), _part(third))
        outcome = rl.invalidate_part(entry, "c-de", 1, "de", recorded_at=ABORTED)
        assert not outcome.was_active
        assert outcome.restored is None
        assert [p.recorded_at for p in entry.parts] == [third]
        assert [p.recorded_at for p in entry.superseded] == [GOOD]

    def test_unknown_stamp_is_refused_and_names_the_known_ones(self, path: Path):
        entry = _entry(path, _part(GOOD), _part(ABORTED))
        with pytest.raises(rl.InvalidationRefused) as exc:
            rl.invalidate_part(entry, "c-de", 1, "de", recorded_at="2020-01-01T00:00:00")
        assert GOOD in str(exc.value) and ABORTED in str(exc.value)
        assert [p.recorded_at for p in entry.parts] == [ABORTED]

    def test_no_such_part_is_refused(self, path: Path):
        entry = _entry(path, _part(GOOD))
        with pytest.raises(rl.InvalidationRefused):
            rl.invalidate_part(entry, "c-de", 2, "de")

    def test_other_identities_are_untouched(self, path: Path):
        other = _part(ABORTED, course="other-de")
        entry = _entry(path, _part(GOOD), other, _part(ABORTED, lang="en"))
        rl.invalidate_part(entry, "c-de", 1, "de")
        assert {rl.part_identity(p) for p in entry.parts} == {
            ("other-de", 1, "de"),
            ("c-de", 1, "en"),
        }

    def test_fallback_restores_when_the_ledger_has_no_history(self, path: Path):
        """A ledger written before history existed: the state file supplies the take."""
        entry = _entry(path, _part(ABORTED))
        fallback = _part(GOOD)
        outcome = rl.invalidate_part(entry, "c-de", 1, "de", fallback=fallback)
        assert outcome.restored == fallback
        assert entry.parts == [fallback]

    def test_ledger_history_beats_the_fallback(self, path: Path):
        entry = _entry(path, _part(GOOD), _part(ABORTED))
        outcome = rl.invalidate_part(
            entry, "c-de", 1, "de", fallback=_part("2026-01-01T00:00:00", fp="f")
        )
        assert outcome.restored.recorded_at == GOOD


class TestAck:
    def test_ack_given_after_the_invalid_take_is_dropped_on_restore(self, path: Path):
        """That ack judged the invalid take; the restored take's drift was never seen."""
        entry = _entry(path, _part(GOOD), _part(ABORTED))
        entry.ack = rl.DeckAck(at="2026-10-22T00:00:00", members={"de:id:s0": "x"})
        outcome = rl.invalidate_part(entry, "c-de", 1, "de")
        assert outcome.ack_dropped
        assert entry.ack is None

    def test_ack_given_before_the_invalid_take_survives_a_restore(self, path: Path):
        entry = _entry(path, _part(GOOD), _part(ABORTED))
        entry.ack = rl.DeckAck(at="2026-10-02T00:00:00", members={"de:id:s0": "x"})
        assert not rl.invalidate_part(entry, "c-de", 1, "de").ack_dropped
        assert entry.ack is not None

    def test_removing_one_part_keeps_the_ack_for_the_rest(self, path: Path):
        entry = _entry(path, _part(GOOD), _part(ABORTED, part=2))
        entry.ack = rl.DeckAck(at="2026-10-22T00:00:00", members={"de:id:s0": "x"})
        assert not rl.invalidate_part(entry, "c-de", 2, "de").ack_dropped
        assert entry.ack is not None

    def test_a_language_with_no_part_left_loses_its_ack_keys(self, path: Path):
        """Otherwise a later recording in that language would read as covered."""
        entry = _entry(path, _part(GOOD), _part(GOOD, lang="en"))
        entry.ack = rl.DeckAck(
            at="2026-10-22T00:00:00",
            members={"de:id:s0": "x", "en:id:s0": "y"},
            order=["de:id:s0", "en:id:s0"],
        )
        rl.invalidate_part(entry, "c-de", 1, "en")
        assert entry.ack.members == {"de:id:s0": "x"}
        assert entry.ack.order == ["de:id:s0"]

    def test_superseded_invalidation_never_touches_the_ack(self, path: Path):
        entry = _entry(path, _part(GOOD), _part(ABORTED))
        entry.ack = rl.DeckAck(at="2026-10-22T00:00:00", members={"de:id:s0": "x"})
        outcome = rl.invalidate_part(entry, "c-de", 1, "de", recorded_at=GOOD)
        assert not outcome.ack_dropped
        assert entry.ack is not None


class TestAuditRoundTrip:
    def test_audit_survives_a_save(self, path: Path):
        ledger = rl.RecordingsLedger()
        ledger.decks[DECK] = _entry(path, _part(GOOD), _part(ABORTED))
        rl.invalidate_part(ledger.decks[DECK], "c-de", 1, "de", at="T", note="n")
        rl.save(ledger, path)
        raw = json.loads(path.read_text(encoding="utf-8"))["decks"][DECK]
        assert "superseded" not in raw
        assert raw["invalidated"] == [
            {
                "at": "T",
                "course_id": "c-de",
                "lang": "de",
                "note": "n",
                "part": 1,
                "recorded_at": ABORTED,
            }
        ]
        assert rl.is_invalidated(rl.load(path).decks[DECK], _part(ABORTED))
        assert not rl.is_invalidated(rl.load(path).decks[DECK], _part(GOOD))
