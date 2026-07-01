"""Tests for clm.slides.validator."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from clm.slides.validator import (
    ALL_CHECKS,
    DEFAULT_CHECKS,
    Finding,
    ReviewMaterial,
    ValidationResult,
    has_voiceover_coverage_marker,
    validate_course,
    validate_directory,
    validate_file,
    validate_quick,
)


def _write_slide(tmp_path: Path, name: str, content: str) -> Path:
    """Write a slide file and return its path."""
    p = tmp_path / name
    p.write_text(dedent(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Format checks
# ---------------------------------------------------------------------------


class TestCheckFormat:
    def test_well_formed_no_findings(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_ok.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            #
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            #
            # ## Title

            # %% tags=["keep"]
            x = 1
            """,
        )
        result = validate_file(p, checks=["format"])
        assert result.findings == []

    def test_malformed_tags_attribute(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_bad_tags.py",
            """\
            # %% [markdown] tags=broken
            #
            # ## Bad
            """,
        )
        result = validate_file(p, checks=["format"])
        assert len(result.findings) == 1
        assert result.findings[0].category == "format"
        assert "Malformed tags" in result.findings[0].message

    def test_malformed_lang_attribute(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_bad_lang.py",
            """\
            # %% [markdown] lang=de tags=["slide"]
            #
            # ## Bad
            """,
        )
        result = validate_file(p, checks=["format"])
        assert len(result.findings) == 1
        assert result.findings[0].category == "format"
        assert "Malformed lang" in result.findings[0].message

    def test_malformed_marker_double_hash(self, tmp_path):
        # `## %%` (an extra leading hash) is NOT a cell boundary, so the parser
        # swallows it into the previous cell's body. The raw-source scan must
        # surface it directly rather than leave the misleading "duplicate
        # slide_id" downstream symptom (Fix #8).
        p = _write_slide(
            tmp_path,
            "slides_double_hash.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            #
            # ## Titel

            ## %% [markdown] lang="en" tags=["slide"]
            #
            # ## Title
            """,
        )
        result = validate_file(p, checks=["format"])
        malformed = [f for f in result.findings if "malformed cell marker" in f.message]
        assert len(malformed) == 1
        assert malformed[0].severity == "error"
        assert malformed[0].category == "format"
        assert malformed[0].line == 5
        assert "did you mean '# %%'" in malformed[0].message
        # The message must classify into the reserved malformed-marker kind.
        from clm.slides.validation_summary import classify_kind

        assert classify_kind(malformed[0].message) == "malformed-marker"

    def test_malformed_marker_clean_deck_none(self, tmp_path):
        # A legit `# %%` deck — including a body line that merely mentions `## %%`
        # NOT at column 0 — yields no malformed-marker finding.
        p = _write_slide(
            tmp_path,
            "slides_clean.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            #
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            #
            # ## Title
            """,
        )
        result = validate_file(p, checks=["format"])
        assert [f for f in result.findings if "malformed cell marker" in f.message] == []

    def test_malformed_marker_cpp_triple_slash(self, tmp_path):
        # The `//`-token languages (e.g. `.cpp`) use `// %%`; `/// %%` is the
        # near-miss typo and must be caught too.
        p = _write_slide(
            tmp_path,
            "slides.cpp",
            """\
            // %% [markdown] lang="de" tags=["slide"]
            //
            // ## Titel

            /// %% [markdown] lang="en" tags=["slide"]
            //
            // ## Title
            """,
        )
        result = validate_file(p, checks=["format"])
        malformed = [f for f in result.findings if "malformed cell marker" in f.message]
        assert len(malformed) == 1
        assert malformed[0].line == 5
        assert "did you mean '// %%'" in malformed[0].message

    def test_j2_cells_skipped(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_j2.py",
            """\
            # j2 from 'macros.j2' import header
            # {{ header("T", "T") }}

            # %% [markdown] lang="de" tags=["slide"]
            #
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            #
            # ## Title
            """,
        )
        result = validate_file(p, checks=["format"])
        assert result.findings == []

    def test_preamble_code_warns(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_preamble.py",
            """\
            # j2 from 'macros.j2' import header
            # {{ header("Regeln", "Rules") }}
            from typing import Iterable

            # %% [markdown] lang="de" tags=["slide"] slide_id="gh"
            #
            # ## Hinweise
            """,
        )
        result = validate_file(p, checks=["format"])
        pc = [f for f in result.findings if "#253" in f.message]
        assert len(pc) == 1
        assert pc[0].severity == "warning"
        assert pc[0].category == "format"
        assert pc[0].line == 3

    def test_preamble_code_clean_no_finding(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_preamble_ok.py",
            """\
            # j2 from 'macros.j2' import header
            # {{ header("Regeln", "Rules") }}

            # %%
            from typing import Iterable

            # %% [markdown] lang="de" tags=["slide"] slide_id="gh"
            #
            # ## Hinweise
            """,
        )
        result = validate_file(p, checks=["format"])
        assert [f for f in result.findings if "#253" in f.message] == []

    def test_preamble_code_is_never_an_error(self, tmp_path):
        # Gate-safety: the preamble-code finding must stay a warning so it never
        # breaks the 1.8 validator gate (which escalates only named checks).
        p = _write_slide(
            tmp_path,
            "slides_preamble_err.py",
            """\
            # j2 from 'macros.j2' import header
            # {{ header("Regeln", "Rules") }}
            from typing import Iterable

            # %% [markdown] lang="de" tags=["slide"] slide_id="gh"
            #
            # ## Hinweise
            """,
        )
        result = validate_file(p, checks=["format"])
        assert all(f.severity != "error" for f in result.findings)


# ---------------------------------------------------------------------------
# Tag checks
# ---------------------------------------------------------------------------


