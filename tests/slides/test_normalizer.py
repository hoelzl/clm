"""Tests for the slide normalization engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.slides.normalizer import (
    ALL_OPERATIONS,
    NormalizationResult,
    _add_tag_to_header,
    _apply_interleaving,
    _apply_tag_migration,
    _apply_workshop_tags,
    _reconstruct,
    _split_raw_cells,
    normalize_directory,
    normalize_file,
)

# ---------------------------------------------------------------------------
# Helper to build slide files
# ---------------------------------------------------------------------------


def _write_slide(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Round-trip (split → reconstruct)
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_simple_file(self):
        text = '# %% [markdown] lang="de"\n# Content\n'
        preamble, cells = _split_raw_cells(text)
        assert _reconstruct(preamble, cells) == text

    def test_multiple_cells(self):
        text = '# %% [markdown] lang="de"\n# Heading\n\n# %%\nx = 1\n'
        preamble, cells = _split_raw_cells(text)
        assert len(cells) == 2
        assert _reconstruct(preamble, cells) == text

    def test_with_preamble(self):
        text = "# File comment\n\n# %% [markdown]\n# Content\n"
        preamble, cells = _split_raw_cells(text)
        assert preamble == "# File comment\n"
        assert len(cells) == 1
        assert _reconstruct(preamble, cells) == text

    def test_j2_cells(self):
        text = (
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("Titel", "Title") }}\n'
            "\n"
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Folie\n"
        )
        preamble, cells = _split_raw_cells(text)
        assert len(cells) == 3  # j2 import, j2 header call, markdown cell
        assert _reconstruct(preamble, cells) == text


# ---------------------------------------------------------------------------
# Tag migration
# ---------------------------------------------------------------------------


class TestTagMigration:
    def test_alt_after_start_becomes_completed(self, tmp_path):
        text = '# %% tags=["start"]\nx = 1\n\n# %% tags=["alt"]\nx = 2\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["tag_migration"])

        assert len(result.changes) == 1
        assert result.changes[0].operation == "tag_migration"
        assert '"completed"' in result.changes[0].description

        # Verify file was modified
        new_text = path.read_text(encoding="utf-8")
        assert '"completed"' in new_text
        assert '"alt"' not in new_text

    def test_standalone_alt_unchanged(self, tmp_path):
        text = '# %% [markdown] tags=["alt"]\n# Some content\n\n# %% tags=["alt"]\nx = 1\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["tag_migration"])

        assert len(result.changes) == 0
        assert path.read_text(encoding="utf-8") == text

    def test_multiple_start_alt_pairs(self, tmp_path):
        text = (
            '# %% tags=["start"]\n'
            "a = 1\n"
            "\n"
            '# %% tags=["alt"]\n'
            "a = 2\n"
            "\n"
            "# %%\n"
            "print(a)\n"
            "\n"
            '# %% tags=["start"]\n'
            "b = 1\n"
            "\n"
            '# %% tags=["alt"]\n'
            "b = 2\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["tag_migration"])

        assert len(result.changes) == 2
        new_text = path.read_text(encoding="utf-8")
        assert new_text.count('"completed"') == 2
        assert '"alt"' not in new_text

    def test_alt_with_other_tags_preserved(self, tmp_path):
        text = '# %% tags=["start"]\nx = 1\n\n# %% tags=["alt", "subslide"]\nx = 2\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["tag_migration"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        assert '"completed"' in new_text
        assert '"subslide"' in new_text

    def test_no_alt_tags_no_changes(self, tmp_path):
        text = '# %% [markdown] tags=["slide"]\n# Title\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["tag_migration"])

        assert len(result.changes) == 0
        assert result.files_modified == 0


# ---------------------------------------------------------------------------
# Workshop tags
# ---------------------------------------------------------------------------


class TestWorkshopTags:
    def test_workshop_heading_gets_tag(self, tmp_path):
        text = '# %% [markdown] lang="de" tags=["subslide"]\n# ## Workshop: Begrüßung\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["workshop_tags"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        assert '"workshop"' in new_text
        assert '"subslide"' in new_text

    def test_mini_workshop_heading_gets_tag(self, tmp_path):
        text = '# %% [markdown] lang="en" tags=["subslide"]\n# ## Mini-Workshop: Greeting\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["workshop_tags"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        assert '"workshop"' in new_text

    def test_already_has_workshop_tag(self, tmp_path):
        text = '# %% [markdown] lang="de" tags=["subslide", "workshop"]\n# ## Workshop: Begrüßung\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["workshop_tags"])

        assert len(result.changes) == 0

    def test_non_workshop_heading_unchanged(self, tmp_path):
        text = '# %% [markdown] lang="de" tags=["slide"]\n# ## Methoden\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["workshop_tags"])

        assert len(result.changes) == 0

    def test_workshop_heading_no_existing_tags(self, tmp_path):
        text = '# %% [markdown] lang="de"\n# ## Workshop: Begrüßung\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["workshop_tags"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        assert 'tags=["workshop"]' in new_text


# ---------------------------------------------------------------------------
# Header manipulation helper
# ---------------------------------------------------------------------------


class TestAddTagToHeader:
    def test_add_to_existing_tags(self):
        header = '# %% [markdown] lang="de" tags=["slide"]'
        result = _add_tag_to_header(header, "workshop")
        assert result == '# %% [markdown] lang="de" tags=["slide", "workshop"]'

    def test_add_to_empty_tags(self):
        header = "# %% [markdown] tags=[]"
        result = _add_tag_to_header(header, "workshop")
        assert result == '# %% [markdown] tags=["workshop"]'

    def test_add_when_no_tags(self):
        header = '# %% [markdown] lang="de"'
        result = _add_tag_to_header(header, "workshop")
        assert result == '# %% [markdown] lang="de" tags=["workshop"]'


# ---------------------------------------------------------------------------
# Interleaving
# ---------------------------------------------------------------------------


class TestInterleaving:
    def test_block_layout_interleaved(self, tmp_path):
        """Block layout (all DE then all EN) gets interleaved."""
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Folie 1\n"
            "\n"
            '# %% [markdown] lang="de" tags=["subslide"]\n'
            "# ## Unterfolie\n"
            "\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# # Slide 1\n"
            "\n"
            '# %% [markdown] lang="en" tags=["subslide"]\n'
            "# ## Subslide\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["interleaving"])

        assert len(result.changes) == 1
        assert result.changes[0].operation == "interleaving"

        new_text = path.read_text(encoding="utf-8")
        lines = new_text.split("\n")
        # DE slide should be followed by EN slide
        de_slide_idx = next(i for i, line in enumerate(lines) if "Folie 1" in line)
        en_slide_idx = next(i for i, line in enumerate(lines) if "Slide 1" in line)
        assert en_slide_idx > de_slide_idx
        # EN slide should come before DE subslide
        de_sub_idx = next(i for i, line in enumerate(lines) if "Unterfolie" in line)
        assert en_slide_idx < de_sub_idx

    def test_already_interleaved_no_changes(self, tmp_path):
        """Already-interleaved file produces no changes."""
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Folie\n"
            "\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# # Slide\n"
            "\n"
            "# %%\n"
            "x = 1\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["interleaving"])

        assert len(result.changes) == 0
        assert path.read_text(encoding="utf-8") == text

    def test_shared_cells_preserved(self, tmp_path):
        """Shared (no-lang) cells stay in their relative position."""
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Folie\n"
            "\n"
            "# %%\n"
            "x = 1\n"
            "\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# # Slide\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["interleaving"])

        new_text = path.read_text(encoding="utf-8")
        lines = new_text.split("\n")
        de_idx = next(i for i, line in enumerate(lines) if "Folie" in line)
        en_idx = next(i for i, line in enumerate(lines) if "Slide" in line)
        code_idx = next(i for i, line in enumerate(lines) if "x = 1" in line)
        # EN should come after DE, before the shared code
        assert de_idx < en_idx < code_idx

    def test_j2_preamble_preserved(self, tmp_path):
        """j2 cells at the top stay at the top."""
        text = (
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("T", "T") }}\n'
            "\n"
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Folie\n"
            "\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# # Slide\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["interleaving"])

        new_text = path.read_text(encoding="utf-8")
        assert new_text.startswith("# j2 from")

    def test_count_mismatch_produces_review_item(self, tmp_path):
        """Unequal DE/EN counts produce a review item, not a crash."""
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Folie 1\n"
            "\n"
            '# %% [markdown] lang="de" tags=["subslide"]\n'
            "# ## Extra DE\n"
            "\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# # Slide 1\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["interleaving"])

        assert len(result.review_items) == 1
        assert result.review_items[0].issue == "count_mismatch"
        assert result.review_items[0].details["category"] == "markdown"

    def test_similarity_failure_produces_review_item(self, tmp_path):
        """Tag mismatch between paired cells produces a review item."""
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Folie\n"
            "\n"
            '# %% [markdown] lang="en" tags=["subslide"]\n'
            "# # Slide\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["interleaving"])

        assert len(result.review_items) == 1
        assert result.review_items[0].issue == "similarity_failure"
        assert "tags" in result.review_items[0].details["failed_checks"]

    def test_voiceover_pairs_interleaved(self, tmp_path):
        """Voiceover cells are paired and interleaved like content cells."""
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Folie\n"
            "\n"
            '# %% [markdown] lang="de" tags=["voiceover"]\n'
            "# DE voiceover\n"
            "\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# # Slide\n"
            "\n"
            '# %% [markdown] lang="en" tags=["voiceover"]\n'
            "# EN voiceover\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["interleaving"])

        new_text = path.read_text(encoding="utf-8")
        lines = new_text.split("\n")
        de_slide = next(i for i, line in enumerate(lines) if "Folie" in line)
        en_slide = next(i for i, line in enumerate(lines) if "# # Slide" in line)
        de_vo = next(i for i, line in enumerate(lines) if "DE voiceover" in line)
        en_vo = next(i for i, line in enumerate(lines) if "EN voiceover" in line)
        # Order: DE slide, EN slide, DE voiceover, EN voiceover
        assert de_slide < en_slide < de_vo < en_vo


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_does_not_modify_file(self, tmp_path):
        text = '# %% tags=["start"]\nx = 1\n\n# %% tags=["alt"]\nx = 2\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["tag_migration"], dry_run=True)

        assert len(result.changes) == 1
        assert result.files_modified == 0
        assert path.read_text(encoding="utf-8") == text

    def test_dry_run_reports_interleaving_changes(self, tmp_path):
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Folie\n"
            "\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# # Slide\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["interleaving"], dry_run=True)

        # Already interleaved → no changes
        assert len(result.changes) == 0


# ---------------------------------------------------------------------------
# Result status
# ---------------------------------------------------------------------------


class TestResultStatus:
    def test_clean(self):
        r = NormalizationResult()
        assert r.status == "clean"

    def test_applied(self):
        r = NormalizationResult(
            changes=[Change(file="f", operation="tag_migration", line=1, description="d")]
        )
        assert r.status == "applied"

    def test_partial(self):
        r = NormalizationResult(
            changes=[Change(file="f", operation="tag_migration", line=1, description="d")],
            review_items=[ReviewItem(file="f", issue="count_mismatch")],
        )
        assert r.status == "partial"

    def test_summary_no_changes(self):
        r = NormalizationResult()
        assert "no changes needed" in r.summary

    def test_summary_with_changes(self):
        r = NormalizationResult(
            files_modified=2,
            changes=[
                Change(file="f", operation="tag_migration", line=1, description="d"),
                Change(file="f", operation="tag_migration", line=5, description="d"),
            ],
        )
        assert "2 file" in r.summary
        assert "2 tag migration" in r.summary


# ---------------------------------------------------------------------------
# Public API: normalize_directory
# ---------------------------------------------------------------------------


class TestNormalizeDirectory:
    def test_normalizes_all_files_in_topic(self, tmp_path):
        topic = tmp_path / "topic_010_intro"
        topic.mkdir()
        _write_slide(
            topic / "slides_intro.py",
            '# %% tags=["start"]\na = 1\n\n# %% tags=["alt"]\na = 2\n',
        )
        _write_slide(
            topic / "slides_intro2.py",
            '# %% tags=["start"]\nb = 1\n\n# %% tags=["alt"]\nb = 2\n',
        )
        result = normalize_directory(topic, operations=["tag_migration"])

        assert result.files_modified == 2
        assert len(result.changes) == 2

    def test_recursive_finds_nested_files(self, tmp_path):
        slides = tmp_path / "slides"
        m1 = slides / "module_100_basics" / "topic_010_intro"
        m1.mkdir(parents=True)
        _write_slide(
            m1 / "slides_intro.py",
            '# %% tags=["start"]\na = 1\n\n# %% tags=["alt"]\na = 2\n',
        )
        result = normalize_directory(slides, operations=["tag_migration"])

        assert result.files_modified == 1
        assert len(result.changes) == 1


# ---------------------------------------------------------------------------
# All operations combined
# ---------------------------------------------------------------------------


class TestCombinedOperations:
    def test_all_operations(self, tmp_path):
        text = (
            '# %% [markdown] lang="de" tags=["subslide"]\n'
            "# ## Workshop: Begrüßung\n"
            "\n"
            '# %% tags=["start"]\n'
            "x = 1\n"
            "\n"
            '# %% tags=["alt"]\n'
            "x = 2\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path)

        ops = {c.operation for c in result.changes}
        assert "tag_migration" in ops
        assert "workshop_tags" in ops

    def test_operations_filter(self, tmp_path):
        text = (
            '# %% [markdown] lang="de" tags=["subslide"]\n'
            "# ## Workshop: Begrüßung\n"
            "\n"
            '# %% tags=["start"]\n'
            "x = 1\n"
            "\n"
            '# %% tags=["alt"]\n'
            "x = 2\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["tag_migration"])

        ops = {c.operation for c in result.changes}
        assert "tag_migration" in ops
        assert "workshop_tags" not in ops


# ---------------------------------------------------------------------------
# Slide ID auto-generation
# ---------------------------------------------------------------------------


class TestSlideIds:
    def test_markdown_heading_becomes_slug(self, tmp_path):
        """Markdown cell with heading → slugified heading text."""
        text = '# %% [markdown] lang="de" tags=["slide"]\n# # Einführung in Python\n'
        path = _write_slide(tmp_path / "slides_intro.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        assert 'slide_id="einf-hrung-in-python"' in new_text

    def test_code_with_function_def(self, tmp_path):
        """Code cell with def → function name."""
        text = '# %% lang="de"\ndef greet(name):\n    print(f"Hello, {name}")\n'
        path = _write_slide(tmp_path / "slides_funcs.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        assert 'slide_id="greet"' in new_text

    def test_code_with_class_def(self, tmp_path):
        """Code cell with class → class name."""
        text = '# %% lang="de"\nclass MyHandler:\n    pass\n'
        path = _write_slide(tmp_path / "slides_classes.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        assert 'slide_id="MyHandler"' in new_text

    def test_fallback_file_stem_cell_n(self, tmp_path):
        """Cell without heading or def → file-stem-cell-N."""
        text = '# %% [markdown] lang="de"\n# Just some text without a heading\n'
        path = _write_slide(tmp_path / "slides_misc.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        assert 'slide_id="slides_misc-cell-1"' in new_text

    def test_paired_de_en_get_same_id(self, tmp_path):
        """Paired DE/EN cells get the same slide_id (German heading as source)."""
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Methoden\n"
            "\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# # Methods\n"
        )
        path = _write_slide(tmp_path / "slides_methods.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 2
        new_text = path.read_text(encoding="utf-8")
        # Both should have the same ID derived from the DE heading
        assert new_text.count('slide_id="methoden"') == 2

    def test_existing_slide_id_unchanged(self, tmp_path):
        """Cells that already have slide_id are not modified."""
        text = '# %% [markdown] lang="de" tags=["slide"] slide_id="custom-id"\n# # Einführung\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 0
        assert path.read_text(encoding="utf-8") == text

    def test_collision_resolution(self, tmp_path):
        """Duplicate IDs get -2, -3 suffixes."""
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Einführung\n"
            "\n"
            '# %% [markdown] lang="de" tags=["subslide"]\n'
            "# # Einführung\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 2
        new_text = path.read_text(encoding="utf-8")
        assert 'slide_id="einf-hrung"' in new_text
        assert 'slide_id="einf-hrung-2"' in new_text

    def test_j2_cells_skipped(self, tmp_path):
        """j2 template cells don't get slide_ids."""
        text = (
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("Titel", "Title") }}\n'
            "\n"
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Titel\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        # Only the markdown cell should get an ID, not the j2 cells
        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        assert "# j2 " in new_text.split("\n")[0]  # j2 unchanged
        assert 'slide_id="titel"' in new_text

    def test_shared_cells_skipped(self, tmp_path):
        """Shared (no-lang) cells don't get slide_ids."""
        text = '# %%\nx = 1\n\n# %% [markdown] lang="de" tags=["slide"]\n# # Titel\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        # Only the DE markdown cell gets an ID
        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        # The shared code cell should not have slide_id
        lines = new_text.split("\n")
        assert "slide_id" not in lines[0]  # "# %%"

    def test_paired_en_uses_de_heading(self, tmp_path):
        """For DE/EN pairs, the ID comes from the German cell even if EN is different."""
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Variablen und Typen\n"
            "\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# # Variables and Types\n"
        )
        path = _write_slide(tmp_path / "slides_vars.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        new_text = path.read_text(encoding="utf-8")
        # Both cells should use the DE-derived ID
        assert new_text.count('slide_id="variablen-und-typen"') == 2

    def test_code_pairs_share_id(self, tmp_path):
        """Paired DE/EN code cells share the same ID."""
        text = (
            '# %% lang="de"\n'
            "def begruessung():\n"
            '    print("Hallo")\n'
            "\n"
            '# %% lang="en"\n'
            "def greeting():\n"
            '    print("Hello")\n'
        )
        path = _write_slide(tmp_path / "slides_funcs.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 2
        new_text = path.read_text(encoding="utf-8")
        # Both should have the DE function name
        assert new_text.count('slide_id="begruessung"') == 2

    def test_mixed_existing_and_new_ids(self, tmp_path):
        """Cells with existing IDs are preserved; new cells get generated IDs."""
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="existing"\n'
            "# # Folie 1\n"
            "\n"
            '# %% [markdown] lang="de" tags=["subslide"]\n'
            "# # Folie 2\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 1  # Only the second cell
        new_text = path.read_text(encoding="utf-8")
        assert 'slide_id="existing"' in new_text
        assert 'slide_id="folie-2"' in new_text

    def test_existing_id_on_de_shared_with_en(self, tmp_path):
        """EN cell paired with a DE cell that already has an ID inherits it."""
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="my-id"\n'
            "# # Folie\n"
            "\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# # Slide\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        assert new_text.count('slide_id="my-id"') == 2

    def test_voiceover_cells_get_ids(self, tmp_path):
        """Voiceover cells also get slide_ids."""
        text = (
            '# %% [markdown] lang="de" tags=["voiceover"]\n'
            "# DE voiceover text\n"
            "\n"
            '# %% [markdown] lang="en" tags=["voiceover"]\n'
            "# EN voiceover text\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 2

    def test_dry_run_does_not_modify(self, tmp_path):
        """Dry run reports changes but doesn't write."""
        text = '# %% [markdown] lang="de" tags=["slide"]\n# # Methoden\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"], dry_run=True)

        assert len(result.changes) == 1
        assert result.files_modified == 0
        assert path.read_text(encoding="utf-8") == text

    def test_collision_with_existing_id(self, tmp_path):
        """New cell colliding with an existing slide_id gets a suffix."""
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="methoden"\n'
            "# # Methoden\n"
            "\n"
            '# %% [markdown] lang="de" tags=["subslide"]\n'
            "# # Methoden\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        # First cell keeps "methoden", second gets "methoden-2"
        assert 'slide_id="methoden-2"' in new_text

    def test_multiple_collisions(self, tmp_path):
        """Three cells with the same heading get -2 and -3 suffixes."""
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Beispiel\n"
            "\n"
            '# %% [markdown] lang="de" tags=["subslide"]\n'
            "# # Beispiel\n"
            "\n"
            '# %% [markdown] lang="de" tags=["subslide"]\n'
            "# # Beispiel\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 3
        new_text = path.read_text(encoding="utf-8")
        assert 'slide_id="beispiel"' in new_text
        assert 'slide_id="beispiel-2"' in new_text
        assert 'slide_id="beispiel-3"' in new_text

    def test_heading_with_markdown_formatting(self, tmp_path):
        """Heading with **bold** and `code` is cleaned before slugifying."""
        text = '# %% [markdown] lang="de" tags=["slide"]\n# # **Wichtige** `Methoden`\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        new_text = path.read_text(encoding="utf-8")
        assert 'slide_id="wichtige-methoden"' in new_text


# Import needed for TestResultStatus
from clm.slides.normalizer import Change, ReviewItem  # noqa: E402
