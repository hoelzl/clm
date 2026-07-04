"""Tests for :mod:`clm.slides.doc_ledger` (#520 Phase 3, design §5).

The member-keyed committed trust store: round-trip fidelity, the
``complete=False`` ledger baseline, hash-version lazy migration, the §7.3
pos→id key migration at record time, and — post the Phase-4 cutover — the
legacy-envelope handling: schema-1/2 files still load, but the v1
``slides`` / ``idless`` sections are ignored and dropped on the next save.
"""

from __future__ import annotations

import json
from pathlib import Path

from attrs import evolve

from clm.slides import doc_ledger
from clm.slides.bilingual_doc import BilingualDeck
from clm.slides.doc_lenses import parse_bundle
from clm.slides.sync_diff import baseline_from_deck, diff_deck

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _localized(slug: str, lang: str, text: str) -> str:
    return f'# %% [markdown] lang="{lang}" slide_id="{slug}"\n# {text}\n\n'


def _shared_code(name: str, value: int = 1) -> str:
    return f'# %% tags=["keep"]\n{name} = {value}\n\n'


def _build(*parts: str) -> str:
    return "".join(parts).rstrip("\n") + "\n"


DE0 = _build(
    HEADER_DE,
    _slide("s0", "de", "Titel"),
    _shared_code("x"),
    _localized("s0-m", "de", "DE Text"),
)
EN0 = _build(
    HEADER_EN,
    _slide("s0", "en", "Title"),
    _shared_code("x"),
    _localized("s0-m", "en", "EN text"),
)


def _parse(de: str, en: str) -> BilingualDeck:
    outcome = parse_bundle(de, en)
    assert outcome.deck is not None
    return outcome.deck


def _recorded_ledger(de: str = DE0, en: str = EN0) -> doc_ledger.TopicLedger:
    ledger = doc_ledger.TopicLedger()
    doc_ledger.record_deck_snapshot(ledger, "slides_x", _parse(de, en), provenance="record")
    return ledger


class TestRoundTrip:
    def test_full_record_round_trips_through_json(self, tmp_path: Path):
        ledger = _recorded_ledger()
        path = tmp_path / ".clm" / "sync-ledger.json"
        doc_ledger.save(ledger, path)
        loaded = doc_ledger.load(path)
        assert loaded.decks.keys() == {"slides_x"}
        original = ledger.decks["slides_x"]
        restored = loaded.decks["slides_x"]
        assert restored.members == original.members
        assert restored.group_order == original.group_order
        assert restored.group_order_by_side == original.group_order_by_side
        assert restored.member_order == original.member_order
        assert restored.preamble_fps == original.preamble_fps

    def test_ledger_baseline_equals_deck_snapshot_but_incomplete(self):
        ledger = _recorded_ledger()
        base = doc_ledger.baseline_from_ledger(ledger.decks["slides_x"])
        snapshot = baseline_from_deck(_parse(DE0, EN0))
        assert base.complete is False
        assert base.members == snapshot.members
        assert base.member_order == snapshot.member_order
        assert base.group_order_by_side == snapshot.group_order_by_side
        assert base.preamble_fps == snapshot.preamble_fps

    def test_noop_diff_against_the_recorded_ledger_is_clean(self):
        ledger = _recorded_ledger()
        base = doc_ledger.baseline_from_ledger(ledger.decks["slides_x"])
        diff = diff_deck(_parse(DE0, EN0), base)
        assert diff.is_clean, [(i.action, i.key) for i in diff.items]

    def test_absent_or_malformed_file_loads_empty(self, tmp_path: Path):
        assert doc_ledger.load(tmp_path / "missing.json").decks == {}
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert doc_ledger.load(bad).decks == {}
        wrong = tmp_path / "wrong.json"
        wrong.write_text('{"schema": 99, "decks": {}}', encoding="utf-8")
        assert doc_ledger.load(wrong).decks == {}


class TestTrustSemantics:
    def test_new_member_is_cold_never_an_add(self):
        ledger = _recorded_ledger()
        base = doc_ledger.baseline_from_ledger(ledger.decks["slides_x"])
        de = _build(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
            _shared_code("x"),
            _localized("s0-m", "de", "DE Text"),
            _localized("s0-new", "de", "Neu"),
        )
        en = _build(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _shared_code("x"),
            _localized("s0-m", "en", "EN text"),
            _localized("s0-new", "en", "New"),
        )
        diff = diff_deck(_parse(de, en), base)
        assert [(i.outcome, i.action) for i in diff.items] == [("unverified", "verify_cold")]

    def test_stale_hash_version_drops_to_cold(self):
        ledger = _recorded_ledger()
        deck_ledger = ledger.decks["slides_x"]
        key = "id:s0-m"
        deck_ledger.members[key] = evolve(deck_ledger.members[key], hash_version=0)
        base = doc_ledger.baseline_from_ledger(deck_ledger)
        assert key not in base.members
        diff = diff_deck(_parse(DE0, EN0), base)
        assert [(i.key, i.action) for i in diff.items] == [(key, "verify_cold")]

    def test_partial_record_of_a_pos_key_rerecords_its_whole_pool(self):
        # Positional ordinals renumber together: a per-entry patch would mix
        # ordinal generations (aliased keys), so a pos --member re-records
        # the (group, kind) pool wholesale.
        ledger = _recorded_ledger()
        de = _build(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
            _shared_code("z", 9),  # new cell shifts the pool ordinals
            _shared_code("x"),
            _localized("s0-m", "de", "DE Text"),
        )
        en = _build(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _shared_code("z", 9),
            _shared_code("x"),
            _localized("s0-m", "en", "EN text"),
        )
        recorded, _ = doc_ledger.record_deck_snapshot(
            ledger,
            "slides_x",
            _parse(de, en),
            provenance="record",
            member_keys={"pos:s0/code/1"},
        )
        assert recorded == 2  # the whole pool, not one aliased ordinal
        base = doc_ledger.baseline_from_ledger(ledger.decks["slides_x"])
        diff = diff_deck(_parse(de, en), base)
        assert not any(i.key.startswith("pos:s0/code") for i in diff.items)

    def test_partial_record_upserts_only_the_named_members(self):
        ledger = _recorded_ledger()
        de = DE0.replace("DE Text", "DE Text NEU")
        recorded, _ = doc_ledger.record_deck_snapshot(
            ledger,
            "slides_x",
            _parse(de, EN0),
            provenance="agent",
            member_keys={"id:s0-m"},
        )
        assert recorded == 1
        deck_ledger = ledger.decks["slides_x"]
        assert deck_ledger.members["id:s0-m"].provenance == "agent"
        # The untouched siblings keep their original provenance.
        assert deck_ledger.members["id:s0"].provenance == "record"
        base = doc_ledger.baseline_from_ledger(deck_ledger)
        assert diff_deck(_parse(de, EN0), base).is_clean

    def test_full_record_sweeps_stale_members(self):
        ledger = _recorded_ledger()
        de = _build(HEADER_DE, _slide("s0", "de", "Titel"), _shared_code("x"))
        en = _build(HEADER_EN, _slide("s0", "en", "Title"), _shared_code("x"))
        doc_ledger.record_deck_snapshot(ledger, "slides_x", _parse(de, en), provenance="record")
        assert "id:s0-m" not in ledger.decks["slides_x"].members


