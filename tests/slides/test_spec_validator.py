"""Tests for clm.slides.spec_validator."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from clm.slides.spec_validator import SpecFinding, SpecValidationResult, validate_spec


def _write_spec(tmp_path: Path, sections_xml: str, dir_groups_xml: str = "") -> Path:
    """Write a minimal course spec XML and return its path."""
    spec_file = tmp_path / "course-specs" / "test.xml"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(
        dedent(f"""\
        <course>
          <name><de>Test</de><en>Test</en></name>
          <prog-lang>python</prog-lang>
          <description><de></de><en></en></description>
          <certificate><de></de><en></en></certificate>
          {sections_xml}
          {dir_groups_xml}
        </course>
        """),
        encoding="utf-8",
    )
    return spec_file


def _make_topic(tmp_path: Path, module: str, topic: str) -> Path:
    """Create a topic directory with a slide file inside slides/."""
    slides_dir = tmp_path / "slides"
    topic_dir = slides_dir / module / topic
    topic_dir.mkdir(parents=True, exist_ok=True)
    (topic_dir / "slides_intro.py").write_text("# %% [markdown]\n# Hello\n")
    return topic_dir


class TestValidateSpecClean:
    """Spec with no issues."""

    def test_clean_spec(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        _make_topic(tmp_path, "module_100_basics", "topic_020_variables")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>Grundlagen</de><en>Basics</en></name>
              <topics>
                <topic>intro</topic>
                <topic>variables</topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert result.topics_total == 2
        assert result.findings == []

    def test_result_properties(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )
        result = validate_spec(spec_file, tmp_path / "slides")
        assert result.errors == []
        assert result.warnings == []


class TestUnresolvedTopic:
    """Topic ID not found on the filesystem."""

    def test_unresolved_topic_error(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro</topic>
                <topic>nonexistent</topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert len(result.errors) == 1
        f = result.errors[0]
        assert f.type == "unresolved_topic"
        assert f.topic_id == "nonexistent"
        assert f.severity == "error"

    def test_near_match_suggestion(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_linear_regression")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>linar_regression</topic></topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert len(result.errors) == 1
        assert "linear_regression" in result.errors[0].suggestion

    def test_no_suggestion_for_distant_name(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>zzz_completely_different</topic></topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert len(result.errors) == 1
        assert result.errors[0].suggestion == ""


class TestAmbiguousTopic:
    """Same topic ID in multiple modules."""

    def test_ambiguous_topic_error(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_decorators")
        _make_topic(tmp_path, "module_200_advanced", "topic_010_decorators")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>decorators</topic></topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert len(result.errors) == 1
        f = result.errors[0]
        assert f.type == "ambiguous_topic"
        assert f.topic_id == "decorators"
        assert len(f.matches) == 2


class TestDuplicateTopic:
    """Same topic referenced in multiple sections."""

    def test_duplicate_topic_warning(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections>
              <section>
                <name><de>S1</de><en>Section 1</en></name>
                <topics><topic>intro</topic></topics>
              </section>
              <section>
                <name><de>S2</de><en>Section 2</en></name>
                <topics><topic>intro</topic></topics>
              </section>
            </sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert len(result.warnings) == 1
        f = result.warnings[0]
        assert f.type == "duplicate_topic"
        assert f.topic_id == "intro"
        assert f.sections == ["Section 1", "Section 2"]


class TestEmptySection:
    """Section with no topics."""

    def test_empty_section_warning(self, tmp_path):
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>Leer</de><en>Empty</en></name>
              <topics></topics>
            </section></sections>""",
        )
        (tmp_path / "slides").mkdir(parents=True, exist_ok=True)

        result = validate_spec(spec_file, tmp_path / "slides")

        assert len(result.warnings) == 1
        assert result.warnings[0].type == "empty_section"


