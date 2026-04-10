"""Tests for clm.slides.validator."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from clm.slides.validator import (
    Finding,
    ReviewMaterial,
    ValidationResult,
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
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
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
            # ## Bad
            """,
        )
        result = validate_file(p, checks=["format"])
        assert len(result.findings) == 1
        assert result.findings[0].category == "format"
        assert "Malformed lang" in result.findings[0].message

    def test_j2_cells_skipped(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_j2.py",
            """\
            # j2 from 'macros.j2' import header
            # {{ header("T", "T") }}

            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title
            """,
        )
        result = validate_file(p, checks=["format"])
        assert result.findings == []


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

    def test_start_completed_inside_workshop(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_ws.py",
            """\
            # %% [markdown] lang="de" tags=["subslide", "workshop"]
            # ## Workshop: Übung

            # %% [markdown] lang="en" tags=["subslide", "workshop"]
            # ## Workshop: Exercise

            # %% tags=["start"]
            # starter

            # %% tags=["completed"]
            result = 42
            """,
        )
        result = validate_file(p, checks=["tags"])
        warnings = [f for f in result.findings if f.severity == "warning"]
        assert any("workshop" in w.message for w in warnings)

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


# ---------------------------------------------------------------------------
# Pairing checks
# ---------------------------------------------------------------------------


class TestCheckPairing:
    def test_balanced_pairing(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_paired.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
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
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title

            # %% [markdown] lang="de" tags=["subslide"]
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
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["subslide"]
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
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="de" tags=["voiceover"]
            # Voiceover text

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title

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
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title

            # %% tags=["keep"]
            x = 1
            """,
        )
        result = validate_file(p, checks=["pairing"])
        assert result.findings == []


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
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
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
            # %% [markdown] lang="de" tags=["slide"]
            # ## Only German
            """,
        )
        result = validate_quick(p)
        # Quick mode doesn't check pairing
        pairing_findings = [f for f in result.findings if f.category == "pairing"]
        assert pairing_findings == []


# ---------------------------------------------------------------------------
# validate_directory
# ---------------------------------------------------------------------------


class TestValidateDirectory:
    def test_validates_all_slide_files(self, tmp_path):
        topic_dir = tmp_path / "topic_010_intro"
        topic_dir.mkdir()
        (topic_dir / "slides_intro.py").write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Titel\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Title\n',
            encoding="utf-8",
        )
        (topic_dir / "slides_extra.py").write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Mehr\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## More\n',
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
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Titel\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Title\n',
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
