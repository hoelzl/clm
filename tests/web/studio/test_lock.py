"""Tests for the P3 bilingual lock (design §3.5).

A language is editable iff the *other* split half is clean relative to the sync
watermark. The lock-logic tests stub ``build_sync_plan`` with a controlled plan
so the direction→dirty→editable mapping and the 423 enforcement are exercised
hermetically (no git, no LLM); one test runs the *real* plan build to confirm a
twin with no baseline is treated as unlocked (cold-start).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clm.slides.sync_plan import Proposal, SyncPlan
from clm.web.app import create_app
from clm.web.studio.service import LanguageLockedError, StudioService

from .conftest import Bilingual, Course

TOKEN = "test-studio-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _fake_plan(*proposals: Proposal, baseline: str = "git-head"):
    """A factory that ignores the real paths and returns a fixed plan."""

    def _build(de_path, en_path, **kwargs):  # noqa: ANN001 - test stub
        return SyncPlan(
            de_path=de_path,
            en_path=en_path,
            baseline_source=baseline,
            proposals=list(proposals),
        )

    return _build


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


class TestColdStartUnlocked:
    def test_twin_with_no_baseline_is_editable(
        self, bilingual_service: StudioService, bilingual: Bilingual
    ):
        # Real build_sync_plan: no watermark + no git → baseline "none" → unlocked.
        view = bilingual_service.open_deck(bilingual.de_id)
        assert view.lock.is_pair is True
        assert view.lock.lang == "de"
        assert view.lock.other_lang == "en"
        assert view.lock.twin_deck_id == bilingual.en_id
        assert view.lock.editable is True
        assert view.lock.baseline == "none"


class TestWatermarkDerivedLock:
    def test_de_dirty_locks_en(
        self, bilingual_service: StudioService, bilingual: Bilingual, monkeypatch
    ):
        # DE drifted (direction de->en) → DE is the source; EN is locked + DE-stale.
        monkeypatch.setattr(
            "clm.slides.sync_plan.build_sync_plan",
            _fake_plan(
                Proposal(kind="edit", role="slide", direction="de->en", slide_id="intro-welcome")
            ),
        )
        de = bilingual_service.open_deck(bilingual.de_id)
        en = bilingual_service.open_deck(bilingual.en_id)
        assert de.lock.editable is True and de.lock.other_stale is True
        assert en.lock.editable is False
        assert en.lock.locked_reason and "DE" in en.lock.locked_reason

    def test_conflict_locks_both(
        self, bilingual_service: StudioService, bilingual: Bilingual, monkeypatch
    ):
        monkeypatch.setattr(
            "clm.slides.sync_plan.build_sync_plan",
            _fake_plan(
                Proposal(kind="conflict", role="slide", direction=None, slide_id="intro-welcome")
            ),
        )
        de = bilingual_service.open_deck(bilingual.de_id)
        en = bilingual_service.open_deck(bilingual.en_id)
        assert de.lock.editable is False and de.lock.has_conflicts is True
        assert en.lock.editable is False and en.lock.has_conflicts is True

    def test_clean_pair_both_editable(
        self, bilingual_service: StudioService, bilingual: Bilingual, monkeypatch
    ):
        monkeypatch.setattr("clm.slides.sync_plan.build_sync_plan", _fake_plan())
        de = bilingual_service.open_deck(bilingual.de_id)
        en = bilingual_service.open_deck(bilingual.en_id)
        assert de.lock.editable is True and en.lock.editable is True
        assert de.lock.other_stale is False and en.lock.other_stale is False


class TestLockEnforcement:
    def test_edit_on_locked_language_raises(
        self, bilingual_service: StudioService, bilingual: Bilingual, monkeypatch
    ):
        monkeypatch.setattr(
            "clm.slides.sync_plan.build_sync_plan",
            _fake_plan(
                Proposal(kind="edit", role="slide", direction="de->en", slide_id="intro-welcome")
            ),
        )
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
        self, bilingual_service: StudioService, bilingual: Bilingual, monkeypatch
    ):
        monkeypatch.setattr(
            "clm.slides.sync_plan.build_sync_plan",
            _fake_plan(
                Proposal(kind="edit", role="slide", direction="de->en", slide_id="intro-welcome")
            ),
        )
        en = bilingual_service.open_deck(bilingual.en_id)
        with pytest.raises(LanguageLockedError):
            bilingual_service.insert_cell(
                bilingual.en_id,
                role="slide",
                body="# x",
                expected_deck_version=en.deck_version,
            )

    def test_edit_on_source_language_succeeds(
        self, bilingual_service: StudioService, bilingual: Bilingual, monkeypatch
    ):
        monkeypatch.setattr(
            "clm.slides.sync_plan.build_sync_plan",
            _fake_plan(
                Proposal(kind="edit", role="slide", direction="de->en", slide_id="intro-welcome")
            ),
        )
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
    def test_locked_write_is_423_with_reason(self, bilingual: Bilingual, monkeypatch):
        monkeypatch.setattr(
            "clm.slides.sync_plan.build_sync_plan",
            _fake_plan(
                Proposal(kind="edit", role="slide", direction="de->en", slide_id="intro-welcome")
            ),
        )
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