class TestMissingDirGroup:
    """Dir-group path does not exist."""

    def test_missing_dir_group_warning(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
            dir_groups_xml="""\
            <dir-groups>
              <dir-group>
                <name><de>Extras</de><en>Extras</en></name>
                <path>div/nonexistent</path>
              </dir-group>
            </dir-groups>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert len(result.warnings) == 1
        f = result.warnings[0]
        assert f.type == "missing_dir_group"
        assert "nonexistent" in f.message

    def test_existing_dir_group_ok(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        (tmp_path / "div" / "extras").mkdir(parents=True)

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
            dir_groups_xml="""\
            <dir-groups>
              <dir-group>
                <name><de>Extras</de><en>Extras</en></name>
                <path>div/extras</path>
              </dir-group>
            </dir-groups>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")
        assert result.findings == []


class TestCombinedFindings:
    """Multiple issues in a single spec."""

    def test_multiple_issues(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        _make_topic(tmp_path, "module_100_basics", "topic_020_lists")
        _make_topic(tmp_path, "module_200_advanced", "topic_010_lists")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections>
              <section>
                <name><de>S1</de><en>Section 1</en></name>
                <topics>
                  <topic>intro</topic>
                  <topic>lists</topic>
                  <topic>nonexistent</topic>
                </topics>
              </section>
              <section>
                <name><de>S2</de><en>Section 2</en></name>
                <topics><topic>intro</topic></topics>
              </section>
            </sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        types = {f.type for f in result.findings}
        assert "ambiguous_topic" in types
        assert "unresolved_topic" in types
        assert "duplicate_topic" in types
        assert result.topics_total == 4


class TestValidateSpecIncludeDisabled:
    """Tests for the ``include_disabled`` keyword argument on ``validate_spec``."""

    def test_default_drops_disabled_sections(self, tmp_path):
        """By default, disabled sections are invisible to the validator."""
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections>
              <section>
                <name><de>Aktiv</de><en>Active</en></name>
                <topics><topic>intro</topic></topics>
              </section>
              <section enabled="false">
                <name><de>Aus</de><en>Off</en></name>
                <topics><topic>not_yet_implemented</topic></topics>
              </section>
            </sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert result.topics_total == 1
        assert result.findings == []

    def test_include_disabled_reports_findings_with_suffix(self, tmp_path):
        """Disabled findings are reported with a ``(disabled)`` suffix."""
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections>
              <section>
                <name><de>Aktiv</de><en>Active</en></name>
                <topics><topic>intro</topic></topics>
              </section>
              <section enabled="false">
                <name><de>Aus</de><en>Off</en></name>
                <topics><topic>not_yet_implemented</topic></topics>
              </section>
            </sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides", include_disabled=True)

        assert result.topics_total == 2
        unresolved = [f for f in result.findings if f.type == "unresolved_topic"]
        assert len(unresolved) == 1
        assert unresolved[0].topic_id == "not_yet_implemented"
        assert unresolved[0].message.endswith("(disabled)")

    def test_include_disabled_preserves_enabled_message_format(self, tmp_path):
        """Findings from enabled sections are **not** suffixed."""
        (tmp_path / "slides").mkdir(parents=True)
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections>
              <section>
                <name><de>Aktiv</de><en>Active</en></name>
                <topics><topic>missing_enabled</topic></topics>
              </section>
              <section enabled="false">
                <name><de>Aus</de><en>Off</en></name>
                <topics><topic>missing_disabled</topic></topics>
              </section>
            </sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides", include_disabled=True)

        by_topic = {f.topic_id: f for f in result.findings if f.type == "unresolved_topic"}
        assert "(disabled)" not in by_topic["missing_enabled"].message
        assert by_topic["missing_disabled"].message.endswith("(disabled)")

    def test_empty_disabled_section_warning_is_suffixed(self, tmp_path):
        """Empty-section warnings from disabled sections are also suffixed."""
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections>
              <section>
                <name><de>Aktiv</de><en>Active</en></name>
                <topics><topic>intro</topic></topics>
              </section>
              <section enabled="false">
                <name><de>Leer</de><en>Empty Section</en></name>
              </section>
            </sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides", include_disabled=True)

        empty_findings = [f for f in result.findings if f.type == "empty_section"]
        assert len(empty_findings) == 1
        assert empty_findings[0].message.endswith("(disabled)")


class TestModuleBindingValidation:
    """Validation behaviour for the optional ``module=`` attribute on
    ``<section>`` and ``<topic>``."""

    def test_module_bound_section_resolves_correctly(self, tmp_path):
        """Section with ``module=`` resolves to the named module's copy
        even when the topic ID exists in another module."""
        _make_topic(tmp_path, "module_100_live", "topic_010_intro")
        _make_topic(tmp_path, "module_545_frozen", "topic_010_intro")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections>
              <section module="module_545_frozen">
                <name><de>Frozen</de><en>Frozen</en></name>
                <topics><topic>intro</topic></topics>
              </section>
            </sections>""",
        )
        result = validate_spec(spec_file, tmp_path / "slides")
        # No ambiguity finding because the module binding disambiguates.
        assert [f for f in result.findings if f.type == "ambiguous_topic"] == []
        assert [f for f in result.findings if f.type == "unresolved_topic"] == []

    def test_module_bound_two_sections_no_duplicate_warning(self, tmp_path):
        """Two sections binding the same topic ID to different modules
        should NOT produce a duplicate-reference warning."""
        _make_topic(tmp_path, "module_100_live", "topic_010_intro")
        _make_topic(tmp_path, "module_545_frozen", "topic_010_intro")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections>
              <section module="module_545_frozen">
                <name><de>Frozen</de><en>Frozen</en></name>
                <topics><topic>intro</topic></topics>
              </section>
              <section enabled="false">
                <name><de>Live</de><en>Live</en></name>
                <topics><topic>intro</topic></topics>
              </section>
            </sections>""",
        )
        result = validate_spec(spec_file, tmp_path / "slides", include_disabled=True)
        assert [f for f in result.findings if f.type == "duplicate_topic"] == []

    def test_unknown_section_module_error(self, tmp_path):
        """Section ``module=`` referencing a non-existent directory errors out."""
        _make_topic(tmp_path, "module_100_live", "topic_010_intro")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections>
              <section module="module_999_nope">
                <name><de>X</de><en>X</en></name>
                <topics><topic>intro</topic></topics>
              </section>
            </sections>""",
        )
        result = validate_spec(spec_file, tmp_path / "slides")
        unknown = [f for f in result.findings if f.type == "unknown_module"]
        assert len(unknown) == 1
        assert "module_999_nope" in unknown[0].message

    def test_topic_module_override_resolves(self, tmp_path):
        """Per-topic ``module=`` overrides the section default."""
        _make_topic(tmp_path, "module_100_live", "topic_010_intro")
        _make_topic(tmp_path, "module_545_frozen", "topic_010_intro")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections>
              <section module="module_545_frozen">
                <name><de>Mixed</de><en>Mixed</en></name>
                <topics>
                  <topic>intro</topic>
                  <topic module="module_100_live">intro</topic>
                </topics>
              </section>
            </sections>""",
        )
        result = validate_spec(spec_file, tmp_path / "slides")
        # No ambiguity, no unknown_module, no duplicate (different modules
        # → distinct bindings).
        assert [f for f in result.findings if f.severity == "error"] == []
        assert [f for f in result.findings if f.type == "duplicate_topic"] == []

    def test_module_bound_topic_not_in_module(self, tmp_path):
        """``module=`` topic that doesn't exist in the named module errors."""
        _make_topic(tmp_path, "module_100_live", "topic_010_intro")
        # Create the frozen module, but with a different topic
        _make_topic(tmp_path, "module_545_frozen", "topic_020_variables")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections>
              <section module="module_545_frozen">
                <name><de>X</de><en>X</en></name>
                <topics><topic>intro</topic></topics>
              </section>
            </sections>""",
        )
        result = validate_spec(spec_file, tmp_path / "slides")
        unresolved = [f for f in result.findings if f.type == "unresolved_topic"]
        assert len(unresolved) == 1
        assert "module_545_frozen" in unresolved[0].message


def _write_include_source_dir(tmp_path: Path, rel: str) -> Path:
    """Create a non-trivial include source directory with two files."""
    src = tmp_path / rel
    src.mkdir(parents=True, exist_ok=True)
    (src / "main.py").write_text("# main\n")
    (src / "helper.py").write_text("# helper\n")
    return src


class TestIncludeSourceMissing:
    """Missing include source paths raise an error unless optional."""

    def test_missing_source_required_errors(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro<include source="examples/missing/pkg" as="pkg"/></topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        errors = [f for f in result.errors if f.type == "include_source_missing"]
        assert len(errors) == 1
        assert errors[0].topic_id == "intro"
        assert "examples/missing/pkg" in errors[0].message
        assert errors[0].suggestion

    def test_missing_source_optional_silent(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro<include source="examples/missing/pkg" as="pkg" optional="true"/></topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert [f for f in result.findings if f.type == "include_source_missing"] == []

    def test_existing_source_no_error(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        _write_include_source_dir(tmp_path, "examples/pkg")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro<include source="examples/pkg" as="pkg"/></topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert [f for f in result.findings if f.type == "include_source_missing"] == []

    def test_section_default_propagates_missing_per_topic(self, tmp_path):
        """A missing section-level include errors once per inheriting topic."""
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        _make_topic(tmp_path, "module_100_basics", "topic_020_lists")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <include source="examples/missing/pkg" as="pkg"/>
              <topics>
                <topic>intro</topic>
                <topic>lists</topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        errors = [f for f in result.errors if f.type == "include_source_missing"]
        assert {e.topic_id for e in errors} == {"intro", "lists"}


class TestIncludeShadowed:
    """A real file/directory at topic.path/as_path shadows the include."""

    def test_shadowed_emits_warning(self, tmp_path):
        topic_dir = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        # Real local pkg dir at the include's target location
        (topic_dir / "pkg").mkdir()
        (topic_dir / "pkg" / "main.py").write_text("# local override\n")
        _write_include_source_dir(tmp_path, "examples/pkg")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro<include source="examples/pkg" as="pkg"/></topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        warnings = [f for f in result.warnings if f.type == "include_shadowed"]
        assert len(warnings) == 1
        assert warnings[0].topic_id == "intro"
        assert "pkg" in warnings[0].message

    def test_no_shadow_when_target_absent(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        _write_include_source_dir(tmp_path, "examples/pkg")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro<include source="examples/pkg" as="pkg"/></topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert [f for f in result.findings if f.type == "include_shadowed"] == []

    def test_ledger_authorized_shadow_does_not_warn(self, tmp_path):
        """A `.clm-include` ledger entry matching the include suppresses
        the warning — those files are sync-includes' materialization,
        not an ad-hoc local override."""
        import json as _json

        topic_dir = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        (topic_dir / "pkg").mkdir()
        (topic_dir / "pkg" / "main.py").write_text("# materialized\n")
        _write_include_source_dir(tmp_path, "examples/pkg")
        (topic_dir / ".clm-include").write_text(
            _json.dumps(
                {
                    "version": 1,
                    "entries": [
                        {
                            "as_path": "pkg",
                            "source": "examples/pkg",
                            "mode": "copy",
                        }
                    ],
                }
            )
        )

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro<include source="examples/pkg" as="pkg"/></topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert [f for f in result.findings if f.type == "include_shadowed"] == []

    def test_ledger_with_mismatched_source_still_warns(self, tmp_path):
        """Ledger lists a different source for the same as_path — that
        is a stale or unrelated entry; the shadow is unauthorized."""
        import json as _json

        topic_dir = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        (topic_dir / "pkg").mkdir()
        (topic_dir / "pkg" / "main.py").write_text("# local override\n")
        _write_include_source_dir(tmp_path, "examples/pkg")
        _write_include_source_dir(tmp_path, "examples/other")
        (topic_dir / ".clm-include").write_text(
            _json.dumps(
                {
                    "version": 1,
                    "entries": [
                        {
                            "as_path": "pkg",
                            "source": "examples/other",
                            "mode": "copy",
                        }
                    ],
                }
            )
        )

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro<include source="examples/pkg" as="pkg"/></topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")
        warnings = [f for f in result.findings if f.type == "include_shadowed"]
        assert len(warnings) == 1


class TestIncludeSourceIsTopicDir:
    """Source pointing inside slides/.../topic_* warns once per unique source."""

    def test_topic_dir_source_warns(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        _make_topic(tmp_path, "module_100_basics", "topic_020_other")
        # The include source is itself a topic dir.
        (tmp_path / "slides" / "module_100_basics" / "topic_020_other" / "pkg").mkdir()
        (tmp_path / "slides" / "module_100_basics" / "topic_020_other" / "pkg" / "x.py").write_text(
            "# x\n"
        )

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro<include
                  source="slides/module_100_basics/topic_020_other/pkg"
                  as="pkg"/></topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        warns = [f for f in result.warnings if f.type == "include_source_is_topic_dir"]
        assert len(warns) == 1
        assert "topic_020_other" in warns[0].message

    def test_topic_dir_source_dedup_across_topics(self, tmp_path):
        """One warning per unique source, even when many topics include it."""
        _make_topic(tmp_path, "module_100_basics", "topic_010_a")
        _make_topic(tmp_path, "module_100_basics", "topic_020_b")
        _make_topic(tmp_path, "module_100_basics", "topic_030_target")
        target_pkg = tmp_path / "slides" / "module_100_basics" / "topic_030_target" / "pkg"
        target_pkg.mkdir()
        (target_pkg / "x.py").write_text("# x\n")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <include source="slides/module_100_basics/topic_030_target/pkg" as="pkg"/>
              <topics>
                <topic>a</topic>
                <topic>b</topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        warns = [f for f in result.warnings if f.type == "include_source_is_topic_dir"]
        assert len(warns) == 1

    def test_non_topic_dir_source_no_warning(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        _write_include_source_dir(tmp_path, "examples/pkg")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro<include source="examples/pkg" as="pkg"/></topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert [f for f in result.findings if f.type == "include_source_is_topic_dir"] == []


class TestIncludeDependencies:
    """Surface [project] dependencies from the include's pyproject.toml."""

    def test_dependencies_info_emitted(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        pkg_src = _write_include_source_dir(tmp_path, "examples/SimpleChatbot/src/simple_chatbot")
        # pyproject lives one level above src/
        (pkg_src.parent.parent / "pyproject.toml").write_text(
            dedent("""\
            [project]
            name = "simple-chatbot"
            version = "0.1.0"
            dependencies = ["gradio>=4.0", "openai>=1.0"]
            """)
        )

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro<include
                  source="examples/SimpleChatbot/src/simple_chatbot"
                  as="simple_chatbot"/></topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        infos = [f for f in result.findings if f.type == "include_dependencies"]
        assert len(infos) == 1
        assert "gradio>=4.0" in infos[0].message
        assert "openai>=1.0" in infos[0].message
        assert "pyproject.toml" in infos[0].message

    def test_no_pyproject_no_finding(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        _write_include_source_dir(tmp_path, "examples/pkg")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro<include source="examples/pkg" as="pkg"/></topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert [f for f in result.findings if f.type == "include_dependencies"] == []

    def test_course_root_pyproject_not_used(self, tmp_path):
        """The host project's pyproject.toml must not be reported as the include's."""
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        # A pyproject at the *course root* should be ignored when there's
        # no nearer pyproject for the include.
        (tmp_path / "pyproject.toml").write_text(
            dedent("""\
            [project]
            name = "the-course-itself"
            version = "0.0.0"
            dependencies = ["this-is-not-an-include-dep"]
            """)
        )
        _write_include_source_dir(tmp_path, "examples/pkg")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro<include source="examples/pkg" as="pkg"/></topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert [f for f in result.findings if f.type == "include_dependencies"] == []

    def test_dependencies_dedup_across_topics(self, tmp_path):
        """One info per unique source, regardless of topic count."""
        _make_topic(tmp_path, "module_100_basics", "topic_010_a")
        _make_topic(tmp_path, "module_100_basics", "topic_020_b")
        pkg_src = _write_include_source_dir(tmp_path, "examples/pkg/src/pkg")
        (pkg_src.parent.parent / "pyproject.toml").write_text(
            dedent("""\
            [project]
            name = "pkg"
            version = "0.1.0"
            dependencies = ["x"]
            """)
        )

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <include source="examples/pkg/src/pkg" as="pkg"/>
              <topics>
                <topic>a</topic>
                <topic>b</topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        infos = [f for f in result.findings if f.type == "include_dependencies"]
        assert len(infos) == 1


class TestIncludeSectionInheritance:
    """Info-level audit of how section-level includes propagate."""

    def test_all_topics_inherit(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_a")
        _make_topic(tmp_path, "module_100_basics", "topic_020_b")
        _write_include_source_dir(tmp_path, "examples/pkg")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>Week 4</en></name>
              <include source="examples/pkg" as="pkg"/>
              <topics>
                <topic>a</topic>
                <topic>b</topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        infos = [f for f in result.findings if f.type == "include_section_inheritance"]
        assert len(infos) == 1
        msg = infos[0].message
        assert "Week 4" in msg
        assert "examples/pkg" in msg
        assert "inherited by: a, b" in msg
        assert "overridden by" not in msg

    def test_topic_override_with_different_source(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_a")
        _make_topic(tmp_path, "module_100_basics", "topic_020_b")
        _write_include_source_dir(tmp_path, "examples/pkg")
        _write_include_source_dir(tmp_path, "examples/custom")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>Week 4</en></name>
              <include source="examples/pkg" as="pkg"/>
              <topics>
                <topic>a</topic>
                <topic>b<include source="examples/custom" as="pkg"/></topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        infos = [f for f in result.findings if f.type == "include_section_inheritance"]
        assert len(infos) == 1
        msg = infos[0].message
        assert "inherited by: a" in msg
        assert "overridden by: b" in msg
        assert "examples/custom" in msg

    def test_topic_override_same_source_counts_as_inheriting(self, tmp_path):
        """A topic-level redeclaration with the same source is not an override."""
        _make_topic(tmp_path, "module_100_basics", "topic_010_a")
        _write_include_source_dir(tmp_path, "examples/pkg")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>Week 4</en></name>
              <include source="examples/pkg" as="pkg"/>
              <topics>
                <topic>a<include source="examples/pkg" as="pkg"/></topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        infos = [f for f in result.findings if f.type == "include_section_inheritance"]
        assert len(infos) == 1
        assert "inherited by: a" in infos[0].message
        assert "overridden by" not in infos[0].message

    def test_no_section_include_no_inheritance_finding(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_a")
        _write_include_source_dir(tmp_path, "examples/pkg")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>a<include source="examples/pkg" as="pkg"/></topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert [f for f in result.findings if f.type == "include_section_inheritance"] == []


def _make_topic_with_content(tmp_path: Path, module: str, topic: str, content: str) -> Path:
    """Create a topic dir whose single slide file holds *content*."""
    topic_dir = tmp_path / "slides" / module / topic
    topic_dir.mkdir(parents=True, exist_ok=True)
    (topic_dir / "slides_main.py").write_text(content, encoding="utf-8")
    return topic_dir


class TestCrossReferences:
    """Cross-reference (clm:) validation findings (Issue #17)."""

    def test_missing_target_is_error(self, tmp_path):
        _make_topic_with_content(
            tmp_path,
            "module_100_basics",
            "topic_010_intro",
            "# %% [markdown]\n# Intro\nSee [the deck](clm:nonexistent).\n",
        )
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")
        missing = [f for f in result.findings if f.type == "cross_reference_target_missing"]
        assert len(missing) == 1
        assert missing[0].severity == "error"
        assert "nonexistent" in missing[0].message

    def test_present_target_passes(self, tmp_path):
        _make_topic_with_content(
            tmp_path,
            "module_100_basics",
            "topic_010_intro",
            "# %% [markdown]\n# Intro\nSee [workshop](clm:workshop).\n",
        )
        _make_topic(tmp_path, "module_100_basics", "topic_020_workshop")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro</topic>
                <topic>workshop</topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")
        assert [f for f in result.findings if f.type.startswith("cross_reference")] == []

    def test_target_excluded_by_section_selection_is_missing(self, tmp_path):
        """A real topic that is not referenced by the spec is reported.

        ``validate_spec`` only sees the topics the spec includes, so a link
        to a topic that exists on disk but is omitted from the spec
        (the analogue of section filtering) is correctly flagged.
        """
        _make_topic_with_content(
            tmp_path,
            "module_100_basics",
            "topic_010_intro",
            "# %% [markdown]\n# Intro\nSee [workshop](clm:workshop).\n",
        )
        # The 'workshop' topic exists on disk but is NOT in the spec below.
        _make_topic(tmp_path, "module_100_basics", "topic_020_workshop")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")
        missing = [f for f in result.findings if f.type == "cross_reference_target_missing"]
        assert len(missing) == 1
        assert "workshop" in missing[0].message

    def test_ambiguous_multi_notebook_target_warns(self, tmp_path):
        _make_topic_with_content(
            tmp_path,
            "module_100_basics",
            "topic_010_intro",
            "# %% [markdown]\n# Intro\nSee [advanced](clm:advanced).\n",
        )
        adv_dir = tmp_path / "slides" / "module_100_basics" / "topic_020_advanced"
        adv_dir.mkdir(parents=True, exist_ok=True)
        (adv_dir / "slides_part_a.py").write_text("# %% [markdown]\n# A\n", encoding="utf-8")
        (adv_dir / "slides_part_b.py").write_text("# %% [markdown]\n# B\n", encoding="utf-8")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro</topic>
                <topic>advanced</topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")
        ambiguous = [f for f in result.findings if f.type == "cross_reference_ambiguous"]
        assert len(ambiguous) == 1
        assert ambiguous[0].severity == "warning"

    def test_disambiguated_multi_notebook_target_passes(self, tmp_path):
        _make_topic_with_content(
            tmp_path,
            "module_100_basics",
            "topic_010_intro",
            "# %% [markdown]\n# Intro\nSee [b](clm:advanced/slides_part_b).\n",
        )
        adv_dir = tmp_path / "slides" / "module_100_basics" / "topic_020_advanced"
        adv_dir.mkdir(parents=True, exist_ok=True)
        (adv_dir / "slides_part_a.py").write_text("# %% [markdown]\n# A\n", encoding="utf-8")
        (adv_dir / "slides_part_b.py").write_text("# %% [markdown]\n# B\n", encoding="utf-8")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro</topic>
                <topic>advanced</topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")
        assert [f for f in result.findings if f.type.startswith("cross_reference")] == []


class TestValidateTasks:
    """``<tasks>`` findings (``clm run``): structure + step resolution."""

    SECTIONS = """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>"""

    def _validate(self, tmp_path: Path, tasks_xml: str) -> SpecValidationResult:
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        spec_file = _write_spec(tmp_path, self.SECTIONS, tasks_xml)
        return validate_spec(spec_file, tmp_path / "slides")

    def test_clean_tasks_produce_no_findings(self, tmp_path):
        result = self._validate(
            tmp_path,
            """<tasks>
              <task name="pre-release">
                <step>export outline {spec} -o outline/</step>
                <step>build {spec}</step>
              </task>
            </tasks>""",
        )
        assert result.findings == []

    def test_structural_error_is_reported(self, tmp_path):
        result = self._validate(tmp_path, '<tasks><task name="empty"/></tasks>')
        assert any(f.type == "invalid_task" for f in result.errors)

    def test_unknown_command_is_reported(self, tmp_path):
        result = self._validate(
            tmp_path,
            '<tasks><task name="bad"><step>frobnicate {spec}</step></task></tasks>',
        )
        errors = [f for f in result.errors if f.type == "unknown_task_command"]
        assert len(errors) == 1
        assert "frobnicate" in errors[0].message

    def test_unknown_placeholder_is_reported(self, tmp_path):
        result = self._validate(
            tmp_path,
            '<tasks><task name="bad"><step>build {sepc}</step></task></tasks>',
        )
        errors = [f for f in result.errors if f.type == "invalid_task_step"]
        assert len(errors) == 1
        assert "{sepc}" in errors[0].message
