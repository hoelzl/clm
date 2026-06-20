"""Tests for the editor de-prefix ergonomics (clean markdown editing).

The keystone is the **byte-exact round-trip**: a clean cell edited back to the
same text must reproduce identical file bytes, and a non-canonical cell must fall
back to raw editing so the write path is never weakened.
"""

from __future__ import annotations

from pathlib import Path

from clm.web.studio.prefix import deprefix, reprefix, round_trips
from clm.web.studio.service import StudioService

from .conftest import Course


class TestPrefixHelpers:
    def test_canonical_round_trips(self):
        raw = "# # Willkommen\n#\n# Schön, dass du da bist."
        assert round_trips(raw, "#")
        clean = deprefix(raw, "#")
        assert clean == "# Willkommen\n\nSchön, dass du da bist."
        assert reprefix(clean, "#") == raw

    def test_blank_line_is_bare_token(self):
        assert reprefix("a\n\nb", "#") == "# a\n#\n# b"
        assert deprefix("# a\n#\n# b", "#") == "a\n\nb"

    def test_trailing_space_line_is_non_canonical(self):
        assert round_trips("# ", "#") is False  # "# " → "" → "#" ≠ "# "

    def test_missing_prefix_is_non_canonical(self):
        assert round_trips("no prefix here", "#") is False

    def test_cpp_token(self):
        raw = "// // Titel\n//\n// Text"
        assert round_trips(raw, "//")
        assert deprefix(raw, "//") == "// Titel\n\nText"


class TestCleanCellViews:
    def test_markdown_cell_is_clean(self, service: StudioService, course: Course):
        view = service.open_deck(course.deck_id)
        slide = next(c for c in view.cells if c.role == "slide")
        assert slide.body_format == "clean"
        assert slide.body == "Willkommen\n\nSchön, dass du da bist."

    def test_code_cell_stays_raw(self, service: StudioService, course: Course):
        view = service.open_deck(course.deck_id)
        code = next(c for c in view.cells if c.cell_type == "code")
        assert code.body_format == "raw"
        assert code.body == 'print("hello")'


class TestByteExactRoundTrip:
    def test_resaving_clean_body_is_byte_exact(self, service: StudioService, course: Course):
        before = course.deck_path.read_text(encoding="utf-8")
        view = service.open_deck(course.deck_id)
        slide = next(c for c in view.cells if c.role == "slide")
        # Re-save the exact clean body the editor was given.
        service.edit_body(
            course.deck_id,
            slide.slide_id,
            slide.role,
            slide.body,
            body_format="clean",
            expected_deck_version=view.deck_version,
            expected_cell_hash=slide.content_hash,
        )
        after = course.deck_path.read_text(encoding="utf-8")
        assert after == before  # de-prefix → re-prefix lost nothing

    def test_clean_edit_reprefixes_on_disk(self, service: StudioService, course: Course):
        view = service.open_deck(course.deck_id)
        slide = next(c for c in view.cells if c.role == "slide")
        service.edit_body(
            course.deck_id,
            slide.slide_id,
            slide.role,
            "# Neuer Titel\n\nNeuer Text.",  # a real markdown heading, clean
            body_format="clean",
            expected_deck_version=view.deck_version,
            expected_cell_hash=slide.content_hash,
        )
        text = course.deck_path.read_text(encoding="utf-8")
        assert "# # Neuer Titel" in text  # md heading stored as comment + heading
        assert "# Neuer Text." in text
        # And it reads back clean.
        reslide = next(c for c in service.open_deck(course.deck_id).cells if c.role == "slide")
        assert reslide.body == "# Neuer Titel\n\nNeuer Text."

    def test_other_cells_byte_exact_after_clean_edit(self, service: StudioService, course: Course):
        view = service.open_deck(course.deck_id)
        slide = next(c for c in view.cells if c.role == "slide")
        notes_before = course.deck_path.read_text(encoding="utf-8").split("# %%")[2]
        service.edit_body(
            course.deck_id,
            slide.slide_id,
            slide.role,
            "Ganz anderer Text.",
            body_format="clean",
            expected_deck_version=view.deck_version,
            expected_cell_hash=slide.content_hash,
        )
        notes_after = course.deck_path.read_text(encoding="utf-8").split("# %%")[2]
        assert notes_after == notes_before  # the notes + code cells are untouched


class TestNonCanonicalFallback:
    def _write_deck(self, course: Course, rel: str, text: str) -> str:
        path = course.slides_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return rel

    def test_non_canonical_cell_served_raw(self, service: StudioService, course: Course):
        # "#Tight" has no space after the token → does not round-trip → raw.
        rel = self._write_deck(
            course,
            "module_100_basics/topic_010_intro/odd.de.py",
            '# %% [markdown] lang="de" tags=["slide"] slide_id="odd"\n#Tight\n',
        )
        cell = next(c for c in service.open_deck(rel).cells if c.role == "slide")
        assert cell.body_format == "raw"
        assert cell.body == "#Tight"


class TestCleanInsert:
    def test_insert_clean_markdown_is_prefixed_on_disk(
        self, service: StudioService, course: Course
    ):
        version = service.open_deck(course.deck_id).deck_version
        service.insert_cell(
            course.deck_id,
            role="slide",
            cell_type="markdown",
            body="# Frische Folie\n\nMit Text.",
            body_format="clean",
            expected_deck_version=version,
        )
        text = course.deck_path.read_text(encoding="utf-8")
        assert "# # Frische Folie" in text
        assert "# Mit Text." in text
