"""Tests for the P3 bilingual lock (design §3.5) over the v3 sync engine.

A language is editable iff the *other* split half is clean relative to the
committed per-topic sync ledger — the only trust store. The tests drive the
real ``load_bundle`` + ``diff_bundle`` path (read-only, LLM-free): the ledger
is seeded via :func:`record_pair` (the ``clm slides sync record`` recipe) and
dirtiness is produced by editing real files on disk. A never-recorded pair is
all-cold — every member is a framed ``verify_cold`` item — which locks BOTH
halves until a record blesses the state.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clm.web.app import create_app
from clm.web.studio.service import LanguageLockedError, StudioService

from .conftest import Bilingual, Course, record_pair

TOKEN = "test-studio-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _dirty_de(bilingual: Bilingual) -> None:
    """Edit only the DE half's localized slide cell (→ items with de_to_en)."""
    text = bilingual.de_path.read_text(encoding="utf-8")
    bilingual.de_path.write_text(text.replace("Willkommen", "Herzlich willkommen"), "utf-8")


def _dirty_en(bilingual: Bilingual) -> None:
    """Edit only the EN half's localized slide cell (→ items with en_to_de)."""
    text = bilingual.en_path.read_text(encoding="utf-8")
    bilingual.en_path.write_text(text.replace("Welcome", "A warm welcome"), "utf-8")


class TestNoTwinIsUnlocked:
    def test_single_language_deck_is_not_a_pair(self, service: StudioService, course: Course):
        view = service.open_deck(course.deck_id)
        assert view.lock.is_pair is False
        assert view.lock.editable is True

    def test_writes_unaffected_without_twin(self, service: StudioService, course: Course):
        view = service.open_deck(course.deck_id)
        slide = next(c for c in view.cells if c.role == "slide")
        # No twin → no lock → the P1 edit path still works.
        result = service.edit_body(
            course.deck_id,
            slide.slide_id,
            slide.role,
            "# x\n#\n# y",
            expected_deck_version=view.deck_version,
            expected_cell_hash=slide.content_hash,
        )
        assert result.ok


class TestColdStartLocked:
    def test_unrecorded_pair_locks_both(
        self, bilingual_service: StudioService, bilingual: Bilingual
    ):
        # No ledger entry → every member is cold (verify_cold) → judgment needed
        # → both halves lock until `clm slides sync record` blesses the state.
        de = bilingual_service.open_deck(bilingual.de_id)
        en = bilingual_service.open_deck(bilingual.en_id)
        assert de.lock.is_pair is True
        assert de.lock.lang == "de"
        assert de.lock.other_lang == "en"
        assert de.lock.twin_deck_id == bilingual.en_id
        assert de.lock.baseline == "ledger"
        assert de.lock.editable is False and de.lock.has_conflicts is True
        assert en.lock.editable is False and en.lock.has_conflicts is True


class TestLedgerDerivedLock:
    def test_de_dirty_locks_en(self, bilingual_service: StudioService, bilingual: Bilingual):
        # DE drifted (items direction de_to_en) → DE is the source; EN is locked.
        record_pair(bilingual.de_path, bilingual.en_path)
        _dirty_de(bilingual)
        de = bilingual_service.open_deck(bilingual.de_id)
        en = bilingual_service.open_deck(bilingual.en_id)
        assert de.lock.editable is True and de.lock.other_stale is True
        assert en.lock.editable is False
        assert en.lock.locked_reason and "DE" in en.lock.locked_reason

    def test_en_dirty_locks_de(self, bilingual_service: StudioService, bilingual: Bilingual):
        record_pair(bilingual.de_path, bilingual.en_path)
        _dirty_en(bilingual)
        de = bilingual_service.open_deck(bilingual.de_id)
        en = bilingual_service.open_deck(bilingual.en_id)
        assert en.lock.editable is True and en.lock.other_stale is True
        assert de.lock.editable is False
        assert de.lock.locked_reason and "EN" in de.lock.locked_reason

    def test_conflict_locks_both(self, bilingual_service: StudioService, bilingual: Bilingual):
        # The same member edited on BOTH sides → a framed conflict (direction
        # "both") → judgment needed → both halves lock.
        record_pair(bilingual.de_path, bilingual.en_path)
        _dirty_de(bilingual)
        _dirty_en(bilingual)
        de = bilingual_service.open_deck(bilingual.de_id)
        en = bilingual_service.open_deck(bilingual.en_id)
        assert de.lock.editable is False and de.lock.has_conflicts is True
        assert en.lock.editable is False and en.lock.has_conflicts is True

    def test_clean_pair_both_editable(self, bilingual_service: StudioService, bilingual: Bilingual):
        record_pair(bilingual.de_path, bilingual.en_path)
        de = bilingual_service.open_deck(bilingual.de_id)
        en = bilingual_service.open_deck(bilingual.en_id)
        assert de.lock.editable is True and en.lock.editable is True
        assert de.lock.other_stale is False and en.lock.other_stale is False
        assert de.lock.baseline == "ledger"

    def test_normalize_refusal_locks_both(
        self, bilingual_service: StudioService, bilingual: Bilingual
    ):
        # A pair the doc lenses refuse to normalize (here: a duplicate slide_id
        # on one side) cannot be trusted → judgment needed → both halves lock.
        record_pair(bilingual.de_path, bilingual.en_path)
        text = bilingual.en_path.read_text(encoding="utf-8")
        bilingual.en_path.write_text(
            text.replace('slide_id="intro-notes"', 'slide_id="intro-welcome"'), "utf-8"
        )
        de = bilingual_service.open_deck(bilingual.de_id)
        en = bilingual_service.open_deck(bilingual.en_id)
        assert de.lock.editable is False and de.lock.has_conflicts is True
        assert en.lock.editable is False and en.lock.has_conflicts is True
        assert de.lock.baseline == "ledger"