class TestCheckTags:
    def test_valid_tags_no_findings(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_ok.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title

            # %% tags=["start"]
            # starter

            # %% tags=["completed"]
            result = 42
            """,
        )
        result = validate_file(p, checks=["tags"])
        assert result.findings == []

    def test_unrecognized_tag(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_bad.py",
            """\
            # %% tags=["bogus_tag"]
            x = 1
            """,
        )
        result = validate_file(p, checks=["tags"])
        errors = [f for f in result.findings if f.severity == "error"]
        assert len(errors) == 1
        assert "bogus_tag" in errors[0].message

    def test_tag_wrong_cell_type(self, tmp_path):
        # "answer" is valid for markdown but not code
        p = _write_slide(
            tmp_path,
            "slides_wrong.py",
            """\
            # %% tags=["answer"]
            x = 1
            """,
        )
        result = validate_file(p, checks=["tags"])
        warnings = [f for f in result.findings if f.severity == "warning"]
        assert len(warnings) == 1
        assert "answer" in warnings[0].message

    def test_unclosed_start_at_eof(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_unclosed.py",
            """\
            # %% tags=["start"]
            # starter code

            # %% tags=["keep"]
            x = 1
            """,
        )
        result = validate_file(p, checks=["tags"])
        errors = [f for f in result.findings if f.severity == "error"]
        assert len(errors) == 1
        assert "start" in errors[0].message
        assert "no matching" in errors[0].message

    def test_completed_without_start(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_orphan.py",
            """\
            # %% tags=["completed"]
            result = 42
            """,
        )
        result = validate_file(p, checks=["tags"])
        errors = [f for f in result.findings if f.severity == "error"]
        assert len(errors) == 1
        assert "completed" in errors[0].message
        assert "without" in errors[0].message
        # No 'keep' predecessor: the generic suggestion, no #233 hint.
        assert "did you mean 'start'" not in (errors[0].suggestion or "")

    def test_completed_after_keep_hints_at_mistag(self, tmp_path):
        # #233 item 4(b): an incremental build whose "before" cell was tagged
        # 'keep' instead of 'start' leaves the 'completed' orphaned — the
        # suggestion points at the likely mis-tag.
        p = _write_slide(
            tmp_path,
            "slides_keep_completed.py",
            """\
            # %% tags=["keep"]
            class PointV2:
                def __init__(self):
                    pass

            # %% tags=["completed"]
            class PointV2:
                def __init__(self, x, y):
                    self.x, self.y = x, y
            """,
        )
        result = validate_file(p, checks=["tags"])
        errors = [f for f in result.findings if f.severity == "error"]
        assert len(errors) == 1
        assert "completed" in errors[0].message
        assert "did you mean 'start'" in (errors[0].suggestion or "")
        assert "line 1" in (errors[0].suggestion or "")

    def test_consecutive_starts_without_completed(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_double_start.py",
            """\
            # %% tags=["start"]
            # first

            # %% tags=["start"]
            # second

            # %% tags=["completed"]
            result = 42
            """,
        )
        result = validate_file(p, checks=["tags"])
        errors = [f for f in result.findings if f.severity == "error"]
        assert len(errors) == 1
        assert "no matching" in errors[0].message

    def test_workshop_tag_recognized(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_ws_ok.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "workshop"]
            # ## Workshop: Übung

            # %% [markdown] lang="en" tags=["subslide", "workshop"]
            # ## Workshop: Exercise

            # %%
            total = 1 + 2
            """,
        )
        result = validate_file(p, checks=["tags"])
        # No unrecognized-tag errors
        tag_errors = [f for f in result.findings if "nrecognized" in f.message]
        assert tag_errors == []

    def test_orphan_end_workshop_warns(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_orphan_end.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "end-workshop"]
            # ## Without preceding workshop
            """,
        )
        result = validate_file(p, checks=["tags"])
        warnings = [
            w for w in result.findings if w.severity == "warning" and "end-workshop" in w.message
        ]
        assert len(warnings) == 1

    def test_end_workshop_tag_recognized(self, tmp_path):
        """``end-workshop`` is a valid markdown tag — no unrecognized-tag error."""
        p = _write_slide(
            tmp_path,
            "slides_end_ws_ok.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "workshop"]
            # ## Workshop

            # %% [markdown] lang="de" tags=["subslide", "end-workshop"]
            # ## Next topic
            """,
        )
        result = validate_file(p, checks=["tags"])
        tag_errors = [f for f in result.findings if "nrecognized" in f.message]
        assert tag_errors == []

    def test_end_workshop_on_code_cell_warns(self, tmp_path):
        """``end-workshop`` is markdown-only, so it warns on a code cell."""
        p = _write_slide(
            tmp_path,
            "slides_end_ws_code.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "workshop"]
            # ## Workshop

            # %% tags=["end-workshop"]
            x = 1
            """,
        )
        result = validate_file(p, checks=["tags"])
        warnings = [f for f in result.findings if f.severity == "warning"]
        assert any("end-workshop" in w.message and "code" in w.message for w in warnings)

    def test_valid_start_completed_pair(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_pair.py",
            """\
            # %% tags=["start"]
            # starter

            # %% tags=["completed"]
            result = 1 + 2

            # %% tags=["start"]
            # another starter

            # %% tags=["completed"]
            result2 = 3 + 4
            """,
        )
        result = validate_file(p, checks=["tags"])
        assert result.findings == []

    def test_bilingual_interleaved_start_completed_pair(self, tmp_path):
        # The canonical interleaved bilingual layout
        # [DE_start, EN_start, DE_completed, EN_completed] must not produce
        # tag errors. Each language stream has its own pending-start tracker.
        p = _write_slide(
            tmp_path,
            "slides_interleaved_tags.py",
            """\
            # %% lang="de" tags=["start"]
            def f():
                pass

            # %% lang="en" tags=["start"]
            def f():
                pass

            # %% lang="de" tags=["completed"]
            def f() -> None:
                pass

            # %% lang="en" tags=["completed"]
            def f() -> None:
                pass
            """,
        )
        result = validate_file(p, checks=["tags"])
        assert result.findings == []

    def test_bilingual_cohesion_start_completed_pair(self, tmp_path):
        # The cohesion layout
        # [DE_start, DE_completed, EN_start, EN_completed] must also pass.
        p = _write_slide(
            tmp_path,
            "slides_cohesion_tags.py",
            """\
            # %% lang="de" tags=["start"]
            def f():
                pass

            # %% lang="de" tags=["completed"]
            def f() -> None:
                pass

            # %% lang="en" tags=["start"]
            def f():
                pass

            # %% lang="en" tags=["completed"]
            def f() -> None:
                pass
            """,
        )
        result = validate_file(p, checks=["tags"])
        assert result.findings == []

    def test_two_consecutive_same_lang_starts_still_errors(self, tmp_path):
        # Per-language tracking must still flag two ``start`` cells in the
        # same language stream with no intervening ``completed``.
        p = _write_slide(
            tmp_path,
            "slides_double_de_start.py",
            """\
            # %% lang="de" tags=["start"]
            # first

            # %% lang="de" tags=["start"]
            # second

            # %% lang="de" tags=["completed"]
            result = 42
            """,
        )
        result = validate_file(p, checks=["tags"])
        errors = [f for f in result.findings if f.severity == "error"]
        assert len(errors) == 1
        assert "no matching" in errors[0].message


# ---------------------------------------------------------------------------
# Workshop-heading checks (issue #78)
# ---------------------------------------------------------------------------


def _workshop_heading_warnings(result):
    return [
        f for f in result.findings if f.severity == "warning" and "Workshop' heading" in f.message
    ]


class TestCheckWorkshopHeadings:
    def test_heading_with_workshop_tag_is_ok(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "ws_tag_ok.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "workshop"]
            # ## Workshop: Übung
            """,
        )
        result = validate_file(p, checks=["tags"])
        assert _workshop_heading_warnings(result) == []

    def test_heading_with_workshop_slide_id_is_ok(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "ws_slideid_ok.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="workshop-uebung"
            # # Workshop: Übung
            """,
        )
        result = validate_file(p, checks=["tags"])
        assert _workshop_heading_warnings(result) == []

    def test_heading_without_workshop_scope_warns(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "ws_missing.py",
            """\
            # %% [markdown] lang="de" tags=["subslide"]
            # ## Workshop: Übung

            # %%
            total = 1 + 2
            """,
        )
        result = validate_file(p, checks=["tags"])
        warnings = _workshop_heading_warnings(result)
        assert len(warnings) == 1
        assert warnings[0].category == "tags"
        assert warnings[0].line == 1

    def test_no_workshop_heading_is_ok(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "no_ws.py",
            """\
            # %% [markdown] lang="de" tags=["subslide"]
            # ## Reguläres Thema

            # %%
            total = 1 + 2
            """,
        )
        result = validate_file(p, checks=["tags"])
        assert _workshop_heading_warnings(result) == []

    def test_workshops_word_is_not_a_workshop_heading(self, tmp_path):
        """``\\bWorkshop\\b`` must not match plurals/compounds like 'Workshops'."""
        p = _write_slide(
            tmp_path,
            "workshops.py",
            """\
            # %% [markdown] lang="de" tags=["subslide"]
            # ## Workshops Overview
            """,
        )
        result = validate_file(p, checks=["tags"])
        assert _workshop_heading_warnings(result) == []

    def test_heading_count_and_whitespace_variants_warn(self, tmp_path):
        # Single '#', many '#', and extra surrounding whitespace must all be
        # detected as workshop headings (and flagged, since none is scoped).
        p = _write_slide(
            tmp_path,
            "ws_variants.py",
            """\
            # %% [markdown] lang="de" tags=["subslide"]
            # #    Workshop one

            # %% [markdown] lang="en" tags=["subslide"]
            # ####   Workshop two
            """,
        )
        result = validate_file(p, checks=["tags"])
        assert len(_workshop_heading_warnings(result)) == 2

    def test_continuation_heading_inside_open_workshop_is_ok(self, tmp_path):
        # A 'Workshop (Continued)' heading inside an already-open workshop
        # scope carries no tag of its own and must not be flagged.
        p = _write_slide(
            tmp_path,
            "ws_continued.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "workshop"]
            # ## Workshop: Übung

            # %% [markdown] lang="de" tags=["subslide"]
            # ## Workshop (Fortsetzung)
            """,
        )
        result = validate_file(p, checks=["tags"])
        assert _workshop_heading_warnings(result) == []

    def test_heading_after_end_workshop_warns(self, tmp_path):
        # Once 'end-workshop' closes the scope, a later unscoped workshop
        # heading is again outside any range and is flagged.
        p = _write_slide(
            tmp_path,
            "ws_after_end.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "workshop"]
            # ## Workshop: First

            # %% [markdown] lang="de" tags=["subslide", "end-workshop"]
            # ## Next topic

            # %% [markdown] lang="de" tags=["subslide"]
            # ## Workshop: Second
            """,
        )
        result = validate_file(p, checks=["tags"])
        warnings = _workshop_heading_warnings(result)
        assert len(warnings) == 1
        # The flagged heading is the second (unscoped) one.
        assert "Second" not in warnings[0].message  # message is generic
        assert warnings[0].line > 1

    def test_quick_mode_flags_unscoped_workshop_heading(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "ws_quick.py",
            """\
            # %% [markdown] lang="de" tags=["subslide"]
            # ## Workshop: Übung
            """,
        )
        result = validate_quick(p)
        assert len(_workshop_heading_warnings(result)) == 1


# ---------------------------------------------------------------------------
# Workshop tag symmetry (DE/EN)
# ---------------------------------------------------------------------------


def _workshop_symmetry_warnings(result):
    return [
        f for f in result.findings if f.category == "pairing" and "disagrees on the" in f.message
    ]


class TestCheckWorkshopTagSymmetry:
    """A workshop tag on only one language's heading leaks solutions in the
    other language's split build — flag the asymmetry on the bilingual source.
    """

    def test_asymmetric_workshop_tag_warns(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "asym.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "workshop"]
            # ## Mini-Workshop: Übung

            # %% [markdown] lang="en" tags=["subslide"]
            # ## Mini Workshop: Exercise
            """,
        )
        result = validate_file(p, checks=["pairing"])
        warnings = _workshop_symmetry_warnings(result)
        assert len(warnings) == 1
        assert "workshop" in warnings[0].message

    def test_symmetric_workshop_tag_ok(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "sym.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "workshop"]
            # ## Mini-Workshop: Übung

            # %% [markdown] lang="en" tags=["subslide", "workshop"]
            # ## Mini Workshop: Exercise
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert _workshop_symmetry_warnings(result) == []

    def test_asymmetric_end_workshop_warns(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "asym_end.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "end-workshop"]
            # ## Ende

            # %% [markdown] lang="en" tags=["subslide"]
            # ## End
            """,
        )
        result = validate_file(p, checks=["pairing"])
        warnings = _workshop_symmetry_warnings(result)
        assert len(warnings) == 1
        assert "end-workshop" in warnings[0].message

    def test_single_language_file_no_warning(self, tmp_path):
        # A split single-language file has no DE/EN pairs, so the check is a
        # no-op even when a workshop heading is present.
        p = _write_slide(
            tmp_path,
            "split.en.py",
            """\
            # %% [markdown] lang="en" tags=["subslide", "workshop"]
            # ## Mini Workshop: Exercise
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert _workshop_symmetry_warnings(result) == []


# ---------------------------------------------------------------------------
# Pairing checks
# ---------------------------------------------------------------------------


class TestCheckPairing:
    def test_balanced_pairing(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_paired.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="title"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="title"
            # ## Title

            # %% tags=["keep"]
            x = 1
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert result.findings == []

    def test_count_mismatch(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_unbalanced.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="title"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="title"
            # ## Title

            # %% [markdown] lang="de" tags=["subslide"] slide_id="extra"
            # ## Extra German
            """,
        )
        result = validate_file(p, checks=["pairing"])
        errors = [f for f in result.findings if f.severity == "error"]
        assert len(errors) == 1
        assert "mismatch" in errors[0].message
        assert "2 German" in errors[0].message
        assert "1 English" in errors[0].message

    def test_tag_mismatch_in_pair(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_tag_mismatch.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="title"
            # ## Titel

            # %% [markdown] lang="en" tags=["subslide"] slide_id="title"
            # ## Title
            """,
        )
        result = validate_file(p, checks=["pairing"])
        warnings = [f for f in result.findings if f.severity == "warning"]
        assert len(warnings) == 1
        assert "Tag mismatch" in warnings[0].message

    def test_voiceover_cells_excluded_from_pairing(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_vo.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="title"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="title"
            # ## Title

            # %% [markdown] lang="de" tags=["voiceover"]
            # Voiceover text

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover text
            """,
        )
        result = validate_file(p, checks=["pairing"])
        errors = [f for f in result.findings if f.severity == "error"]
        assert errors == []

    def test_language_neutral_cells_excluded(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_neutral.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="title"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="title"
            # ## Title

            # %% tags=["keep"]
            x = 1
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert result.findings == []


class TestSplitFilePairing:
    """Issue #160: the bilingual DE/EN pairing checks must be suppressed on
    single-language split halves (``*.de.py`` / ``*.en.py``), which by design
    contain cells of only one language. Per-file slide_id integrity and the
    format/tags checks must keep running, and bilingual files are unaffected.
    """

    def test_de_split_file_no_count_mismatch(self, tmp_path):
        # A .de.py with only German cells must NOT trip the DE/EN count check.
        p = _write_slide(
            tmp_path,
            "slides_010.de.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Einführung

            # %% [markdown] lang="de" tags=["subslide"] slide_id="details"
            # ## Mehr
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert result.findings == []

    def test_en_split_file_no_count_mismatch(self, tmp_path):
        # Symmetric: a .en.py with only English cells is also clean.
        p = _write_slide(
            tmp_path,
            "slides_010.en.py",
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Introduction

            # %% [markdown] lang="en" tags=["subslide"] slide_id="details"
            # ## More
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert result.findings == []

    def test_split_file_still_runs_format_and_tags(self, tmp_path):
        # The applicable checks keep firing; only the bilingual pairing
        # checks are suppressed. A malformed tag is still an error, and there
        # is no false count-mismatch finding.
        p = _write_slide(
            tmp_path,
            "slides_020.de.py",
            """\
            # %% [markdown] lang="de" tags=broken
            # ## Kaputt
            """,
        )
        result = validate_file(p, checks=None)
        assert any(
            f.category == "format" and "Malformed tags" in f.message for f in result.findings
        )
        assert not any("cell count mismatch" in f.message for f in result.findings)

    def test_split_file_still_checks_slide_ids(self, tmp_path):
        # slide_id integrity is a per-file property and must keep running on
        # a split half — a duplicate id is still a (pairing-category) error.
        p = _write_slide(
            tmp_path,
            "slides_030.de.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="dup"
            # ## A

            # %% [markdown] lang="de" tags=["slide"] slide_id="dup"
            # ## B
            """,
        )
        result = validate_file(p, checks=["pairing"])
        errors = [f for f in result.findings if f.severity == "error"]
        assert len(errors) == 1
        assert "duplicate slide_id" in errors[0].message
        assert not any("cell count mismatch" in f.message for f in result.findings)

    def test_bilingual_file_still_flags_count_mismatch(self, tmp_path):
        # Control: a bilingual deck (no .de/.en suffix) is unaffected — the
        # count check still fires.
        p = _write_slide(
            tmp_path,
            "slides_040.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="de" tags=["subslide"]
            # ## Extra German
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert any("cell count mismatch" in f.message for f in result.findings)

    def test_directory_split_pair_no_false_count_mismatch(self, tmp_path):
        # A topic directory holding a complete split pair validates clean: no
        # per-file count mismatch, and the byte-identical shared cell passes
        # the cross-file parity check.
        topic = tmp_path / "topic"
        topic.mkdir()
        _write_slide(
            topic,
            "slides_intro.de.py",
            """\
            # j2 from "macros.j2" import header_de
            # {{ header_de("Mein Titel") }}

            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Einführung

            # %% tags=["keep"]
            x = 1
            """,
        )
        _write_slide(
            topic,
            "slides_intro.en.py",
            """\
            # j2 from "macros.j2" import header_en
            # {{ header_en("My Title") }}

            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Introduction

            # %% tags=["keep"]
            x = 1
            """,
        )
        result = validate_directory(topic, checks=["pairing"])
        assert not any("cell count mismatch" in f.message for f in result.findings)
        assert result.findings == []

    def test_directory_split_pair_still_catches_divergent_shared_cell(self, tmp_path):
        # The cross-file parity check is unchanged: a shared cell that differs
        # between the two halves is still reported as a pairing error.
        topic = tmp_path / "topic"
        topic.mkdir()
        _write_slide(
            topic,
            "slides_intro.de.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Einführung

            # %% tags=["keep"]
            x = 1
            """,
        )
        _write_slide(
            topic,
            "slides_intro.en.py",
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Introduction

            # %% tags=["keep"]
            x = 99
            """,
        )
        result = validate_directory(topic, checks=["pairing"])
        parity_errors = [
            f
            for f in result.findings
            if f.severity == "error" and "shared cell" in f.message.lower()
        ]
        assert len(parity_errors) == 1


class TestSplitSlideIdParity:
    """Cross-file ``slide_id`` parity for split pairs — the #162 detective.

    ``slide_id`` is the cross-language join key (voiceover ``for_slide``,
    ``unify``, extract/inline). A born-split deck or a per-file ``assign-ids``
    on one half silently diverges the two halves; this check makes it loud.
    """

    @staticmethod
    def _id_findings(result):
        return [
            f
            for f in result.findings
            if f.severity == "warning" and "slide_id" in f.message and "diverge" in f.message
        ]

    def _pair(self, parent, de_body: str, en_body: str):
        _write_slide(parent, "slides_intro.de.py", de_body)
        _write_slide(parent, "slides_intro.en.py", en_body)

    def test_matching_ids_are_clean(self, tmp_path):
        topic = tmp_path / "topic"
        topic.mkdir()
        self._pair(
            topic,
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Einführung

            # %% [markdown] lang="de" tags=["slide"] slide_id="setup"
            # ## Aufbau
            """,
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Introduction

            # %% [markdown] lang="en" tags=["slide"] slide_id="setup"
            # ## Setup
            """,
        )
        result = validate_directory(topic, checks=["pairing"])
        assert self._id_findings(result) == []

    def test_set_mismatch_is_flagged(self, tmp_path):
        topic = tmp_path / "topic"
        topic.mkdir()
        self._pair(
            topic,
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Einführung

            # %% [markdown] lang="de" tags=["slide"] slide_id="extra"
            # ## Extra
            """,
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Introduction
            """,
        )
        result = validate_directory(topic, checks=["pairing"])
        findings = self._id_findings(result)
        assert len(findings) == 1
        assert "sets diverge" in findings[0].message
        assert "extra" in findings[0].message

    def test_order_mismatch_is_flagged(self, tmp_path):
        topic = tmp_path / "topic"
        topic.mkdir()
        self._pair(
            topic,
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="a"
            # ## A

            # %% [markdown] lang="de" tags=["slide"] slide_id="b"
            # ## B
            """,
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="b"
            # ## B

            # %% [markdown] lang="en" tags=["slide"] slide_id="a"
            # ## A
            """,
        )
        result = validate_directory(topic, checks=["pairing"])
        findings = self._id_findings(result)
        assert len(findings) == 1
        assert "order diverges" in findings[0].message

    def test_set_mismatch_reported_once_in_directory_run(self, tmp_path):
        # The per-file pass runs with cross_file_parity=False, so a directory
        # run reports the divergence exactly once (not once per half).
        topic = tmp_path / "topic"
        topic.mkdir()
        self._pair(
            topic,
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Einführung

            # %% [markdown] lang="de" tags=["slide"] slide_id="extra"
            # ## Extra
            """,
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Introduction
            """,
        )
        result = validate_directory(topic, checks=["pairing"])
        assert len(self._id_findings(result)) == 1

    def test_single_file_with_twin_catches_divergence(self, tmp_path):
        # The single-file path (CLI standalone / pre-commit gate) catches twin
        # divergence when the sibling exists on disk.
        self._pair(
            tmp_path,
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Einführung

            # %% [markdown] lang="de" tags=["slide"] slide_id="extra"
            # ## Extra
            """,
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Introduction
            """,
        )
        result = validate_file(tmp_path / "slides_intro.de.py", checks=["pairing"])
        assert len(self._id_findings(result)) == 1
        # Symmetric: editing/validating the EN half catches it too.
        result_en = validate_file(tmp_path / "slides_intro.en.py", checks=["pairing"])
        assert len(self._id_findings(result_en)) == 1

    def test_single_file_without_twin_is_silent(self, tmp_path):
        # A lone .de.py with no .en.py on disk must not error or crash.
        p = _write_slide(
            tmp_path,
            "slides_intro.de.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Einführung
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert self._id_findings(result) == []


class TestSplitCompanionForSlideParity:
    """Cross-file ``for_slide`` parity for voiceover companions — the companion
    arm of the #162 detective (the both-language voiceover compatibility check).

    Separated voiceover lives in ``voiceover_X.de.py`` / ``voiceover_X.en.py``;
    each narration cell's ``for_slide`` names the slide it narrates. If one
    companion narrates a slide its twin does not, that language ships without
    narration silently — this check makes it loud.
    """

    @staticmethod
    def _companion_findings(result):
        return [
            f
            for f in result.findings
            if f.severity == "warning" and "voiceover companion" in f.message
        ]

    def _slide_pair(self, parent):
        # Two slide-start cells in slide_id parity, so the slide_id detective is
        # clean and only the companion check can fire.
        _write_slide(
            parent,
            "slides_intro.de.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Einführung

            # %% [markdown] lang="de" tags=["slide"] slide_id="setup"
            # ## Aufbau
            """,
        )
        _write_slide(
            parent,
            "slides_intro.en.py",
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Introduction

            # %% [markdown] lang="en" tags=["slide"] slide_id="setup"
            # ## Setup
            """,
        )

    @staticmethod
    def _vo(lang: str, *pairs: tuple[str, str]) -> str:
        """Build companion text: each ``(for_slide, body)`` is one narration cell."""
        cells = []
        for for_slide, body in pairs:
            cells.append(
                f'# %% [markdown] lang="{lang}" tags=["voiceover"] '
                f'for_slide="{for_slide}"\n#\n# {body}\n'
            )
        return "\n".join(cells) + "\n"

    def test_matching_for_slides_are_clean(self, tmp_path):
        self._slide_pair(tmp_path)
        _write_slide(
            tmp_path,
            "voiceover_intro.de.py",
            self._vo("de", ("intro", "Intro DE"), ("setup", "Setup DE")),
        )
        _write_slide(
            tmp_path,
            "voiceover_intro.en.py",
            self._vo("en", ("intro", "Intro EN"), ("setup", "Setup EN")),
        )
        result = validate_directory(tmp_path, checks=["pairing"])
        assert self._companion_findings(result) == []

    def test_companion_set_mismatch_is_flagged(self, tmp_path):
        self._slide_pair(tmp_path)
        _write_slide(
            tmp_path,
            "voiceover_intro.de.py",
            self._vo("de", ("intro", "Intro DE"), ("setup", "Setup DE")),
        )
        _write_slide(
            tmp_path,
            "voiceover_intro.en.py",
            self._vo("en", ("intro", "Intro EN")),
        )
        result = validate_directory(tmp_path, checks=["pairing"])
        findings = self._companion_findings(result)
        assert len(findings) == 1
        assert "for_slide sets diverge" in findings[0].message
        assert "setup" in findings[0].message
        assert "only on DE" in findings[0].message

    def test_multiplicity_difference_is_clean(self, tmp_path):
        # One language may split a slide's narration across more cells; the
        # *set* of for_slide is equal, so this is not a divergence.
        self._slide_pair(tmp_path)
        _write_slide(
            tmp_path,
            "voiceover_intro.de.py",
            self._vo("de", ("intro", "Intro DE part 1"), ("intro", "Intro DE part 2")),
        )
        _write_slide(
            tmp_path,
            "voiceover_intro.en.py",
            self._vo("en", ("intro", "Intro EN")),
        )
        result = validate_directory(tmp_path, checks=["pairing"])
        assert self._companion_findings(result) == []

    def test_one_sided_companion_is_flagged(self, tmp_path):
        self._slide_pair(tmp_path)
        _write_slide(
            tmp_path,
            "voiceover_intro.de.py",
            self._vo("de", ("intro", "Intro DE")),
        )
        result = validate_directory(tmp_path, checks=["pairing"])
        findings = self._companion_findings(result)
        assert len(findings) == 1
        assert "does not" in findings[0].message
        assert "EN half ships without narration" in findings[0].message

    def test_one_sided_companion_en_only_is_flagged(self, tmp_path):
        # Symmetric to the DE-only case: the EN companion exists alone, so the
        # DE half ships without narration. Pins the language ternary direction.
        self._slide_pair(tmp_path)
        _write_slide(
            tmp_path,
            "voiceover_intro.en.py",
            self._vo("en", ("intro", "Intro EN")),
        )
        result = validate_directory(tmp_path, checks=["pairing"])
        findings = self._companion_findings(result)
        assert len(findings) == 1
        assert "DE half ships without narration" in findings[0].message
        assert findings[0].file.endswith("voiceover_intro.en.py")

    def test_no_companions_is_clean(self, tmp_path):
        self._slide_pair(tmp_path)
        result = validate_directory(tmp_path, checks=["pairing"])
        assert self._companion_findings(result) == []

    def test_set_mismatch_reported_once_in_directory_run(self, tmp_path):
        # The per-file pass runs with cross_file_parity=False, so a directory
        # run reports the divergence exactly once (not once per half).
        self._slide_pair(tmp_path)
        _write_slide(
            tmp_path,
            "voiceover_intro.de.py",
            self._vo("de", ("intro", "Intro DE"), ("setup", "Setup DE")),
        )
        _write_slide(
            tmp_path,
            "voiceover_intro.en.py",
            self._vo("en", ("intro", "Intro EN")),
        )
        result = validate_directory(tmp_path, checks=["pairing"])
        assert len(self._companion_findings(result)) == 1

    def test_single_file_with_twin_catches_companion_divergence(self, tmp_path):
        self._slide_pair(tmp_path)
        _write_slide(
            tmp_path,
            "voiceover_intro.de.py",
            self._vo("de", ("intro", "Intro DE"), ("setup", "Setup DE")),
        )
        _write_slide(
            tmp_path,
            "voiceover_intro.en.py",
            self._vo("en", ("intro", "Intro EN")),
        )
        result_de = validate_file(tmp_path / "slides_intro.de.py", checks=["pairing"])
        assert len(self._companion_findings(result_de)) == 1
        # Symmetric: validating the EN deck half catches it too.
        result_en = validate_file(tmp_path / "slides_intro.en.py", checks=["pairing"])
        assert len(self._companion_findings(result_en)) == 1

    def test_single_file_without_twin_is_silent(self, tmp_path):
        # A lone .de.py deck half with no .en.py twin: the cross-file parity
        # never runs (no twin), so a lone DE companion is not flagged here.
        _write_slide(
            tmp_path,
            "slides_intro.de.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Einführung
            """,
        )
        _write_slide(
            tmp_path,
            "voiceover_intro.de.py",
            self._vo("de", ("intro", "Intro DE")),
        )
        result = validate_file(tmp_path / "slides_intro.de.py", checks=["pairing"])
        assert self._companion_findings(result) == []

    def test_preserve_marker_equivalence(self, tmp_path):
        # for_slide referencing a preserve-marked id (!intro) on one side and
        # the bare id on the other is the same join key after stripping.
        self._slide_pair(tmp_path)
        _write_slide(
            tmp_path,
            "voiceover_intro.de.py",
            self._vo("de", ("!intro", "Intro DE"), ("setup", "Setup DE")),
        )
        _write_slide(
            tmp_path,
            "voiceover_intro.en.py",
            self._vo("en", ("intro", "Intro EN"), ("setup", "Setup EN")),
        )
        result = validate_directory(tmp_path, checks=["pairing"])
        assert self._companion_findings(result) == []


class TestSplitTagParity:
    """Cross-language tag-set parity for split pairs (Issue #198)."""

    @staticmethod
    def _tag_warnings(result):
        return [
            f for f in result.findings if f.severity == "warning" and "mismatched tags" in f.message
        ]

    def test_mismatched_localized_code_tag_warns(self, tmp_path):
        # The exact #198 case: a localized (lang) code cell with no slide_id whose
        # `keep` tag was added on one half only. _check_shared_cell_parity never
        # sees it (it's not a shared cell); the tag-parity check must catch it.
        topic = tmp_path / "topic"
        topic.mkdir()
        _write_slide(
            topic,
            "slides_rag.de.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Einführung

            # %% lang="de" tags=["keep"]
            antwort = invoke("Frage")
            """,
        )
        _write_slide(
            topic,
            "slides_rag.en.py",
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Introduction

            # %% lang="en"
            answer = invoke("question")
            """,
        )
        result = validate_directory(topic, checks=["pairing"])
        warnings = self._tag_warnings(result)
        assert len(warnings) == 1
        assert "only on DE: ['keep']" in warnings[0].message

    def test_mismatched_markdown_tag_warns(self, tmp_path):
        topic = tmp_path / "topic"
        topic.mkdir()
        _write_slide(
            topic,
            "slides_a.de.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "keep"] slide_id="vec"
            # ## Vektoren
            """,
        )
        _write_slide(
            topic,
            "slides_a.en.py",
            """\
            # %% [markdown] lang="en" tags=["subslide"] slide_id="vec"
            # ## Vectors
            """,
        )
        result = validate_directory(topic, checks=["pairing"])
        warnings = self._tag_warnings(result)
        assert len(warnings) == 1
        assert "only on DE: ['keep']" in warnings[0].message

    def test_matched_tags_clean(self, tmp_path):
        topic = tmp_path / "topic"
        topic.mkdir()
        _write_slide(
            topic,
            "slides_a.de.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "keep"] slide_id="vec"
            # ## Vektoren

            # %% lang="de" tags=["keep"]
            antwort = invoke("Frage")
            """,
        )
        _write_slide(
            topic,
            "slides_a.en.py",
            """\
            # %% [markdown] lang="en" tags=["keep", "subslide"] slide_id="vec"
            # ## Vectors

            # %% lang="en" tags=["keep"]
            answer = invoke("question")
            """,
        )
        result = validate_directory(topic, checks=["pairing"])
        # Tag order differs ("subslide","keep" vs "keep","subslide") but the sets
        # match, so no warning.
        assert self._tag_warnings(result) == []

    def test_length_mismatch_is_silent_on_tags(self, tmp_path):
        # A structural divergence (an extra cell on one half) must not produce a
        # tag-parity warning — positional pairing would be unreliable, so the
        # check bows out (the count itself is the shared-cell parity's concern).
        topic = tmp_path / "topic"
        topic.mkdir()
        _write_slide(
            topic,
            "slides_a.de.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="a"
            # ## A

            # %% [markdown] lang="de" tags=["subslide", "keep"] slide_id="b"
            # ## B
            """,
        )
        _write_slide(
            topic,
            "slides_a.en.py",
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="a"
            # ## A
            """,
        )
        result = validate_directory(topic, checks=["pairing"])
        assert self._tag_warnings(result) == []


class TestCheckOrdering:
    """DE/EN adjacency checks (canonical layout)."""

    def test_canonical_layout_passes(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_canonical.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="title"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="title"
            # ## Title

            # %% [markdown] lang="de" tags=["voiceover"] slide_id="title"
            # Sprechertext

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="title"
            # Voiceover text
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert result.findings == []

    def test_voiceover_wedged_between_de_en_warns(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_wedged.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="de" tags=["voiceover"]
            # Sprechertext

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover text
            """,
        )
        result = validate_file(p, checks=["pairing"])
        # The DE/EN slide pair has the DE voiceover wedged between, AND
        # the DE/EN voiceover pair has the EN slide wedged between.
        adjacency = [f for f in result.findings if "not adjacent" in f.message]
        assert len(adjacency) >= 1
        assert all(f.severity == "error" for f in adjacency)

    def test_shared_cell_between_de_en_is_ok(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_shared_between.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="title"
            # ## Titel

            # %% tags=["keep"]
            x = 1

            # %% [markdown] lang="en" tags=["slide"] slide_id="title"
            # ## Title
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert result.findings == []

    def test_count_mismatch_skips_ordering_check(self, tmp_path):
        # When DE/EN counts don't match, _check_pairing reports it and the
        # ordering check should not pile on with adjacency warnings for
        # the same category.
        p = _write_slide(
            tmp_path,
            "slides_count_mismatch.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel 1

            # %% [markdown] lang="de" tags=["voiceover"]
            # Sprechertext

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title 1

            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel 2
            """,
        )
        result = validate_file(p, checks=["pairing"])
        # The mismatch is the only error/warning we care about — no
        # adjacency warnings should be produced for the markdown category.
        adjacency = [f for f in result.findings if "not adjacent" in f.message]
        # The voiceover category has 1 DE and 0 EN, also a mismatch — skipped.
        # The markdown category has 3 DE and 1 EN — also skipped.
        # So no adjacency findings.
        assert adjacency == []

    def test_start_completed_cohesion_layout_passes(self, tmp_path):
        # Same-language start/completed pairs are permitted to stay
        # grouped together: [DE_start, DE_completed, EN_start, EN_completed].
        # The DE_completed between DE_start and its EN partner must NOT
        # be flagged as intervening.
        p = _write_slide(
            tmp_path,
            "slides_cohesion.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title

            # %% lang="de" tags=["start"]
            def begruessung(name, alter):
                return f"Hallo {name}"

            # %% lang="de" tags=["completed"]
            def begruessung(name: str, alter: int) -> str:
                return f"Hallo {name}"

            # %% lang="en" tags=["start"]
            def greeting(name, age):
                return f"Hello {name}"

            # %% lang="en" tags=["completed"]
            def greeting(name: str, age: int) -> str:
                return f"Hello {name}"
            """,
        )
        result = validate_file(p, checks=["pairing"])
        adjacency = [f for f in result.findings if "not adjacent" in f.message]
        assert adjacency == []

    def test_canonical_interleaved_start_completed_passes(self, tmp_path):
        # The canonical interleave [DE_start, EN_start, DE_completed, EN_completed]
        # also passes — start/completed cohesion is permitted, not required.
        p = _write_slide(
            tmp_path,
            "slides_interleaved.py",
            """\
            # %% lang="de" tags=["start"]
            def f():
                pass

            # %% lang="en" tags=["start"]
            def f():
                pass

            # %% lang="de" tags=["completed"]
            def f() -> None:
                pass

            # %% lang="en" tags=["completed"]
            def f() -> None:
                pass
            """,
        )
        result = validate_file(p, checks=["pairing"])
        adjacency = [f for f in result.findings if "not adjacent" in f.message]
        assert adjacency == []

    def test_start_completed_cohesion_with_voiceover(self, tmp_path):
        # Cohesion + voiceover: voiceover comes after the cohesion block.
        p = _write_slide(
            tmp_path,
            "slides_cohesion_vo.py",
            """\
            # %% lang="de" tags=["start"]
            def f():
                pass

            # %% lang="de" tags=["completed"]
            def f() -> None:
                pass

            # %% lang="en" tags=["start"]
            def f():
                pass

            # %% lang="en" tags=["completed"]
            def f() -> None:
                pass

            # %% [markdown] lang="de" tags=["voiceover"]
            # Sprechertext

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover text
            """,
        )
        result = validate_file(p, checks=["pairing"])
        adjacency = [f for f in result.findings if "not adjacent" in f.message]
        assert adjacency == []

    def test_start_completed_separated_by_call_does_not_collapse(self, tmp_path):
        # Cohesion only applies when start is *immediately* followed by
        # completed (same lang). Here a `keep` cell sits between DE_start
        # and DE_completed, so they are NOT a cohesion pair. The layout
        # below therefore fails adjacency (DE_start ↔ EN_start has the
        # DE_keep and DE_completed cells in between, which are lang-tagged).
        p = _write_slide(
            tmp_path,
            "slides_split.py",
            """\
            # %% lang="de" tags=["start"]
            def f():
                pass

            # %% lang="de" tags=["keep"]
            x = 1

            # %% lang="de" tags=["completed"]
            def f() -> None:
                pass

            # %% lang="en" tags=["start"]
            def f():
                pass

            # %% lang="en" tags=["keep"]
            x = 1

            # %% lang="en" tags=["completed"]
            def f() -> None:
                pass
            """,
        )
        result = validate_file(p, checks=["pairing"])
        adjacency = [f for f in result.findings if "not adjacent" in f.message]
        # No spurious collapse: at least one adjacency warning fires
        # because start/completed aren't immediate neighbors.
        assert len(adjacency) >= 1

    def test_code_pair_separated_by_voiceover_warns(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_code_split.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title

            # %% lang="de"
            print("hallo")

            # %% [markdown] lang="de" tags=["voiceover"]
            # Sprechertext

            # %% lang="en"
            print("hello")

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover text
            """,
        )
        result = validate_file(p, checks=["pairing"])
        warnings = [f for f in result.findings if "not adjacent" in f.message]
        assert len(warnings) >= 1


# ---------------------------------------------------------------------------
# Slide-id checks (Phase 3)
# ---------------------------------------------------------------------------


class TestCheckSlideIds:
    """Validate Phase 3's `_check_slide_ids` rules."""

    # -- Missing slide_id: an error as of CLM 1.8 (was a warning through 1.7) --

    def test_missing_slide_id_errors(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_missing.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title
            """,
        )
        result = validate_file(p, checks=["pairing"])
        missing = [f for f in result.findings if "missing slide_id" in f.message]
        assert len(missing) == 2
        assert all(f.severity == "error" for f in missing)
        # The fix hint must mention assign-ids, otherwise authors have no
        # fix path.
        assert all("assign-ids" in f.suggestion for f in missing)

    def test_present_slide_id_silent(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_have_id.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Title
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert result.findings == []

    # -- Duplicate slide_id: group-aware (paired DE/EN sharing an id is fine) --

    def test_duplicate_slide_id_errors_across_groups(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_dup.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Title

            # %% [markdown] lang="de" tags=["subslide"] slide_id="intro"
            # ## Mehr

            # %% [markdown] lang="en" tags=["subslide"] slide_id="intro"
            # ## More
            """,
        )
        result = validate_file(p, checks=["pairing"])
        dup = [f for f in result.findings if "duplicate" in f.message]
        # Each cell of the second group reports against the first group's line.
        assert len(dup) == 2
        assert all(f.severity == "error" for f in dup)
        assert all("'intro'" in f.message for f in dup)

    def test_paired_de_en_sharing_id_is_not_a_duplicate(self, tmp_path):
        # The EN-derived policy makes the DE and EN halves of one logical
        # slide share the same bare id. That is the canonical case, not a
        # duplicate.
        p = _write_slide(
            tmp_path,
            "slides_paired.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Title
            """,
        )
        result = validate_file(p, checks=["pairing"])
        dup = [f for f in result.findings if "duplicate" in f.message]
        assert dup == []

    def test_preserve_marker_collides_with_bare(self, tmp_path):
        # `!intro` and `intro` must collide — the `!` is purely a source
        # marker and is stripped before uniqueness checks.
        p = _write_slide(
            tmp_path,
            "slides_marker_collide.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="!intro"
            # ## Erstes

            # %% [markdown] lang="en" tags=["slide"] slide_id="!intro"
            # ## First

            # %% [markdown] lang="de" tags=["subslide"] slide_id="intro"
            # ## Zweites

            # %% [markdown] lang="en" tags=["subslide"] slide_id="intro"
            # ## Second
            """,
        )
        result = validate_file(p, checks=["pairing"])
        dup = [f for f in result.findings if "duplicate" in f.message]
        assert len(dup) == 2

    # -- Voiceover/notes adjacency --

    def test_narrative_inherits_preceding_slide_id(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_inherit.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Title

            # %% [markdown] lang="de" tags=["voiceover"] slide_id="intro"
            # Sprechertext

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="intro"
            # Voiceover text
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert result.findings == []

    def test_narrative_with_unique_own_id_passes(self, tmp_path):
        # The sync-v3 own-id convention (§12.1 / #520, `normalize
        # --stamp-ids`): a narrative id of the cell's own — unique in the
        # file, not the anchor's id — is legal. (Through CLM 1.18 this was
        # an unconditional adjacency error.)
        p = _write_slide(
            tmp_path,
            "slides_own_id.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="second"
            # ## Zweites

            # %% [markdown] lang="en" tags=["slide"] slide_id="second"
            # ## Second

            # %% [markdown] lang="de" tags=["voiceover"] slide_id="own-narration"
            # Sprechertext mit eigener Kennung

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="own-narration"
            # Voiceover with its own id
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert result.findings == []

    def test_narrative_with_stale_duplicate_slide_id_errors(self, tmp_path):
        # The stale copy-paste id this rule has always guarded against: an
        # id that equals some OTHER slide's id. Under the own-id convention
        # it is flagged as a duplicate rather than an adjacency mismatch.
        p = _write_slide(
            tmp_path,
            "slides_stale.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="first"
            # ## Erstes

            # %% [markdown] lang="en" tags=["slide"] slide_id="first"
            # ## First

            # %% [markdown] lang="de" tags=["slide"] slide_id="second"
            # ## Zweites

            # %% [markdown] lang="en" tags=["slide"] slide_id="second"
            # ## Second

            # %% [markdown] lang="de" tags=["voiceover"] slide_id="first"
            # Sprechertext (gehoert eigentlich zum ersten Slide)
            """,
        )
        result = validate_file(p, checks=["pairing"])
        mismatch = [f for f in result.findings if "duplicates an id" in f.message]
        assert len(mismatch) == 1
        assert mismatch[0].severity == "error"
        assert "'first'" in mismatch[0].message
        assert "'second'" in mismatch[0].message

    def test_narrative_twins_share_their_own_id_without_duplicate_error(self, tmp_path):
        # Adjacent DE/EN narrative twins share one own id, exactly like a
        # DE/EN slide pair — the duplicate check must treat them as one
        # logical narrative.
        p = _write_slide(
            tmp_path,
            "slides_own_twins.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Title

            # %% [markdown] lang="de" tags=["notes"] slide_id="speaker-notes-intro"
            # Notizen

            # %% [markdown] lang="en" tags=["notes"] slide_id="speaker-notes-intro"
            # Notes
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert result.findings == []

    def test_narrative_without_preceding_anchor_errors(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_no_anchor.py",
            """\
            # %% [markdown] lang="de" tags=["voiceover"] slide_id="ghost"
            # Sprechertext ohne Slide davor
            """,
        )
        result = validate_file(p, checks=["pairing"])
        no_anchor = [
            f for f in result.findings if "no preceding slide/subslide anchor" in f.message
        ]
        assert len(no_anchor) == 1
        assert no_anchor[0].severity == "error"

    def test_narrative_walks_back_through_code_and_shared_cells(self, tmp_path):
        # Intervening code/shared/j2 cells must NOT reset current_slide_id.
        p = _write_slide(
            tmp_path,
            "slides_walkback.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Title

            # %% tags=["keep"]
            x = 1

            # %% [markdown] lang="de" tags=["voiceover"] slide_id="intro"
            # Sprechertext
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert result.findings == []

    def test_preserve_marker_equivalent_for_narrative_adjacency(self, tmp_path):
        # `!intro` on the slide and bare `intro` on the voiceover are
        # equivalent for adjacency: bare forms match.
        p = _write_slide(
            tmp_path,
            "slides_marker_adjacency.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="!intro"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="!intro"
            # ## Title

            # %% [markdown] lang="de" tags=["voiceover"] slide_id="intro"
            # Sprechertext
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert result.findings == []

    # -- Title macro anchor --

    def test_title_macro_anchors_following_narrative(self, tmp_path):
        # The j2 header() macro line does not carry a slide_id itself but
        # anchors "title" for the following narrative cells.
        p = _write_slide(
            tmp_path,
            "slides_title.py",
            """\
            # j2 from 'macros.j2' import header
            # {{ header("Einfuehrung", "Introduction") }}

            # %% [markdown] lang="de" tags=["voiceover"] slide_id="title"
            # Sprechertext zum Titelslide

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="title"
            # Voiceover for the title slide
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert result.findings == []

    def test_title_macro_narrative_with_own_id_passes(self, tmp_path):
        # Under the own-id convention a unique narrative id below the title
        # macro is legal (formerly an adjacency error against "title").
        p = _write_slide(
            tmp_path,
            "slides_title_own_id.py",
            """\
            # j2 from 'macros.j2' import header
            # {{ header("Einfuehrung", "Introduction") }}

            # %% [markdown] lang="de" tags=["voiceover"] slide_id="something-else"
            # Sprechertext
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert result.findings == []

    # -- Pair-mismatch warning --

    def test_paired_de_en_with_mismatched_ids_warns(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_pair_mismatch.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="alpha"
            # ## Alpha (DE)

            # %% [markdown] lang="en" tags=["slide"] slide_id="beta"
            # ## Beta (EN)
            """,
        )
        result = validate_file(p, checks=["pairing"])
        mismatch = [f for f in result.findings if "mismatched slide_id" in f.message]
        assert len(mismatch) == 1
        assert mismatch[0].severity == "warning"
        assert "'alpha'" in mismatch[0].message
        assert "'beta'" in mismatch[0].message
        assert "assign-ids --force" in mismatch[0].suggestion

    def test_paired_de_en_preserve_marker_equivalent_for_pair_match(self, tmp_path):
        # `!intro` and `intro` are equivalent on the bare form, so a pair
        # with one side `!`-marked must not trigger the mismatch warning.
        p = _write_slide(
            tmp_path,
            "slides_pair_marker.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="!intro"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Title
            """,
        )
        result = validate_file(p, checks=["pairing"])
        mismatch = [f for f in result.findings if "mismatched slide_id" in f.message]
        assert mismatch == []

    def test_solo_slide_skips_pair_mismatch(self, tmp_path):
        # A single solo DE slide is not a pair; no pair-mismatch warning
        # regardless of its id.
        p = _write_slide(
            tmp_path,
            "slides_solo.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="solo"
            # ## Nur Deutsch
            """,
        )
        result = validate_file(p, checks=["pairing"])
        mismatch = [f for f in result.findings if "mismatched slide_id" in f.message]
        assert mismatch == []

    # -- Slug format --

    def test_invalid_slug_format_warns(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_bad_slug.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="Bad Slug!"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="Bad Slug!"
            # ## Title
            """,
        )
        result = validate_file(p, checks=["pairing"])
        bad_format = [f for f in result.findings if "not a valid kebab-case" in f.message]
        # One finding per cell.
        assert len(bad_format) == 2
        assert all(f.severity == "warning" for f in bad_format)

    def test_preserve_marker_is_valid_slug_form(self, tmp_path):
        # A leading `!` is permitted and does not count toward the
        # length cap.
        p = _write_slide(
            tmp_path,
            "slides_marker_valid.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="!intro"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="!intro"
            # ## Title
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert result.findings == []

    def test_slug_too_long_warns(self, tmp_path):
        too_long = "a" * 31  # MAX_SLUG_LENGTH = 30
        p = _write_slide(
            tmp_path,
            "slides_long.py",
            f"""\
            # %% [markdown] lang="de" tags=["slide"] slide_id="{too_long}"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="{too_long}"
            # ## Title
            """,
        )
        result = validate_file(p, checks=["pairing"])
        bad_format = [f for f in result.findings if "not a valid kebab-case" in f.message]
        assert len(bad_format) == 2

    # -- Quick-mode integration (PostToolUse hook surface) --

    def test_quick_mode_flags_missing_slide_id(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_quick_missing.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel
            """,
        )
        result = validate_quick(p)
        warnings = [f for f in result.findings if "missing slide_id" in f.message]
        assert len(warnings) == 1

    def test_quick_mode_flags_narrative_duplicate(self, tmp_path):
        # Quick mode reuses _check_slide_ids, so the own-id duplicate rule
        # fires there too: a narrative id equal to another slide's id is a
        # stale copy-paste, not an own id.
        p = _write_slide(
            tmp_path,
            "slides_quick_stale.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="real"
            # ## Titel

            # %% [markdown] lang="de" tags=["slide"] slide_id="other"
            # ## Anderes

            # %% [markdown] lang="de" tags=["voiceover"] slide_id="real"
            # Sprechertext
            """,
        )
        result = validate_quick(p)
        mismatch = [f for f in result.findings if "duplicates an id" in f.message]
        assert len(mismatch) == 1
        assert mismatch[0].severity == "error"

    # -- Code-cell slide and unusual structures --

    def test_code_slide_cell_participates_in_slide_id_checks(self, tmp_path):
        # `tags=["slide"]` on a code cell is unusual but valid; the
        # missing-id error should fire just like for markdown slides.
        p = _write_slide(
            tmp_path,
            "slides_code_slide.py",
            """\
            # %% lang="de" tags=["slide"]
            x = 1
            """,
        )
        result = validate_file(p, checks=["pairing"])
        warnings = [f for f in result.findings if "missing slide_id" in f.message]
        assert len(warnings) == 1


# ---------------------------------------------------------------------------
# Review material extraction
# ---------------------------------------------------------------------------


class TestCodeQualityExtraction:
    def test_detects_print_calls(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_print.py",
            """\
            # %% tags=["keep"]
            x = 42
            print(x)
            """,
        )
        result = validate_file(p, checks=["code_quality"])
        assert result.review_material is not None
        assert result.review_material.code_quality is not None
        assert len(result.review_material.code_quality["print_calls"]) == 1

    def test_detects_leading_comments(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_comment.py",
            """\
            # %% tags=["keep"]
            # Calculate the result
            result = 1 + 2
            """,
        )
        result = validate_file(p, checks=["code_quality"])
        assert result.review_material is not None
        assert result.review_material.code_quality is not None
        assert len(result.review_material.code_quality["leading_comments"]) == 1

    def test_empty_when_no_issues(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_clean.py",
            """\
            # %% tags=["keep"]
            x = 42
            """,
        )
        result = validate_file(p, checks=["code_quality"])
        assert result.review_material is not None
        assert result.review_material.code_quality == {}


class TestVoiceoverGapsExtraction:
    def test_detects_missing_voiceover(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_no_vo.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title

            # %% tags=["keep"]
            x = 1
            """,
        )
        result = validate_file(p, checks=["voiceover"])
        assert result.review_material is not None
        assert result.review_material.voiceover_gaps is not None
        assert len(result.review_material.voiceover_gaps) > 0

    def test_no_gaps_when_voiceover_present(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_with_vo.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="de" tags=["voiceover"]
            # Voiceover

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover
            """,
        )
        result = validate_file(p, checks=["voiceover"])
        assert result.review_material is not None
        gaps = result.review_material.voiceover_gaps or []
        assert gaps == []

    def test_no_gaps_with_bilingual_interleaved_voiceover(self, tmp_path):
        # Regression test: the canonical layout produced by ``normalize-slides``
        # puts both DE and EN content cells first, then both voiceover cells.
        # A linear scan mistakenly flags the DE slide as missing voiceover.
        p = _write_slide(
            tmp_path,
            "slides_bilingual_vo.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title

            # %% [markdown] lang="de" tags=["voiceover"]
            # Voiceover DE

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover EN
            """,
        )
        result = validate_file(p, checks=["voiceover"])
        assert result.review_material is not None
        gaps = result.review_material.voiceover_gaps or []
        assert gaps == []

    def test_no_gaps_with_multiple_bilingual_slides(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_multi_bilingual.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel 1

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title 1

            # %% [markdown] lang="de" tags=["voiceover"]
            # Voiceover DE 1

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover EN 1

            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel 2

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title 2

            # %% [markdown] lang="de" tags=["voiceover"]
            # Voiceover DE 2

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover EN 2
            """,
        )
        result = validate_file(p, checks=["voiceover"])
        assert result.review_material is not None
        gaps = result.review_material.voiceover_gaps or []
        assert gaps == []

    def test_detects_uncovered_de_when_only_en_voiceover(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_en_only_vo.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover EN
            """,
        )
        result = validate_file(p, checks=["voiceover"])
        assert result.review_material is not None
        gaps = result.review_material.voiceover_gaps or []
        assert len(gaps) == 1
        assert gaps[0]["lang"] == "de"

    def test_lang_less_voiceover_covers_both_languages(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_shared_vo.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title

            # %% [markdown] tags=["voiceover"]
            # Shared voiceover
            """,
        )
        result = validate_file(p, checks=["voiceover"])
        assert result.review_material is not None
        gaps = result.review_material.voiceover_gaps or []
        assert gaps == []

    def test_voiceover_does_not_carry_across_slide_boundary(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_boundary.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel 1

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title 1

            # %% [markdown] lang="de" tags=["voiceover"]
            # Voiceover DE 1

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover EN 1

            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel 2

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title 2
            """,
        )
        result = validate_file(p, checks=["voiceover"])
        assert result.review_material is not None
        gaps = result.review_material.voiceover_gaps or []
        assert len(gaps) == 2
        langs = {g["lang"] for g in gaps}
        assert langs == {"de", "en"}


class TestVoiceoverGapsWithSeparatedCompanion:
    def test_no_gaps_when_voiceover_in_subdir_companion(self, tmp_path):
        # Regression test for issue #360: a separated voiceover companion in
        # the voiceover/ subdirectory must count as coverage, not produce
        # false-positive gaps for every slide.
        p = _write_slide(
            tmp_path,
            "slides_vo_companion.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Title
            """,
        )
        companion_dir = tmp_path / "voiceover"
        companion_dir.mkdir()
        _write_slide(
            companion_dir,
            "voiceover_vo_companion.py",
            """\
            # %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"
            # Voiceover DE

            # %% [markdown] lang="en" tags=["voiceover"] for_slide="intro"
            # Voiceover EN
            """,
        )
        result = validate_file(p, checks=["voiceover"])
        assert result.review_material is not None
        gaps = result.review_material.voiceover_gaps or []
        assert gaps == []

    def test_notes_in_companion_do_not_count_as_voiceover(self, tmp_path):
        # Secondary issue #360: only the "voiceover" tag should count as
        # coverage, not the broader "notes" tag.
        p = _write_slide(
            tmp_path,
            "slides_notes_companion.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Title
            """,
        )
        companion_dir = tmp_path / "voiceover"
        companion_dir.mkdir()
        _write_slide(
            companion_dir,
            "voiceover_notes_companion.py",
            """\
            # %% [markdown] lang="de" tags=["notes"] for_slide="intro"
            # Notes DE

            # %% [markdown] lang="en" tags=["notes"] for_slide="intro"
            # Notes EN
            """,
        )
        result = validate_file(p, checks=["voiceover"])
        assert result.review_material is not None
        gaps = result.review_material.voiceover_gaps or []
        assert len(gaps) == 2
        assert {g["lang"] for g in gaps} == {"de", "en"}


class TestVoiceoverGapsInsideWorkshop:
    def test_workshop_internal_cells_are_suppressed(self, tmp_path):
        # Workshop heading has voiceover; subslides and code cells inside
        # the workshop have none. Expectation: zero gaps.
        p = _write_slide(
            tmp_path,
            "slides_workshop_silent.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "workshop"]
            # ## Workshop: Übung

            # %% [markdown] lang="en" tags=["subslide", "workshop"]
            # ## Workshop: Exercise

            # %% [markdown] lang="de" tags=["voiceover"]
            # Voiceover DE

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover EN

            # %% [markdown] lang="de" tags=["subslide"]
            # ## Aufgabe 1

            # %% [markdown] lang="en" tags=["subslide"]
            # ## Task 1

            # %% tags=["keep"]
            x = 1
            """,
        )
        result = validate_file(p, checks=["voiceover"])
        assert result.review_material is not None
        gaps = result.review_material.voiceover_gaps or []
        assert gaps == []

    def test_workshop_heading_without_voiceover_is_flagged(self, tmp_path):
        # Bilingual workshop heading with no voiceover at all. Both DE and
        # EN heading cells must be flagged; the trailing subslide and code
        # cell are inside the workshop range and stay suppressed.
        p = _write_slide(
            tmp_path,
            "slides_workshop_no_intro.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "workshop"]
            # ## Workshop: Übung

            # %% [markdown] lang="en" tags=["subslide", "workshop"]
            # ## Workshop: Exercise

            # %% [markdown] lang="de" tags=["subslide"]
            # ## Aufgabe 1

            # %% [markdown] lang="en" tags=["subslide"]
            # ## Task 1

            # %% tags=["keep"]
            x = 1
            """,
        )
        result = validate_file(p, checks=["voiceover"])
        assert result.review_material is not None
        gaps = result.review_material.voiceover_gaps or []
        assert len(gaps) == 2
        langs = {g["lang"] for g in gaps}
        assert langs == {"de", "en"}
        assert all("workshop" in g.get("heading", "").lower() for g in gaps)

    def test_workshop_heading_partial_voiceover_flags_missing_side(self, tmp_path):
        # DE heading has voiceover, EN doesn't. Only the EN heading is
        # flagged; the workshop body stays suppressed.
        p = _write_slide(
            tmp_path,
            "slides_workshop_partial_intro.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "workshop"]
            # ## Workshop: Übung

            # %% [markdown] lang="en" tags=["subslide", "workshop"]
            # ## Workshop: Exercise

            # %% [markdown] lang="de" tags=["voiceover"]
            # Voiceover DE

            # %% [markdown] lang="de" tags=["subslide"]
            # ## Aufgabe 1

            # %% [markdown] lang="en" tags=["subslide"]
            # ## Task 1
            """,
        )
        result = validate_file(p, checks=["voiceover"])
        assert result.review_material is not None
        gaps = result.review_material.voiceover_gaps or []
        assert len(gaps) == 1
        assert gaps[0]["lang"] == "en"

    def test_cells_after_end_workshop_still_require_voiceover(self, tmp_path):
        # The cell carrying ``end-workshop`` is outside the workshop. Its
        # missing voiceover must be reported. (And the workshop heading
        # has voiceover, so it stays silent.)
        p = _write_slide(
            tmp_path,
            "slides_workshop_then_normal.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "workshop"]
            # ## Workshop: Übung

            # %% [markdown] lang="en" tags=["subslide", "workshop"]
            # ## Workshop: Exercise

            # %% [markdown] lang="de" tags=["voiceover"]
            # Voiceover DE

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover EN

            # %% tags=["keep"]
            x = 1

            # %% [markdown] lang="de" tags=["subslide", "end-workshop"]
            # ## Nach dem Workshop

            # %% [markdown] lang="en" tags=["subslide", "end-workshop"]
            # ## After the workshop
            """,
        )
        result = validate_file(p, checks=["voiceover"])
        assert result.review_material is not None
        gaps = result.review_material.voiceover_gaps or []
        assert len(gaps) == 2
        langs = {g["lang"] for g in gaps}
        assert langs == {"de", "en"}

    def test_two_workshops_independent_heading_checks(self, tmp_path):
        # Two workshops in one file. First has heading voiceover; second
        # does not. Only the second workshop's heading cells flag.
        p = _write_slide(
            tmp_path,
            "slides_two_workshops.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "workshop"]
            # ## Workshop 1

            # %% [markdown] lang="en" tags=["subslide", "workshop"]
            # ## Workshop 1

            # %% [markdown] lang="de" tags=["voiceover"]
            # Voiceover DE 1

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover EN 1

            # %% tags=["keep"]
            x = 1

            # %% [markdown] lang="de" tags=["subslide", "end-workshop"]
            # ## Zwischendurch

            # %% [markdown] lang="en" tags=["subslide", "end-workshop"]
            # ## Intermission

            # %% [markdown] lang="de" tags=["voiceover"]
            # Voiceover DE Inter

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover EN Inter

            # %% [markdown] lang="de" tags=["subslide", "workshop"]
            # ## Workshop 2

            # %% [markdown] lang="en" tags=["subslide", "workshop"]
            # ## Workshop 2

            # %% tags=["keep"]
            y = 2
            """,
        )
        result = validate_file(p, checks=["voiceover"])
        assert result.review_material is not None
        gaps = result.review_material.voiceover_gaps or []
        # Two heading cells (DE + EN) for Workshop 2 only.
        assert len(gaps) == 2
        langs = {g["lang"] for g in gaps}
        assert langs == {"de", "en"}

    def test_workshop_extends_to_eof_when_no_end_tag(self, tmp_path):
        # Common case: no ``end-workshop`` tag, workshop runs to EOF.
        # The cell after the heading must be suppressed even though it
        # would normally need voiceover.
        p = _write_slide(
            tmp_path,
            "slides_workshop_eof.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Einführung

            # %% [markdown] lang="en" tags=["slide"]
            # ## Introduction

            # %% [markdown] lang="de" tags=["voiceover"]
            # Voiceover DE Intro

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover EN Intro

            # %% [markdown] lang="de" tags=["subslide", "workshop"]
            # ## Workshop: Übung

            # %% [markdown] lang="en" tags=["subslide", "workshop"]
            # ## Workshop: Exercise

            # %% [markdown] lang="de" tags=["voiceover"]
            # Voiceover DE Workshop

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover EN Workshop

            # %% tags=["keep"]
            x = 1

            # %% [markdown] lang="de" tags=["subslide"]
            # ## Aufgabe

            # %% [markdown] lang="en" tags=["subslide"]
            # ## Task
            """,
        )
        result = validate_file(p, checks=["voiceover"])
        assert result.review_material is not None
        gaps = result.review_material.voiceover_gaps or []
        assert gaps == []


class TestVoiceoverOptIn:
    """Issue #176: voiceover coverage is opt-in.

    Voiceover is optional per deck, so the coverage check must never run as
    part of a default / "all" / review bundle — only when the caller names it
    explicitly. The other review checks (code_quality, completeness) still run
    by default.
    """

    # A deck with obvious gaps: slides and a code cell, no voiceover anywhere.
    _GAPPY_DECK = """\
        # %% [markdown] lang="de" tags=["slide"]
        # ## Titel

        # %% [markdown] lang="en" tags=["slide"]
        # ## Title

        # %% tags=["keep"]
        x = 1
        """

    def test_voiceover_excluded_from_default_bundle_constant(self):
        # The name stays valid (so it can be requested), but is not a default.
        assert "voiceover" in ALL_CHECKS
        assert "voiceover" not in DEFAULT_CHECKS
        # The other review checks remain in the default bundle.
        assert {"code_quality", "completeness", "format", "pairing", "tags"} <= DEFAULT_CHECKS

    def test_default_checks_does_not_run_voiceover(self, tmp_path):
        p = _write_slide(tmp_path, "slides_no_vo.py", self._GAPPY_DECK)
        result = validate_file(p, checks=None)
        # Review material is still produced (code_quality / completeness ran)...
        assert result.review_material is not None
        # ...but voiceover coverage was not run, so no gaps are surfaced.
        assert result.review_material.voiceover_gaps is None

    def test_explicit_voiceover_still_runs(self, tmp_path):
        p = _write_slide(tmp_path, "slides_no_vo.py", self._GAPPY_DECK)
        result = validate_file(p, checks=["voiceover"])
        assert result.review_material is not None
        assert result.review_material.voiceover_gaps is not None
        assert len(result.review_material.voiceover_gaps) > 0

    def test_directory_default_does_not_run_voiceover(self, tmp_path):
        _write_slide(tmp_path, "slides_no_vo.py", self._GAPPY_DECK)
        result = validate_directory(tmp_path, checks=None)
        # combined review material exists (completeness/code_quality), but no
        # voiceover gaps are merged in under the default bundle.
        if result.review_material is not None:
            assert result.review_material.voiceover_gaps is None

    def test_directory_explicit_voiceover_runs(self, tmp_path):
        _write_slide(tmp_path, "slides_no_vo.py", self._GAPPY_DECK)
        result = validate_directory(tmp_path, checks=["voiceover"])
        assert result.review_material is not None
        assert result.review_material.voiceover_gaps
        assert len(result.review_material.voiceover_gaps) > 0


class TestVoiceoverCoverageMarker:
    """Issue #178: a `clm: voiceover-coverage` header marker re-enables the
    voiceover coverage check for THAT deck under the default bundle, so a
    fully-narrated deck is coverage-checked automatically while
    voiceover-less decks stay silent (#176)."""

    # A gappy deck that DECLARES it should be fully narrated.
    _MARKED_DECK = """\
        # clm: voiceover-coverage

        # %% [markdown] lang="de" tags=["slide"]
        # ## Titel

        # %% [markdown] lang="en" tags=["slide"]
        # ## Title

        # %% tags=["keep"]
        x = 1
        """

    _UNMARKED_DECK = """\
        # %% [markdown] lang="de" tags=["slide"]
        # ## Titel

        # %% [markdown] lang="en" tags=["slide"]
        # ## Title

        # %% tags=["keep"]
        x = 1
        """

    def test_marker_detection(self):
        assert has_voiceover_coverage_marker("# clm: voiceover-coverage\n")
        assert has_voiceover_coverage_marker("#  clm:  voiceover-coverage  \n")
        assert has_voiceover_coverage_marker("// clm: voiceover-coverage\n", "//")
        assert not has_voiceover_coverage_marker("# clm: something-else\n")
        assert not has_voiceover_coverage_marker("# voiceover-coverage\n")

    def test_marker_inside_a_cell_does_not_count(self):
        # The directive is a file-header declaration: a comment after the
        # first cell marker is cell content, not a directive.
        text = '# %% [markdown] lang="de" tags=["slide"]\n# clm: voiceover-coverage\n'
        assert not has_voiceover_coverage_marker(text)

    def test_marked_deck_gets_coverage_under_default_bundle(self, tmp_path):
        p = _write_slide(tmp_path, "slides_marked.py", self._MARKED_DECK)
        result = validate_file(p, checks=None)
        assert result.review_material is not None
        assert result.review_material.voiceover_gaps is not None
        assert len(result.review_material.voiceover_gaps) > 0

    def test_unmarked_deck_stays_silent(self, tmp_path):
        p = _write_slide(tmp_path, "slides_unmarked.py", self._UNMARKED_DECK)
        result = validate_file(p, checks=None)
        assert result.review_material is not None
        assert result.review_material.voiceover_gaps is None

    def test_explicit_checks_ignore_marker(self, tmp_path):
        # An explicit checks list is honored verbatim (issue spec).
        p = _write_slide(tmp_path, "slides_marked.py", self._MARKED_DECK)
        result = validate_file(p, checks=["format", "tags"])
        assert result.review_material is None

    def test_marker_opt_in_true_promotes_on_explicit_list(self, tmp_path):
        # The CLI default path: deterministic checks named explicitly, but
        # marker_opt_in=True because the user did not pass --checks.
        p = _write_slide(tmp_path, "slides_marked.py", self._MARKED_DECK)
        result = validate_file(p, checks=["format", "pairing", "tags"], marker_opt_in=True)
        assert result.review_material is not None
        assert result.review_material.voiceover_gaps

    def test_directory_mixes_marked_and_unmarked(self, tmp_path):
        _write_slide(tmp_path, "slides_marked.py", self._MARKED_DECK)
        _write_slide(tmp_path, "slides_unmarked.py", self._UNMARKED_DECK)
        result = validate_directory(tmp_path, checks=None)
        assert result.review_material is not None
        gaps = result.review_material.voiceover_gaps or []
        # Only the marked deck contributed coverage gaps.
        assert gaps
        assert all("slides_marked" in g["file"] for g in gaps)

    def test_fully_narrated_marked_deck_is_clean(self, tmp_path):
        deck = """\
            # clm: voiceover-coverage

            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="de" tags=["voiceover"]
            # - sprich hier
            """
        p = _write_slide(tmp_path, "slides_narrated.py", deck)
        result = validate_file(p, checks=None)
        gaps = result.review_material.voiceover_gaps if result.review_material is not None else None
        assert not gaps


class TestCompletenessExtraction:
    def test_extracts_concepts_and_workshops(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_concepts.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Methoden

            # %% [markdown] lang="en" tags=["slide"]
            # ## Methods

            # %% [markdown] lang="de" tags=["subslide"]
            # ## Workshop: Methoden üben

            # %% [markdown] lang="en" tags=["subslide"]
            # ## Workshop: Practice Methods
            """,
        )
        result = validate_file(p, checks=["completeness"])
        assert result.review_material is not None
        c = result.review_material.completeness
        assert c is not None
        assert "Methoden" in c["slide_concepts"] or "Methods" in c["slide_concepts"]
        assert len(c["workshop_exercises"]) > 0


# ---------------------------------------------------------------------------
# validate_quick
# ---------------------------------------------------------------------------


class TestValidateQuick:
    def test_clean_file(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_ok.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="title"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="title"
            # ## Title
            """,
        )
        result = validate_quick(p)
        assert result.findings == []
        assert result.review_material is None

    def test_catches_invalid_tag(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_bad.py",
            """\
            # %% tags=["invalid_tag"]
            x = 1
            """,
        )
        result = validate_quick(p)
        assert len(result.findings) == 1
        assert result.findings[0].category == "tags"

    def test_catches_unclosed_start(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_unclosed.py",
            """\
            # %% tags=["start"]
            # starter
            """,
        )
        result = validate_quick(p)
        errors = [f for f in result.findings if f.severity == "error"]
        assert len(errors) == 1
        assert "start" in errors[0].message

    def test_does_not_check_pairing(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_unpaired.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="only-german"
            # ## Only German
            """,
        )
        result = validate_quick(p)
        # Quick mode doesn't check pairing count/tag mismatches — these
        # would be noisy during in-progress edits where the EN counterpart
        # is not yet written. (Slide-id checks still run, but the fixture
        # carries a valid id so they stay silent.)
        pairing_findings = [f for f in result.findings if f.category == "pairing"]
        assert pairing_findings == []

    def test_checks_ordering(self, tmp_path):
        # Quick mode DOES check DE/EN adjacency — this catches the
        # "voiceover wedged between DE and EN slides" anti-pattern at
        # edit time. Counts are matched, so it's safe to flag.
        p = _write_slide(
            tmp_path,
            "slides_wedged.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="de" tags=["voiceover"]
            # Sprechertext

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title

            # %% [markdown] lang="en" tags=["voiceover"]
            # Voiceover text
            """,
        )
        result = validate_quick(p)
        adjacency = [f for f in result.findings if "not adjacent" in f.message]
        assert len(adjacency) >= 1

    def test_ordering_skipped_on_count_mismatch(self, tmp_path):
        # Confirm ordering check is silent during partial edits, where
        # the user has added a DE cell but not yet the EN counterpart.
        p = _write_slide(
            tmp_path,
            "slides_partial_edit.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel 1

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title 1

            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel 2 (EN not yet written)
            """,
        )
        result = validate_quick(p)
        adjacency = [f for f in result.findings if "not adjacent" in f.message]
        assert adjacency == []


# ---------------------------------------------------------------------------
# validate_directory
# ---------------------------------------------------------------------------


class TestValidateDirectory:
    def test_validates_all_slide_files(self, tmp_path):
        topic_dir = tmp_path / "topic_010_intro"
        topic_dir.mkdir()
        (topic_dir / "slides_intro.py").write_text(
            '# %% [markdown] lang="de" tags=["slide"] slide_id="title"\n#\n# ## Titel\n\n'
            '# %% [markdown] lang="en" tags=["slide"] slide_id="title"\n#\n# ## Title\n',
            encoding="utf-8",
        )
        (topic_dir / "slides_extra.py").write_text(
            '# %% [markdown] lang="de" tags=["slide"] slide_id="more"\n#\n# ## Mehr\n\n'
            '# %% [markdown] lang="en" tags=["slide"] slide_id="more"\n#\n# ## More\n',
            encoding="utf-8",
        )
        # Non-slide file should be ignored
        (topic_dir / "helper.py").write_text("x = 1\n", encoding="utf-8")

        result = validate_directory(topic_dir)
        assert result.files_checked == 2
        assert result.findings == []

    def test_aggregates_findings(self, tmp_path):
        topic_dir = tmp_path / "topic_010_intro"
        topic_dir.mkdir()
        (topic_dir / "slides_bad.py").write_text(
            '# %% tags=["bogus"]\nx = 1\n',
            encoding="utf-8",
        )

        result = validate_directory(topic_dir, checks=["tags"])
        assert result.files_checked == 1
        assert len(result.findings) == 1

    def test_recurses_into_module_directory(self, tmp_path):
        # A module directory has no direct slide files but contains
        # topic subdirectories with slide files. validate_directory must
        # walk into them rather than silently returning zero files.
        module_dir = tmp_path / "module_100_basics"
        topic_dir = module_dir / "topic_010_intro"
        topic_dir.mkdir(parents=True)
        (topic_dir / "slides_intro.py").write_text(
            '# %% tags=["bogus"]\nx = 1\n',
            encoding="utf-8",
        )

        result = validate_directory(module_dir, checks=["tags"])
        assert result.files_checked == 1
        assert len(result.findings) == 1

    def test_recurses_from_slides_root(self, tmp_path):
        # The full slides/ root: nested two levels deep below the input.
        slides_root = tmp_path / "slides"
        topic_a = slides_root / "module_100" / "topic_010"
        topic_b = slides_root / "module_200" / "topic_020"
        topic_a.mkdir(parents=True)
        topic_b.mkdir(parents=True)
        (topic_a / "slides_a.py").write_text("x = 1\n", encoding="utf-8")
        (topic_b / "slides_b.py").write_text("y = 2\n", encoding="utf-8")

        result = validate_directory(slides_root, checks=["format"])
        assert result.files_checked == 2


# ---------------------------------------------------------------------------
# validate_course
# ---------------------------------------------------------------------------


class TestValidateCourse:
    def _make_course(self, tmp_path):
        """Set up a minimal course tree."""
        slides = tmp_path / "slides"
        m1 = slides / "module_100_basics"
        t1 = m1 / "topic_010_intro"
        t1.mkdir(parents=True)
        (t1 / "slides_intro.py").write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n#\n# ## Titel\n\n'
            '# %% [markdown] lang="en" tags=["slide"]\n#\n# ## Title\n',
            encoding="utf-8",
        )

        specs = tmp_path / "course-specs"
        specs.mkdir()
        spec_xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Test</de><en>Test</en></name>
    <prog-lang>python</prog-lang>
    <description><de></de><en></en></description>
    <certificate><de></de><en></en></certificate>
    <sections><section>
        <name><de>S</de><en>S</en></name>
        <topics><topic>intro</topic></topics>
    </section></sections>
</course>
"""
        spec_path = specs / "test.xml"
        spec_path.write_text(spec_xml, encoding="utf-8")
        return spec_path, slides

    def test_validates_course_slides(self, tmp_path):
        spec_path, slides_dir = self._make_course(tmp_path)
        result = validate_course(spec_path, slides_dir, checks=["format", "tags"])
        assert result.files_checked == 1
        assert result.findings == []


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------


class TestValidationResult:
    def test_summary_no_issues(self):
        r = ValidationResult(files_checked=3)
        assert "3 files checked" in r.summary
        assert "no issues" in r.summary

    def test_summary_with_findings(self):
        r = ValidationResult(
            files_checked=1,
            findings=[
                Finding("error", "tags", "f.py", 1, "bad tag"),
                Finding("warning", "pairing", "f.py", 2, "mismatch"),
            ],
        )
        assert "1 error" in r.summary
        assert "1 warning" in r.summary

    def test_summary_with_review(self):
        r = ValidationResult(
            files_checked=1,
            review_material=ReviewMaterial(code_quality={"print_calls": []}),
        )
        assert "1 category for review" in r.summary

    def test_summary_singular_file(self):
        r = ValidationResult(files_checked=1)
        assert "1 file checked" in r.summary


# ---------------------------------------------------------------------------
# Selective checks
# ---------------------------------------------------------------------------


class TestSelectiveChecks:
    def test_only_format_checks(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_bad.py",
            """\
            # %% tags=["bogus"]
            x = 1

            # %% [markdown] lang="de" tags=["slide"]
            # ## Only German
            """,
        )
        result = validate_file(p, checks=["format"])
        # Should not report tag or pairing issues
        assert all(f.category == "format" for f in result.findings)
        assert result.review_material is None

    def test_only_review_checks(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_print.py",
            """\
            # %% tags=["bogus"]
            print(42)
            """,
        )
        result = validate_file(p, checks=["code_quality"])
        # No deterministic findings
        assert result.findings == []
        assert result.review_material is not None

    def test_default_runs_all(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_all.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title

            # %% tags=["keep"]
            print(42)
            """,
        )
        result = validate_file(p)
        assert result.review_material is not None


def test_companion_location_ambiguity_is_flagged(tmp_path):
    """A companion present in BOTH the voiceover/ subdir and as a sibling is
    ambiguous — the build silently prefers the relocated copy."""
    _write_slide(
        tmp_path,
        "slides_intro.py",
        """\
        # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
        # ## Einführung

        # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
        # ## Introduction
        """,
    )
    companion = '# %% [markdown] tags=["voiceover"] for_slide="intro"\n#\n# hi\n'
    _write_slide(tmp_path, "voiceover_intro.py", companion)  # sibling
    (tmp_path / "voiceover").mkdir()
    _write_slide(tmp_path / "voiceover", "voiceover_intro.py", companion)  # relocated

    result = validate_file(tmp_path / "slides_intro.py", checks=["pairing"])
    ambiguity = [
        f for f in result.findings if f.severity == "warning" and "two locations" in f.message
    ]
    assert len(ambiguity) == 1
    assert ambiguity[0].category == "pairing"


# ---------------------------------------------------------------------------
# Cell spacing checks (blank line between cells; markdown leading blank comment)
# ---------------------------------------------------------------------------

_J2_HEADER = "# j2 from 'macros.j2' import header\n# {{ header_de(\"Titel\") }}\n"


def _write_raw(tmp_path: Path, content: str, name: str = "slides_x.de.py") -> Path:
    """Write byte-exact content (no dedent — these tests are whitespace-sensitive)."""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8", newline="\n")
    return p


def _separation(result: ValidationResult) -> list[Finding]:
    return [f for f in result.findings if "separated from the previous" in f.message]


def _lead(result: ValidationResult) -> list[Finding]:
    return [f for f in result.findings if "blank comment" in f.message]


class TestCellSeparationCheck:
    def test_missing_blank_between_cells_warns(self, tmp_path):
        text = '# %% [markdown] lang="de"\n#\n# ## A\n# %% [markdown] lang="de"\n#\n# ## B\n'
        seps = _separation(validate_file(_write_raw(tmp_path, text), checks=["format"]))
        assert len(seps) == 1
        assert seps[0].severity == "warning"
        assert seps[0].category == "format"

    def test_blank_present_no_warning(self, tmp_path):
        text = '# %% [markdown] lang="de"\n#\n# ## A\n\n# %% [markdown] lang="de"\n#\n# ## B\n'
        assert _separation(validate_file(_write_raw(tmp_path, text), checks=["format"])) == []

    def test_j2_header_block_exempt(self, tmp_path):
        # The `# j2 import` -> `# {{ header }}` adjacency carries no blank by design.
        text = _J2_HEADER + '\n# %% [markdown] lang="de"\n#\n# ## A\n'
        assert _separation(validate_file(_write_raw(tmp_path, text), checks=["format"])) == []

    def test_code_cell_separation_enforced(self, tmp_path):
        text = '# %% [markdown] lang="de"\n#\n# ## A\n# %%\nx = 1\n'
        assert len(_separation(validate_file(_write_raw(tmp_path, text), checks=["format"]))) == 1

    def test_content_cell_after_macro_without_blank_warns(self, tmp_path):
        # The header macro (j2) is exempt as a *target*, but a content cell right
        # after it with no blank IS flagged (the macro must end with a blank).
        text = _J2_HEADER + '# %% [markdown] lang="de"\n#\n# ## A\n'
        assert len(_separation(validate_file(_write_raw(tmp_path, text), checks=["format"]))) == 1


class TestMarkdownBlankLeadCheck:
    def test_missing_lead_warns(self, tmp_path):
        text = '# %% [markdown] lang="de" tags=["slide"] slide_id="a"\n# - Bullet\n'
        leads = _lead(validate_file(_write_raw(tmp_path, text), checks=["format"]))
        assert len(leads) == 1
        assert leads[0].severity == "warning"

    def test_lead_present_no_warning(self, tmp_path):
        text = '# %% [markdown] lang="de"\n#\n# - Bullet\n'
        assert _lead(validate_file(_write_raw(tmp_path, text), checks=["format"])) == []

    def test_code_cell_not_subject(self, tmp_path):
        text = '# %% lang="de"\nx = 1\n'
        assert _lead(validate_file(_write_raw(tmp_path, text), checks=["format"])) == []

    def test_j2_macro_not_subject(self, tmp_path):
        assert _lead(validate_file(_write_raw(tmp_path, _J2_HEADER), checks=["format"])) == []

    def test_runs_under_default_checks(self, tmp_path):
        # No explicit --checks: the format bundle (and thus these) run by default.
        text = '# %% [markdown] lang="de" tags=["slide"] slide_id="a"\n# - Bullet\n'
        assert len(_lead(validate_file(_write_raw(tmp_path, text)))) == 1


# ---------------------------------------------------------------------------
# Companion for_slide resolution (a renamed/moved slide drops its narration)
# ---------------------------------------------------------------------------


class TestCompanionForSlideResolves:
    """`clm slides validate` catches a companion for_slide that matches no slide_id
    in its deck — the build would silently drop that narration."""

    def _deck_and_companion(self, tmp_path: Path, for_slide: str, *, header: str = "") -> Path:
        deck = tmp_path / "slides_x.de.py"
        deck.write_text(
            header + '# %% [markdown] lang="de" tags=["slide"] slide_id="s1"\n# ## Slide One\n',
            encoding="utf-8",
        )
        (tmp_path / "voiceover_x.de.py").write_text(
            f'# %% [markdown] lang="de" tags=["notes"] slide_id="{for_slide}" '
            f'for_slide="{for_slide}" vo_anchor="id:{for_slide}#0"\n#\n# - Narration.\n',
            encoding="utf-8",
        )
        return deck

    def _orphans(self, result) -> list:
        return [f for f in result.findings if "matches no slide_id" in f.message]

    def test_aligned_companion_is_clean(self, tmp_path):
        deck = self._deck_and_companion(tmp_path, "s1")
        assert self._orphans(validate_file(deck, checks=["pairing"])) == []

    def test_orphan_for_slide_is_error(self, tmp_path):
        deck = self._deck_and_companion(tmp_path, "configuring-mcp-in-vs-code")
        orphans = self._orphans(validate_file(deck, checks=["pairing"]))
        assert len(orphans) == 1
        assert orphans[0].severity == "error"
        assert orphans[0].category == "pairing"
        assert "configuring-mcp-in-vs-code" in orphans[0].message
        # The companion file is named as the offending file.
        assert orphans[0].file.endswith("voiceover_x.de.py")

    def test_voiceover_less_deck_is_silent(self, tmp_path):
        deck = tmp_path / "slides_x.de.py"
        deck.write_text(
            '# %% [markdown] lang="de" tags=["slide"] slide_id="s1"\n# ## Slide One\n',
            encoding="utf-8",
        )
        assert self._orphans(validate_file(deck, checks=["pairing"])) == []

    def test_title_for_slide_is_not_a_false_positive(self, tmp_path):
        # for_slide="title" resolves to the synthetic title slide (the j2 header
        # macro), so it must not be reported — the check reuses the build matcher.
        header = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"X\") }}\n\n"
        deck = self._deck_and_companion(tmp_path, "title", header=header)
        assert self._orphans(validate_file(deck, checks=["pairing"])) == []

    def test_runs_under_default_checks(self, tmp_path):
        # No explicit --checks: pairing is in the default bundle, so an orphaned
        # companion fails a bare `clm slides validate`.
        deck = self._deck_and_companion(tmp_path, "gone-slide")
        assert len(self._orphans(validate_file(deck))) == 1
