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
    _remove_tag_from_header,
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
# Placeholder start demotion (#233 item 4a)
# ---------------------------------------------------------------------------


class TestPlaceholderStart:
    def test_markdown_completed_after_placeholder_start_fixed(self, tmp_path):
        text = (
            '# %% tags=["start"]\n'
            "# Your solution here\n"
            "\n"
            '# %% [markdown] lang="de" tags=["completed"]\n'
            "# Die Loesung verwendet np.array.\n"
            "\n"
            '# %% tags=["alt"]\n'
            "ages = np.array([25, 32, 18, 45, 28])\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["placeholder_start"])

        assert len(result.changes) == 2
        assert all(c.operation == "placeholder_start" for c in result.changes)
        new_text = path.read_text(encoding="utf-8")
        assert '"start"' not in new_text
        assert new_text.startswith("# %%\n# Your solution here\n")
        assert '# %% [markdown] lang="de" tags=["alt"]' in new_text
        assert '"completed"' not in new_text

    def test_markdown_alt_after_placeholder_start_demotes_start_only(self, tmp_path):
        # The pre-migration authored shape: the markdown solution run is still
        # tagged "alt". The start tag is dropped so tag_migration can no
        # longer promote the alt cell.
        text = (
            '# %% tags=["start"]\n'
            "# Your solution here\n"
            "\n"
            '# %% [markdown] lang="de" tags=["alt"]\n'
            "# Die Loesung verwendet np.array.\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["placeholder_start"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        assert '"start"' not in new_text
        assert 'tags=["alt"]' in new_text

    def test_full_run_does_not_promote_alt_after_demoted_start(self, tmp_path):
        # With all operations enabled, placeholder_start must win over
        # tag_migration: the markdown alt stays alt.
        text = (
            '# %% tags=["start"]\n'
            "# Your solution here\n"
            "\n"
            '# %% [markdown] lang="de" tags=["alt"]\n'
            "#\n"
            "# Die Loesung verwendet np.array.\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        normalize_file(path)

        new_text = path.read_text(encoding="utf-8")
        assert '"start"' not in new_text
        assert 'tags=["alt"]' in new_text
        assert '"completed"' not in new_text

    def test_code_completed_pair_untouched(self, tmp_path):
        # A placeholder start paired with a *code* completed cell is a valid
        # live-coding pair — never touched.
        text = (
            '# %% tags=["start"]\n'
            "# Your solution here\n"
            "\n"
            '# %% tags=["completed"]\n'
            "ages = np.array([25, 32, 18, 45, 28])\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["placeholder_start"])

        assert len(result.changes) == 0
        assert path.read_text(encoding="utf-8") == text

    def test_real_scaffolding_start_untouched(self, tmp_path):
        text = (
            '# %% tags=["start"]\n'
            "def evaluate(student):\n"
            "    ...\n"
            "\n"
            '# %% [markdown] tags=["completed"]\n'
            "# Discussion.\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["placeholder_start"])

        assert len(result.changes) == 0
        assert path.read_text(encoding="utf-8") == text

    def test_hint_comment_is_not_a_placeholder(self, tmp_path):
        # A placeholder phrase followed by real text is a genuine hint.
        text = (
            '# %% tags=["start"]\n'
            "# Your code here: Train linear model\n"
            "\n"
            '# %% [markdown] tags=["completed"]\n'
            "# Discussion.\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["placeholder_start"])

        assert len(result.changes) == 0
        assert path.read_text(encoding="utf-8") == text

    @pytest.mark.parametrize(
        "body",
        [
            "pass\n",
            "...\n",
            "# Ihre Lösung hier\n",
            "# Deine Loesung hier!\n",
            "# Ihr Code hier:\n",
            "# YOUR SOLUTION HERE\n",
            "# Your solution here\npass\n",
        ],
    )
    def test_placeholder_body_variants(self, tmp_path, body):
        text = (
            '# %% tags=["start"]\n'
            + body
            + "\n"
            + '# %% [markdown] tags=["completed"]\n'
            + "# Discussion.\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["placeholder_start"])

        assert len(result.changes) == 2
        new_text = path.read_text(encoding="utf-8")
        assert '"start"' not in new_text
        assert 'tags=["alt"]' in new_text

    def test_other_tags_on_start_cell_preserved(self, tmp_path):
        text = (
            '# %% tags=["start", "subslide"]\n'
            "# Your solution here\n"
            "\n"
            '# %% [markdown] tags=["completed"]\n'
            "# Discussion.\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["placeholder_start"])

        assert len(result.changes) == 2
        new_text = path.read_text(encoding="utf-8")
        assert 'tags=["subslide"]' in new_text
        assert '"start"' not in new_text

    def test_untagged_markdown_follower_untouched(self, tmp_path):
        text = '# %% tags=["start"]\n# Your solution here\n\n# %% [markdown]\n# Plain markdown.\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["placeholder_start"])

        assert len(result.changes) == 0
        assert path.read_text(encoding="utf-8") == text

    def test_idempotent(self, tmp_path):
        text = (
            '# %% tags=["start"]\n'
            "# Your solution here\n"
            "\n"
            '# %% [markdown] tags=["completed"]\n'
            "# Discussion.\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        normalize_file(path, operations=["placeholder_start"])
        first = path.read_text(encoding="utf-8")
        result = normalize_file(path, operations=["placeholder_start"])

        assert len(result.changes) == 0
        assert path.read_text(encoding="utf-8") == first


class TestRemoveTagFromHeader:
    def test_sole_tag_drops_attribute(self):
        assert _remove_tag_from_header('# %% tags=["start"]', "start") == "# %%"

    def test_other_tags_kept(self):
        assert (
            _remove_tag_from_header('# %% tags=["start", "subslide"]', "start")
            == '# %% tags=["subslide"]'
        )

    def test_trailing_attributes_kept(self):
        assert (
            _remove_tag_from_header('# %% tags=["start"] slide_id="abc"', "start")
            == '# %% slide_id="abc"'
        )

    def test_no_tags_attribute_is_noop(self):
        assert _remove_tag_from_header("# %%", "start") == "# %%"


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


# ---------------------------------------------------------------------------
# Workshop tag symmetry (DE/EN propagation)
# ---------------------------------------------------------------------------


class TestWorkshopSymmetry:
    """``workshop``/``end-workshop`` are slide-scoped — symmetrize across pairs.

    Headings are deliberately worded so the heading-text pass
    (``_apply_workshop_tags``) does *not* fire; only the pair-symmetry pass
    can add the missing tag.
    """

    def test_propagates_de_to_en(self, tmp_path):
        text = (
            '# %% [markdown] lang="de" tags=["subslide", "workshop"]\n'
            "# ## Aufgabe\n"
            "\n"
            '# %% [markdown] lang="en" tags=["subslide"]\n'
            "# ## Exercise\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["workshop_tags"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        # Both headings now carry the tag.
        assert new_text.count('"workshop"') == 2

    def test_propagates_en_to_de(self, tmp_path):
        text = (
            '# %% [markdown] lang="de" tags=["subslide"]\n'
            "# ## Aufgabe\n"
            "\n"
            '# %% [markdown] lang="en" tags=["subslide", "workshop"]\n'
            "# ## Exercise\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["workshop_tags"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        assert new_text.count('"workshop"') == 2

    def test_end_workshop_propagates(self, tmp_path):
        text = (
            '# %% [markdown] lang="de" tags=["slide", "end-workshop"]\n'
            "# ## Ende\n"
            "\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# ## End\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["workshop_tags"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        assert new_text.count('"end-workshop"') == 2

    def test_symmetric_pair_unchanged(self, tmp_path):
        text = (
            '# %% [markdown] lang="de" tags=["subslide", "workshop"]\n'
            "# ## Aufgabe\n"
            "\n"
            '# %% [markdown] lang="en" tags=["subslide", "workshop"]\n'
            "# ## Exercise\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["workshop_tags"])

        assert len(result.changes) == 0

    def test_solo_heading_not_propagated(self, tmp_path):
        # A lone DE workshop heading (no EN partner) must not gain a phantom pair.
        text = '# %% [markdown] lang="de" tags=["subslide", "workshop"]\n# ## Aufgabe\n'
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

    def test_similarity_failure_worklist_carries_full_bodies_and_score(self, tmp_path):
        # #236: the similarity_failure worklist gives an agent the FULL DE/EN cell bodies
        # plus a 0..1 similarity score + the category, not just the truncated previews —
        # enough to judge whether a flagged positional pair is a correct (localized) twin.
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Folie\n"
            "# Ein zweiter Satz.\n"
            "\n"
            '# %% [markdown] lang="en" tags=["subslide"]\n'
            "# # Slide\n"
            "# A second sentence.\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["interleaving"])

        assert len(result.review_items) == 1
        d = result.review_items[0].details
        assert d["category"] == "markdown"
        # Only the tag check fails (slide vs subslide); the other 3 markdown checks pass.
        assert d["failed_checks"] == ["tags"]
        assert d["similarity_score"] == 0.75  # 1 - 1/4 applicable checks
        # Full multi-line bodies, NOT the 60-char preview.
        assert d["de_cell"]["body"] == "# # Folie\n# Ein zweiter Satz."
        assert d["en_cell"]["body"] == "# # Slide\n# A second sentence."
        assert d["de_cell"]["preview"] == "# # Folie"  # preview still present (back-compat)

    def test_adjacent_localized_code_pair_converges(self, tmp_path):
        # #236 convergence: an ALREADY-adjacent DE/EN code pair that diverges only by
        # localized identifiers (code_structure) needs no reorder and is not a structural
        # error — so it is NOT flagged (and a course-gate run on it reads clean).
        text = (
            '# %% lang="de"\n'
            "def begruessung():\n"
            '    return "Hallo"\n'
            "\n"
            '# %% lang="en"\n'
            "def greeting():\n"
            '    return "Hello"\n'
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["interleaving"])

        assert result.review_items == []  # localization-only + adjacent → converged
        assert result.changes == []  # nothing to reorder
        assert path.read_text(encoding="utf-8") == text  # untouched

    def test_adjacent_structural_mismatch_still_flags(self, tmp_path):
        # The convergence is keyed on failure MODE: an already-adjacent pair that fails a
        # STRUCTURAL check (here heading_level) is a likely authoring error → still flagged,
        # so `clm course gate` keeps catching it.
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Folie\n"
            "\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# ## Slide\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["interleaving"])

        assert len(result.review_items) == 1
        assert result.review_items[0].issue == "similarity_failure"
        assert "heading_level" in result.review_items[0].details["failed_checks"]

    def test_confirmed_pairing_reorders_diverged_code_then_converges(self, tmp_path):
        # #236 accept: a BLOCK-layout deck whose DE/EN code diverged (localized names) is
        # refused by default; the agent confirms the positional pairings from the worklist,
        # which bypasses the gate and reorders them adjacent — and a re-run is then clean.
        text = (
            '# %% lang="de"\ndef begruessung():\n    return "Hallo"\n\n'
            '# %% lang="de"\ndef abschied():\n    return "Tschuess"\n\n'
            '# %% lang="en"\ndef greeting():\n    return "Hello"\n\n'
            '# %% lang="en"\ndef farewell():\n    return "Goodbye"\n'
        )
        path = _write_slide(tmp_path / "slides_test.py", text)

        # 1. Worklist (the task): two refused similarity_failures, not yet adjacent.
        wl = normalize_file(path, operations=["interleaving"], dry_run=True)
        fails = [r for r in wl.review_items if r.issue == "similarity_failure"]
        assert len(fails) == 2
        confirmed = {(r.details["de_cell"]["line"], r.details["en_cell"]["line"]) for r in fails}

        # 2. Accept: confirm the pairings → bypass the gate, reorder into adjacency.
        applied = normalize_file(path, operations=["interleaving"], confirmed_pairings=confirmed)
        assert applied.review_items == []  # all bypassed
        assert any(c.operation == "interleaving" for c in applied.changes)
        out = path.read_text(encoding="utf-8")
        assert (
            out.index("begruessung")
            < out.index("greeting")
            < out.index("abschied")
            < out.index("farewell")
        )

        # 3. Verify: a plain re-run is now clean (adjacent + localization-only → converged).
        verify = normalize_file(path, operations=["interleaving"])
        assert verify.review_items == [] and verify.changes == []

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
# canonicalize_start_completed flag
# ---------------------------------------------------------------------------


# Cohesion layout [DE_start, DE_completed, EN_start, EN_completed] where the
# DE/EN code differs only by localized identifiers (begruessung vs greeting).
# This is the layout `clm slides normalize` leaves untouched by default
# (the similarity gate rejects the pair) but that breaks the byte-identical
# split→unify round-trip.
_COHESION_DIFF_NAMES = (
    '# %% lang="de" tags=["start"]\n'
    "def begruessung(name, alter):\n"
    '    return f"Hallo {name}"\n'
    "\n"
    '# %% lang="de" tags=["completed"]\n'
    "def begruessung(name: str, alter: int) -> str:\n"
    '    return f"Hallo {name}"\n'
    "\n"
    '# %% lang="en" tags=["start"]\n'
    "def greeting(name, age):\n"
    '    return f"Hello {name}"\n'
    "\n"
    '# %% lang="en" tags=["completed"]\n'
    "def greeting(name: str, age: int) -> str:\n"
    '    return f"Hello {name}"\n'
)


class TestCanonicalizeStartCompleted:
    def test_default_leaves_cohesion_layout_with_review(self, tmp_path):
        """Without the flag, a differing-code start/completed pair is left
        in cohesion layout and reported as a similarity failure."""
        path = _write_slide(tmp_path / "slides_test.py", _COHESION_DIFF_NAMES)
        result = normalize_file(path, operations=["interleaving"])

        assert not any(c.operation == "interleaving" for c in result.changes)
        assert any(
            r.issue == "similarity_failure" and "code_structure" in r.details["failed_checks"]
            for r in result.review_items
        )
        # Source order is unchanged (still cohesion layout).
        assert path.read_text(encoding="utf-8") == _COHESION_DIFF_NAMES

    def test_flag_forces_canonical_interleave(self, tmp_path):
        """With the flag, the pair is forced into the canonical interleave
        [DE_start, EN_start, DE_completed, EN_completed] with no review."""
        path = _write_slide(tmp_path / "slides_test.py", _COHESION_DIFF_NAMES)
        result = normalize_file(
            path,
            operations=["interleaving"],
            canonicalize_start_completed=True,
        )

        assert any(c.operation == "interleaving" for c in result.changes)
        assert result.review_items == []

        lines = path.read_text(encoding="utf-8").split("\n")
        de_start = next(i for i, ln in enumerate(lines) if "def begruessung(name, alter)" in ln)
        en_start = next(i for i, ln in enumerate(lines) if "def greeting(name, age)" in ln)
        de_done = next(i for i, ln in enumerate(lines) if "def begruessung(name: str" in ln)
        en_done = next(i for i, ln in enumerate(lines) if "def greeting(name: str" in ln)
        assert de_start < en_start < de_done < en_done

    def test_flag_does_not_affect_non_start_completed_failures(self, tmp_path):
        """The flag is scoped to start/completed pairs; a genuine markdown
        similarity failure still produces a review item."""
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Folie\n"
            "\n"
            '# %% [markdown] lang="en" tags=["subslide"]\n'
            "# # Slide\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(
            path,
            operations=["interleaving"],
            canonicalize_start_completed=True,
        )

        assert any(r.issue == "similarity_failure" for r in result.review_items)

    def test_flag_is_idempotent_on_identical_code(self, tmp_path):
        """A start/completed pair with identical DE/EN code already
        interleaves; the flag does not disturb the result."""
        text = (
            '# %% lang="de" tags=["start"]\n'
            "names = []\n"
            "\n"
            '# %% lang="de" tags=["completed"]\n'
            "names: list[str] = []\n"
            "\n"
            '# %% lang="en" tags=["start"]\n'
            "names = []\n"
            "\n"
            '# %% lang="en" tags=["completed"]\n'
            "names: list[str] = []\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(
            path,
            operations=["interleaving"],
            canonicalize_start_completed=True,
        )
        lines = path.read_text(encoding="utf-8").split("\n")
        # completed cells carry the annotation; start cells do not
        starts = [i for i, ln in enumerate(lines) if ln == "names = []"]
        dones = [i for i, ln in enumerate(lines) if ln == "names: list[str] = []"]
        # canonical interleave: DE_start, EN_start, DE_completed, EN_completed
        assert starts[0] < starts[1] < dones[0] < dones[1]
        assert result.review_items == []


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
    """Covers the slide_ids operation, which delegates to the shared
    ``clm.slides.assign_ids`` engine: EN-derived kebab slugs, German
    transliteration, narrative inheritance, ``!``-preserve marker. Only
    slide-start cells (markdown with ``slide``/``subslide`` tags) get
    fresh ids; everything else is skipped or inherits.
    """

    def test_markdown_heading_becomes_slug(self, tmp_path):
        """Slide-start markdown cell with heading → transliterated slug."""
        text = '# %% [markdown] lang="de" tags=["slide"]\n# # Einführung in Python\n'
        path = _write_slide(tmp_path / "slides_intro.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        # 'ü' → 'ue' via the assign-ids transliterator.
        assert 'slide_id="einfuehrung-in-python"' in new_text

    def test_non_slide_code_cell_skipped(self, tmp_path):
        """Code cells without a slide/subslide tag don't get an id."""
        text = '# %% lang="de"\ndef greet(name):\n    print(f"Hello, {name}")\n'
        path = _write_slide(tmp_path / "slides_funcs.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 0
        assert path.read_text(encoding="utf-8") == text

    def test_headingless_slide_soft_refused(self, tmp_path):
        """Slide-start cell without a heading → soft refusal (review item)."""
        text = '# %% [markdown] lang="de" tags=["slide"]\n# Just some text without a heading\n'
        path = _write_slide(tmp_path / "slides_misc.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 0
        assert len(result.review_items) == 1
        item = result.review_items[0]
        assert item.issue == "slide_id_soft_refusal"
        # File is untouched when nothing else writes.
        assert path.read_text(encoding="utf-8") == text

    def test_paired_de_en_use_en_heading(self, tmp_path):
        """Paired DE/EN slide cells share the EN-derived slug."""
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
        assert new_text.count('slide_id="methods"') == 2
        # The DE-side slug must not leak in.
        assert "methoden" not in new_text

    def test_existing_slide_id_unchanged(self, tmp_path):
        """Existing ids are preserved without --force."""
        text = '# %% [markdown] lang="de" tags=["slide"] slide_id="custom-id"\n# # Einführung\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 0
        assert path.read_text(encoding="utf-8") == text

    def test_preserve_marker_never_regenerated(self, tmp_path):
        """``!``-prefixed ids are kept even when --force would rewrite."""
        from clm.slides.assign_ids import AssignOptions

        text = '# %% [markdown] lang="de" tags=["slide"] slide_id="!keep-me"\n# # Methoden\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(
            path,
            operations=["slide_ids"],
            assign_options=AssignOptions(force=True),
        )

        assert len(result.changes) == 0
        assert "!keep-me" in path.read_text(encoding="utf-8")

    def test_collision_resolution(self, tmp_path):
        """Duplicate slugs get -2 suffix."""
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
        assert 'slide_id="einfuehrung"' in new_text
        assert 'slide_id="einfuehrung-2"' in new_text

    def test_j2_title_macro_only_slide_assigned(self, tmp_path):
        """j2 import + j2 ``header()`` macro are untouched; the following
        slide gets its own id (the title anchor itself never writes)."""
        text = (
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("Titel", "Title") }}\n'
            "\n"
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Titel\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        assert new_text.startswith("# j2 ")
        assert 'slide_id="titel"' in new_text

    def test_shared_cells_skipped(self, tmp_path):
        """Shared (no-lang) cells without a slide tag don't get ids."""
        text = '# %%\nx = 1\n\n# %% [markdown] lang="de" tags=["slide"]\n# # Titel\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 1
        new_text = path.read_text(encoding="utf-8")
        lines = new_text.split("\n")
        assert "slide_id" not in lines[0]  # "# %%" shared cell

    def test_mixed_existing_and_new_ids(self, tmp_path):
        """Existing ids are preserved; new slide cells get fresh slugs."""
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
        """EN cell paired with a pre-assigned DE cell reuses the id."""
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

    def test_voiceover_inherits_from_preceding_slide(self, tmp_path):
        """Voiceover cells inherit the most recent slide's id."""
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# # Folie\n"
            "\n"
            '# %% [markdown] lang="de" tags=["voiceover"]\n'
            "# DE voiceover text\n"
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        # One slide-start assignment + one narrative inheritance.
        assert len(result.changes) == 2
        new_text = path.read_text(encoding="utf-8")
        assert new_text.count('slide_id="folie"') == 2

    def test_voiceover_without_preceding_slide_skipped(self, tmp_path):
        """Voiceover with nothing before it has no anchor to inherit from."""
        text = '# %% [markdown] lang="de" tags=["voiceover"]\n# DE voiceover text\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"])

        assert len(result.changes) == 0
        assert "slide_id" not in path.read_text(encoding="utf-8")

    def test_dry_run_does_not_modify(self, tmp_path):
        """Dry run reports changes but doesn't write."""
        text = '# %% [markdown] lang="de" tags=["slide"]\n# # Methoden\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        result = normalize_file(path, operations=["slide_ids"], dry_run=True)

        assert len(result.changes) == 1
        assert result.files_modified == 0
        assert path.read_text(encoding="utf-8") == text

    def test_collision_with_existing_id(self, tmp_path):
        """New slug colliding with an existing slide_id gets a suffix."""
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
        assert 'slide_id="methoden-2"' in new_text

    def test_multiple_collisions(self, tmp_path):
        """Three slides with the same heading get -2 and -3 suffixes."""
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


# ---------------------------------------------------------------------------
# Cell spacing (blank line between cells; markdown leading blank comment)
# ---------------------------------------------------------------------------


_DECK_WITH_PREAMBLE = (
    "# j2 from 'macros.j2' import header\n"
    '# {{ header("Regeln für Typen", "Rules for Types") }}\n'
    "from typing import Iterable\n"
    "\n"
    "\n"
    '# %% [markdown] lang="de" tags=["slide"] slide_id="gh"\n'
    "#\n# ## Hinweise\n"
)


class TestPreambleCode:
    def test_wraps_preamble_code(self, tmp_path):
        path = _write_slide(tmp_path / "slides_x.py", _DECK_WITH_PREAMBLE)
        result = normalize_file(path, operations=["preamble_code", "cell_spacing"])
        assert result.files_modified == 1
        out = path.read_text(encoding="utf-8")
        # The import is now its own bare code cell, no longer on the header cell.
        assert "}}\nfrom typing" not in out
        assert "# %%\nfrom typing import Iterable" in out

    def test_change_recorded(self, tmp_path):
        path = _write_slide(tmp_path / "slides_x.py", _DECK_WITH_PREAMBLE)
        result = normalize_file(path, operations=["preamble_code"])
        ops = [c for c in result.changes if c.operation == "preamble_code"]
        assert len(ops) == 1
        assert ops[0].line == 3

    def test_runs_by_default(self, tmp_path):
        assert "preamble_code" in ALL_OPERATIONS
        path = _write_slide(tmp_path / "slides_x.py", _DECK_WITH_PREAMBLE)
        normalize_file(path)  # no operations => all, including preamble_code
        out = path.read_text(encoding="utf-8")
        assert "# %%\nfrom typing import Iterable" in out

    def test_idempotent(self, tmp_path):
        path = _write_slide(tmp_path / "slides_x.py", _DECK_WITH_PREAMBLE)
        normalize_file(path)
        first = path.read_text(encoding="utf-8")
        result = normalize_file(path)
        assert [c for c in result.changes if c.operation == "preamble_code"] == []
        assert path.read_text(encoding="utf-8") == first

    def test_no_code_no_change(self, tmp_path):
        text = (
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("DE", "EN") }}\n\n'
            '# %% [markdown] lang="de" tags=["slide"] slide_id="a"\n#\n# ## A\n'
        )
        path = _write_slide(tmp_path / "slides_x.py", text)
        result = normalize_file(path, operations=["preamble_code"])
        assert [c for c in result.changes if c.operation == "preamble_code"] == []
        assert result.files_modified == 0

    def test_round_trip_preserved(self, tmp_path):
        path = _write_slide(tmp_path / "slides_x.py", _DECK_WITH_PREAMBLE)
        normalize_file(path)
        out = path.read_text(encoding="utf-8")
        preamble, cells = _split_raw_cells(out)
        assert _reconstruct(preamble, cells) == out

    def test_normalized_split_round_trips_and_is_render_neutral(self, tmp_path):
        """After normalize, the bilingual deck and its split halves agree.

        The wrapped code cell is shared (no lang), so split copies it verbatim
        to both halves and unify reproduces the normalized text byte-for-byte —
        the conversion is finally render-neutral (issue #253).
        """
        from clm.slides.split import split_text, unify_texts

        path = _write_slide(tmp_path / "slides_x.py", _DECK_WITH_PREAMBLE)
        normalize_file(path)
        normalized = path.read_text(encoding="utf-8")
        de_text, en_text = split_text(normalized)
        assert unify_texts(de_text, en_text) == normalized
        # The import survives as a shared code cell in BOTH halves.
        assert "# %%\nfrom typing import Iterable" in de_text
        assert "# %%\nfrom typing import Iterable" in en_text

    def test_clike_wraps_with_slash_marker(self, tmp_path):
        text = (
            "// j2 from 'macros.j2' import header\n"
            '// {{ header("DE", "EN") }}\n'
            "using System;\n\n"
            '// %% [markdown] lang="de" tags=["slide"] slide_id="a"\n//\n// ## A\n'
        )
        path = _write_slide(tmp_path / "slides_x.cs", text)
        normalize_file(path, operations=["preamble_code", "cell_spacing"])
        out = path.read_text(encoding="utf-8")
        assert "// %%\nusing System;" in out

    def test_resolves_validator_warning(self, tmp_path):
        from clm.slides.validator import validate_file

        path = _write_slide(tmp_path / "slides_x.py", _DECK_WITH_PREAMBLE)
        before = [f for f in validate_file(path, checks=["format"]).findings if "#253" in f.message]
        assert before  # the preamble-code warning is present
        normalize_file(path)
        after = [f for f in validate_file(path, checks=["format"]).findings if "#253" in f.message]
        assert after == []


class TestCellSpacing:
    def test_inserts_blank_between_cells(self, tmp_path):
        text = '# %% [markdown] lang="de"\n#\n# ## A\n# %% [markdown] lang="de"\n#\n# ## B\n'
        path = _write_slide(tmp_path / "slides_x.de.py", text)
        result = normalize_file(path, operations=["cell_spacing"])
        assert result.files_modified == 1
        out = path.read_text(encoding="utf-8")
        assert "# ## A\n\n# %% [markdown]" in out

    def test_inserts_markdown_lead(self, tmp_path):
        text = '# %% [markdown] lang="de" tags=["slide"] slide_id="a"\n# - Bullet\n'
        path = _write_slide(tmp_path / "slides_x.de.py", text)
        normalize_file(path, operations=["cell_spacing"])
        out = path.read_text(encoding="utf-8")
        assert out.startswith(
            '# %% [markdown] lang="de" tags=["slide"] slide_id="a"\n#\n# - Bullet'
        )

    def test_bare_blank_promoted_to_comment(self, tmp_path):
        # A bare empty first line (not a `#` comment) becomes the blank comment.
        text = '# %% [markdown] lang="de"\n\n# ## A\n'
        path = _write_slide(tmp_path / "slides_x.de.py", text)
        normalize_file(path, operations=["cell_spacing"])
        assert path.read_text(encoding="utf-8") == '# %% [markdown] lang="de"\n#\n# ## A\n'

    def test_j2_header_block_not_separated(self, tmp_path):
        text = (
            "# j2 from 'm' import header\n"
            '# {{ header_de("T") }}\n\n'
            '# %% [markdown] lang="de"\n#\n# ## A\n'
        )
        path = _write_slide(tmp_path / "slides_x.de.py", text)
        result = normalize_file(path, operations=["cell_spacing"])
        out = path.read_text(encoding="utf-8")
        # No blank inserted between the j2 import and the header macro.
        assert "import header\n# {{ header_de" in out
        assert result.files_modified == 0  # already conforming

    def test_idempotent(self, tmp_path):
        text = '# %% [markdown] lang="de"\n# ## A\n# %%\nx = 1\n'
        path = _write_slide(tmp_path / "slides_x.de.py", text)
        normalize_file(path, operations=["cell_spacing"])
        first = path.read_text(encoding="utf-8")
        result = normalize_file(path, operations=["cell_spacing"])
        assert result.files_modified == 0
        assert path.read_text(encoding="utf-8") == first

    def test_round_trip_preserved(self, tmp_path):
        text = '# %% [markdown] lang="de"\n# ## A\n# %%\nx = 1\n'
        path = _write_slide(tmp_path / "slides_x.de.py", text)
        normalize_file(path, operations=["cell_spacing"])
        out = path.read_text(encoding="utf-8")
        preamble, cells = _split_raw_cells(out)
        assert _reconstruct(preamble, cells) == out

    def test_runs_by_default(self, tmp_path):
        assert "cell_spacing" in ALL_OPERATIONS
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="a"\n# ## A\n\n'
            '# %% [markdown] lang="en" tags=["slide"] slide_id="a"\n# ## B\n'
        )
        path = _write_slide(tmp_path / "slides_x.de.py", text)
        normalize_file(path)  # no operations => all, including cell_spacing
        out = path.read_text(encoding="utf-8")
        assert "#\n# ## A" in out
        assert "#\n# ## B" in out

    def test_resolves_validator_warnings(self, tmp_path):
        from clm.slides.validator import validate_file

        text = '# %% [markdown] lang="de" slide_id="a"\n# ## A\n# %%\nx = 1\n'
        path = _write_slide(tmp_path / "slides_x.de.py", text)
        before = [
            f
            for f in validate_file(path, checks=["format"]).findings
            if "blank line" in f.message or "blank comment" in f.message
        ]
        assert before  # spacing warnings present
        normalize_file(path, operations=["cell_spacing"])
        after = [
            f
            for f in validate_file(path, checks=["format"]).findings
            if "blank line" in f.message or "blank comment" in f.message
        ]
        assert after == []


# Import needed for TestResultStatus
from clm.slides.normalizer import Change, ReviewItem  # noqa: E402