class TestLockEnforcement:
    def test_edit_on_locked_language_raises(
        self, bilingual_service: StudioService, bilingual: Bilingual
    ):
        record_pair(bilingual.de_path, bilingual.en_path)
        _dirty_de(bilingual)
        en = bilingual_service.open_deck(bilingual.en_id)  # EN is locked
        slide = next(c for c in en.cells if c.role == "slide")
        with pytest.raises(LanguageLockedError):
            bilingual_service.edit_body(
                bilingual.en_id,
                slide.slide_id,
                slide.role,
                "# changed",
                expected_deck_version=en.deck_version,
                expected_cell_hash=slide.content_hash,
            )

    def test_insert_on_locked_language_raises(
        self, bilingual_service: StudioService, bilingual: Bilingual
    ):
        record_pair(bilingual.de_path, bilingual.en_path)
        _dirty_de(bilingual)
        en = bilingual_service.open_deck(bilingual.en_id)
        with pytest.raises(LanguageLockedError):
            bilingual_service.insert_cell(
                bilingual.en_id,
                role="slide",
                body="# x",
                expected_deck_version=en.deck_version,
            )

    def test_edit_on_source_language_succeeds(
        self, bilingual_service: StudioService, bilingual: Bilingual
    ):
        record_pair(bilingual.de_path, bilingual.en_path)
        _dirty_de(bilingual)
        de = bilingual_service.open_deck(bilingual.de_id)  # DE is the editable source
        slide = next(c for c in de.cells if c.role == "slide")
        result = bilingual_service.edit_body(
            bilingual.de_id,
            slide.slide_id,
            slide.role,
            "# geändert\n#\n# text",
            expected_deck_version=de.deck_version,
            expected_cell_hash=slide.content_hash,
        )
        assert result.ok


class TestLockOverHttp:
    def test_locked_write_is_423_with_reason(self, bilingual: Bilingual):
        record_pair(bilingual.de_path, bilingual.en_path)
        _dirty_de(bilingual)
        app = create_app(
            db_path=bilingual.slides_dir.parent / "jobs.db",
            spec_path=bilingual.spec_path,
            studio_token=TOKEN,
        )
        client = TestClient(app)
        body = client.get(f"/api/studio/deck?id={bilingual.en_id}", headers=AUTH).json()
        assert body["lock"]["editable"] is False  # EN locked, surfaced on open
        slide = next(c for c in body["cells"] if c["role"] == "slide")
        r = client.post(
            "/api/studio/deck/edit-body",
            headers=AUTH,
            json={
                "deck_id": bilingual.en_id,
                "slide_id": slide["slide_id"],
                "role": slide["role"],
                "new_body": "# changed",
                "expected_deck_version": body["deck_version"],
                "expected_cell_hash": slide["content_hash"],
            },
        )
        assert r.status_code == 423
        assert r.json()["detail"]["reason"]
