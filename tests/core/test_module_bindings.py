"""Tests for module-aware spec helpers added on CourseSpec/SectionSpec.

The build pipeline (``Course._build_topics``) and the spec validator
already honoured per-section / per-topic ``module=`` attributes. These
tests cover the helpers that other consumers (normalize-slides,
validate-slides, search-slides, resolve-topic with --course-spec,
authoring-rules) now share so cohort-archive scenarios resolve uniformly
across the codebase.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from clm.core.course_spec import CourseSpec, SectionSpec, TopicBinding, TopicSpec
from clm.core.topic_resolver import (
    TopicMatch,
    matches_for_binding,
)


def _write_spec(tmp_path: Path, sections_xml: str) -> Path:
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
        </course>
        """),
        encoding="utf-8",
    )
    return spec_file


class TestSectionSpecModuleFor:
    """``SectionSpec.module_for(topic_spec)`` resolves the effective module."""

    def test_topic_module_overrides_section(self):
        section = SectionSpec(
            name=None,  # type: ignore[arg-type]
            topics=[],
            module="module_section",
        )
        topic = TopicSpec(id="t", module="module_topic")
        assert section.module_for(topic) == "module_topic"

    def test_section_module_used_when_topic_unbound(self):
        section = SectionSpec(name=None, topics=[], module="module_section")  # type: ignore[arg-type]
        topic = TopicSpec(id="t")
        assert section.module_for(topic) == "module_section"

    def test_neither_set_returns_none(self):
        section = SectionSpec(name=None, topics=[])  # type: ignore[arg-type]
        topic = TopicSpec(id="t")
        assert section.module_for(topic) is None


class TestIterTopicBindings:
    """``CourseSpec.iter_topic_bindings`` yields one TopicBinding per topic."""

    def test_no_modules_yields_unbound(self, tmp_path):
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic><topic>variables</topic></topics>
            </section></sections>""",
        )
        spec = CourseSpec.from_file(spec_file)
        bindings = list(spec.iter_topic_bindings())

        assert len(bindings) == 2
        assert all(isinstance(b, TopicBinding) for b in bindings)
        assert [b.topic_id for b in bindings] == ["intro", "variables"]
        assert all(b.effective_module is None for b in bindings)

    def test_section_module_propagates(self, tmp_path):
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section module="module_545_frozen">
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )
        spec = CourseSpec.from_file(spec_file)
        bindings = list(spec.iter_topic_bindings())

        assert bindings[0].effective_module == "module_545_frozen"

    def test_topic_module_overrides_section_module(self, tmp_path):
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section module="module_section">
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro</topic>
                <topic module="module_topic">advanced</topic>
              </topics>
            </section></sections>""",
        )
        spec = CourseSpec.from_file(spec_file)
        bindings = list(spec.iter_topic_bindings())

        assert bindings[0].effective_module == "module_section"
        assert bindings[1].effective_module == "module_topic"


class TestTopicBindings:
    """``CourseSpec.topic_bindings`` returns ``(topic_id, module)`` pairs."""

    def test_two_sections_different_modules(self, tmp_path):
        """Same topic ID bound to two modules yields two distinct entries.

        This is the cohort-archive scenario: the live module and a frozen
        copy share topic IDs but are deliberately bound to different
        module directories. ``get_course_topic_ids`` collapses them — the
        new ``topic_bindings`` keeps them apart.
        """
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections>
              <section module="module_100_live">
                <name><de>L</de><en>L</en></name>
                <topics><topic>intro</topic></topics>
              </section>
              <section module="module_545_frozen">
                <name><de>F</de><en>F</en></name>
                <topics><topic>intro</topic></topics>
              </section>
            </sections>""",
        )
        spec = CourseSpec.from_file(spec_file)
        assert spec.topic_bindings() == {
            ("intro", "module_100_live"),
            ("intro", "module_545_frozen"),
        }

    def test_unbound_topic(self, tmp_path):
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )
        spec = CourseSpec.from_file(spec_file)
        assert spec.topic_bindings() == {("intro", None)}


class TestMatchesForBinding:
    """``matches_for_binding`` filters topic_map results by module."""

    def _make_topic_map(self) -> dict[str, list[TopicMatch]]:
        return {
            "intro": [
                TopicMatch(
                    topic_id="intro",
                    path=Path("/x/module_100_live/topic_010_intro"),
                    path_type="directory",
                    module="module_100_live",
                ),
                TopicMatch(
                    topic_id="intro",
                    path=Path("/x/module_545_frozen/topic_010_intro"),
                    path_type="directory",
                    module="module_545_frozen",
                ),
            ]
        }

    def test_unbound_returns_all(self):
        topic_map = self._make_topic_map()
        result = matches_for_binding(topic_map, "intro", None)
        assert len(result) == 2

    def test_bound_filters_to_module(self):
        topic_map = self._make_topic_map()
        result = matches_for_binding(topic_map, "intro", "module_545_frozen")
        assert len(result) == 1
        assert result[0].module == "module_545_frozen"

    def test_unknown_topic_returns_empty(self):
        topic_map = self._make_topic_map()
        assert matches_for_binding(topic_map, "missing", None) == []

    def test_bound_to_unknown_module_returns_empty(self):
        topic_map = self._make_topic_map()
        assert matches_for_binding(topic_map, "intro", "module_nonexistent") == []