class TestKeyMigration:
    def test_pos_to_id_migration_is_detected_and_logged(self):
        ledger = _recorded_ledger()
        stamped_de = DE0.replace(
            '# %% tags=["keep"]\nx = 1', '# %% tags=["keep"] slide_id="x-cell"\nx = 1'
        )
        stamped_en = EN0.replace(
            '# %% tags=["keep"]\nx = 1', '# %% tags=["keep"] slide_id="x-cell"\nx = 1'
        )
        recorded, migrations = doc_ledger.record_deck_snapshot(
            ledger, "slides_x", _parse(stamped_de, stamped_en), provenance="record"
        )
        assert recorded > 0
        assert migrations == {"pos:s0/code/0": "id:x-cell"}
        members = ledger.decks["slides_x"].members
        assert "id:x-cell" in members
        assert "pos:s0/code/0" not in members


class TestDeckKeys:
    def test_deck_key_strips_language_and_suffix(self):
        assert doc_ledger.deck_key_for(Path("slides_intro.de.py")) == "slides_intro"
        assert doc_ledger.deck_key_for(Path("slides_intro.en.py")) == "slides_intro"
        assert doc_ledger.deck_key_for(Path("slides_intro.de.cpp")) == "slides_intro"

    def test_two_decks_share_one_topic_ledger(self, tmp_path: Path):
        ledger = doc_ledger.TopicLedger()
        doc_ledger.record_deck_snapshot(ledger, "slides_a", _parse(DE0, EN0), provenance="record")
        doc_ledger.record_deck_snapshot(ledger, "slides_b", _parse(DE0, EN0), provenance="record")
        path = tmp_path / "ledger.json"
        doc_ledger.save(ledger, path)
        assert doc_ledger.load(path).decks.keys() == {"slides_a", "slides_b"}


class TestLegacyEnvelopeDropsOnSave:
    """Post-cutover (#520 Phase 4): the v1 sections load as nothing, save as gone."""

    _V1_SLIDES = {
        "s1": {
            "slide": {
                "de_hash": "d1",
                "en_hash": "e1",
                "construct": None,
                "confirmed_commit": None,
                "confirmed_by": "bless",
                "confirmed_oracle": "structural",
                "hash_version": 3,
            }
        }
    }

    def _v1_file(self, tmp_path: Path) -> Path:
        path = tmp_path / "sync-ledger.json"
        path.write_text(
            json.dumps({"schema": 1, "slides": self._V1_SLIDES}),
            encoding="utf-8",
        )
        return path

    def test_schema_1_file_loads_as_empty_store(self, tmp_path: Path):
        path = self._v1_file(tmp_path)
        assert doc_ledger.load(path).decks == {}

    def test_save_over_a_v1_file_drops_the_legacy_sections(self, tmp_path: Path):
        path = self._v1_file(tmp_path)
        ledger = doc_ledger.load(path)
        doc_ledger.record_deck_snapshot(ledger, "slides_x", _parse(DE0, EN0), provenance="record")
        doc_ledger.save(ledger, path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema"] == 2
        assert set(data.keys()) == {"schema", "decks"}  # v1 "slides" is gone
        assert "slides_x" in data["decks"]

    def test_schema_2_coexistence_envelope_keeps_decks_drops_v1(self, tmp_path: Path):
        # A pre-cutover Phase-3 file carrying BOTH engines' sections: the decks
        # section survives the load/save round trip; the v1 sections do not.
        ledger = doc_ledger.TopicLedger()
        doc_ledger.record_deck_snapshot(ledger, "slides_x", _parse(DE0, EN0), provenance="record")
        path = tmp_path / "sync-ledger.json"
        doc_ledger.save(ledger, path)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["slides"] = self._V1_SLIDES
        data["idless"] = {}
        path.write_text(json.dumps(data), encoding="utf-8")

        loaded = doc_ledger.load(path)
        assert "slides_x" in loaded.decks
        assert loaded.decks["slides_x"].members == ledger.decks["slides_x"].members
        doc_ledger.save(loaded, path)
        again = json.loads(path.read_text(encoding="utf-8"))
        assert set(again.keys()) == {"schema", "decks"}
        assert "slides_x" in again["decks"]
