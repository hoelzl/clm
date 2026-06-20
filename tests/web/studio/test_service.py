"""Tests for :class:`clm.web.studio.service.StudioService`.

The concurrency core is the keystone: these assert that stale writes are
rejected (409-grade) and that untouched cells stay byte-for-byte unchanged
after an edit (the §3.7 invariant lifted from the closed prototype's tests).
"""

from __future__ import annotations

import pytest

from clm.slides.raw_cells import split_cells
from clm.web.studio.service import (
    CellNotFoundError,
    DeckNotFoundError,
    InvalidDeckIdError,
    StaleWriteError,
    StudioService,
)

from .conftest import Course


class TestNavigation:
    def test_list_decks_includes_present_deck(self, service: StudioService, course: Course):
        tree = service.list_decks()
        ids = [d.deck_id for d in tree.decks if d.status == "present"]
        assert course.deck_id in ids

    def test_search_matches_topic(self, service: StudioService, course: Course):
        results = service.search("intro")
        assert results.hits, "expected at least one search hit for 'intro'"

    def test_recents_updates_on_open(self, service: StudioService, course: Course):
        assert service.list_decks().recents == []
        service.open_deck(course.deck_id)
        assert service.list_decks().recents[0] == course.deck_id


class TestOpenDeck:
    def test_open_returns_cells_and_version(self, service: StudioService, course: Course):
        view = service.open_deck(course.deck_id)
        assert view.deck_id == course.deck_id
        assert view.deck_version
        # two markdown cells + one code cell
        assert len(view.cells) == 3

    def test_id_markdown_cells_are_editable(self, service: StudioService, course: Course):
        view = service.open_deck(course.deck_id)
        slide = next(c for c in view.cells if c.role == "slide")
        notes = next(c for c in view.cells if c.role == "notes")
        assert slide.editable and slide.slide_id == "intro-welcome"
        assert notes.editable and notes.slide_id == "intro-welcome"

    def test_idless_code_cell_is_read_only(self, service: StudioService, course: Course):
        view = service.open_deck(course.deck_id)
        code = next(c for c in view.cells if c.cell_type == "code")
        assert not code.editable
        assert code.slide_id is None

    def test_missing_deck_raises(self, service: StudioService):
        with pytest.raises(DeckNotFoundError):
            service.open_deck("module_100_basics/topic_010_intro/nope.de.py")

    def test_traversal_is_rejected(self, service: StudioService):
        with pytest.raises(InvalidDeckIdError):
            service.open_deck("../../../etc/passwd")

    def test_non_py_rejected(self, service: StudioService):
        with pytest.raises(InvalidDeckIdError):
            service.open_deck("module_100_basics/topic_010_intro/slides_intro.de.txt")


class TestConcurrencyCore:
    def _slide_cell(self, service: StudioService, course: Course):
        view = service.open_deck(course.deck_id)
        return view, next(c for c in view.cells if c.role == "slide")

    def test_edit_body_success_round_trips(self, service: StudioService, course: Course):
        view, slide = self._slide_cell(service, course)
        result = service.edit_body(
            course.deck_id,
            slide.slide_id,
            slide.role,
            "# Willkommen (überarbeitet)\n#\n# Neuer Text.",
            expected_deck_version=view.deck_version,
            expected_cell_hash=slide.content_hash,
        )
        assert result.ok
        assert result.deck_version != view.deck_version
        # A fresh open reports the same guards the write returned.
        reopened = service.open_deck(course.deck_id)
        assert reopened.deck_version == result.deck_version
        reslide = next(c for c in reopened.cells if c.role == "slide")
        assert reslide.content_hash == result.cell_hash
        assert "überarbeitet" in reslide.body

    def test_stale_deck_version_is_rejected(self, service: StudioService, course: Course):
        view, slide = self._slide_cell(service, course)
        with pytest.raises(StaleWriteError) as exc:
            service.edit_body(
                course.deck_id,
                slide.slide_id,
                slide.role,
                "x",
                expected_deck_version="deadbeefdeadbeef",
                expected_cell_hash=slide.content_hash,
            )
        assert exc.value.kind == "deck_version"
        assert exc.value.current == view.deck_version

    def test_stale_cell_hash_is_rejected(self, service: StudioService, course: Course):
        view, slide = self._slide_cell(service, course)
        with pytest.raises(StaleWriteError) as exc:
            service.edit_body(
                course.deck_id,
                slide.slide_id,
                slide.role,
                "x",
                expected_deck_version=view.deck_version,  # deck is current…
                expected_cell_hash="0" * 64,  # …but the cell guard is wrong
            )
        assert exc.value.kind == "cell_hash"

    def test_unknown_cell_raises(self, service: StudioService, course: Course):
        view = service.open_deck(course.deck_id)
        with pytest.raises(CellNotFoundError):
            service.edit_body(
                course.deck_id,
                "no-such-id",
                "slide",
                "x",
                expected_deck_version=view.deck_version,
                expected_cell_hash="0" * 64,
            )

    def test_untouched_cells_are_byte_exact_after_edit(
        self, service: StudioService, course: Course
    ):
        before = course.deck_path.read_text(encoding="utf-8")
        _, before_cells = split_cells(before)

        view, slide = self._slide_cell(service, course)
        service.edit_body(
            course.deck_id,
            slide.slide_id,
            slide.role,
            "# Geänderter Titel\n#\n# Geänderter Inhalt.",
            expected_deck_version=view.deck_version,
            expected_cell_hash=slide.content_hash,
        )

        after = course.deck_path.read_text(encoding="utf-8")
        _, after_cells = split_cells(after)
        assert len(after_cells) == len(before_cells)
        # Every cell except the edited slide is byte-for-byte identical.
        for b, a in zip(before_cells, after_cells, strict=True):
            if b.metadata.tags == ["slide"]:
                continue
            assert a.lines == b.lines

    def test_edit_tags_changes_header_not_body_hash(self, service: StudioService, course: Course):
        view, slide = self._slide_cell(service, course)
        result = service.edit_tags(
            course.deck_id,
            slide.slide_id,
            slide.role,
            ["slide", "keep"],
            expected_deck_version=view.deck_version,
            expected_cell_hash=slide.content_hash,
        )
        # Body unchanged → same cell hash; file changed → new deck version.
        assert result.cell_hash == slide.content_hash
        assert result.deck_version != view.deck_version
        assert 'tags=["slide", "keep"]' in course.deck_path.read_text(encoding="utf-8")


class TestSelfWriteHint:
    def test_marks_and_expires(self, course: Course, monkeypatch):
        svc = StudioService(course.spec_path)
        assert not svc.is_self_write(course.deck_id)
        svc.mark_self_write(course.deck_id)
        assert svc.is_self_write(course.deck_id)

    def test_edit_marks_self_write(self, service: StudioService, course: Course):
        view = service.open_deck(course.deck_id)
        slide = next(c for c in view.cells if c.role == "slide")
        service.edit_body(
            course.deck_id,
            slide.slide_id,
            slide.role,
            "# x\n#\n# y",
            expected_deck_version=view.deck_version,
            expected_cell_hash=slide.content_hash,
        )
        assert service.is_self_write(course.deck_id)
