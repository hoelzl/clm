"""Tests for the agenda model built from a course spec's block layer (issue #1163)."""

import io
import xml.etree.ElementTree as ET
from pathlib import Path

from clm.core.agenda import FORM_LABELS, build_agenda, spec_uses_blocks, strip_week_prefix
from clm.core.course_spec import CourseSpec, find_unknown_elements

BLOCK_SPEC = Path("tests/test-data/course-specs/block-spec.xml")


def _parse(xml: str, **kwargs) -> CourseSpec:
    return CourseSpec.from_file(io.StringIO(xml), **kwargs)


def _course(sections: str) -> str:
    return f"""
    <course>
        <name>C</name><prog-lang>python</prog-lang>
        <sections>{sections}</sections>
    </course>
    """


def test_block_spec_weeks_days_and_units():
    weeks = build_agenda(CourseSpec.from_file(BLOCK_SPEC))
    assert [w.number for w in weeks] == [1, 2]
    assert [w.units for w in weeks] == [16, 8]
    week1 = weeks[0]
    assert [d.label for d in week1.days] == ["1", "2"]
    assert [d.units for d in week1.days] == [8, 8]
    assert [b.name.en for b in week1.days[0].blocks] == ["Getting started", "Exercises"]
    assert week1.section.agenda is not None
    assert weeks[1].section.agenda is None


def test_sections_without_blocks_or_agenda_are_not_weeks():
    xml = _course(
        """
        <section><name>Intro</name><topics><topic>a</topic></topics></section>
        <section><name>W1</name><topics>
            <subsection weekday="mon"><block units="8"/></subsection>
        </topics></section>
        <section><name>W2</name><agenda/><topics><topic>b</topic></topics></section>
        """
    )
    weeks = build_agenda(_parse(xml))
    assert [w.section.name.en for w in weeks] == ["W1", "W2"]
    assert [w.number for w in weeks] == [1, 2]
    assert weeks[1].units == 0


def test_multi_weekday_subsection_spans_days():
    xml = _course(
        """
        <section><name>W1</name><topics>
            <subsection weekday="mon,tue"><block units="16"/></subsection>
            <subsection weekday="wed"><block units="8"/></subsection>
        </topics></section>
        """
    )
    days = build_agenda(_parse(xml))[0].days
    assert [(d.label, d.span, d.units) for d in days] == [("1–2", 2, 16), ("3", 1, 8)]


def test_block_without_units_counts_as_zero():
    xml = _course(
        """
        <section><name>W1</name><topics>
            <subsection weekday="mon"><block units="3"/><block/></subsection>
        </topics></section>
        """
    )
    assert build_agenda(_parse(xml))[0].units == 3


def test_optional_sections_and_subsections_excluded_by_default():
    xml = _course(
        """
        <section><name>W1</name><topics>
            <subsection weekday="mon"><block units="8"/></subsection>
            <subsection weekday="tue" optional="true"><block units="8"/></subsection>
        </topics></section>
        <section optional="true"><name>W2</name><topics>
            <subsection weekday="mon"><block units="8"/></subsection>
        </topics></section>
        """
    )
    spec = _parse(xml)
    assert [w.units for w in build_agenda(spec)] == [8]
    assert [w.units for w in build_agenda(spec, include_optional=True)] == [16, 8]


def test_disabled_content_excluded_even_when_parsed():
    xml = _course(
        """
        <section><name>W1</name><topics>
            <subsection weekday="mon"><block units="8"/></subsection>
            <subsection weekday="tue" enabled="false"><block units="8"/></subsection>
        </topics></section>
        <section enabled="false"><name>W2</name><topics>
            <subsection weekday="mon"><block units="8"/></subsection>
        </topics></section>
        """
    )
    weeks = build_agenda(_parse(xml, keep_disabled=True))
    assert [w.units for w in weeks] == [8]
    assert len(weeks[0].days) == 1


def test_spec_uses_blocks():
    assert spec_uses_blocks(CourseSpec.from_file(BLOCK_SPEC))
    xml = _course(
        '<section><name>W</name><topics><subsection weekday="mon"><topic>a</topic>'
        "</subsection></topics></section>"
    )
    assert not spec_uses_blocks(_parse(xml))


def test_form_labels_localized():
    assert FORM_LABELS["theory"].de == "Theorie"
    assert FORM_LABELS["practice"].en == "Practice"
    assert FORM_LABELS["theory-practice"].de == "Theorie/Praxis"
    assert FORM_LABELS["theory-practice"].en == "Theory/Practice"


def _documented_agenda_example() -> str:
    """The ``<block>`` example from the spec-files info topic."""
    text = Path("src/clm/cli/info_topics/spec-files.md").read_text(encoding="utf-8")
    section = text.split("#### `<block>`", 1)[1]
    return section.split("```xml", 1)[1].split("```", 1)[0]


def test_documented_example_parses_and_is_fully_known():
    """The info-topic example must stay valid: it parses and every element is known."""
    xml = _documented_agenda_example()
    assert find_unknown_elements(ET.fromstring(xml)) == []
    spec = _parse(xml)
    assert [w.units for w in build_agenda(spec)] == [16]
    assert spec.agenda_targets is not None
    assert spec.agenda_targets.units_total == 480


def test_test_data_specs_have_no_unknown_elements_but_legacy_github_urls():
    for path in sorted(Path("tests/test-data/course-specs").glob("*.xml")):
        unknown = find_unknown_elements(ET.parse(path).getroot())
        assert {u.path for u in unknown} <= {"course/github/de", "course/github/en"}, path


def test_strip_week_prefix():
    assert strip_week_prefix("Woche 05: LLM-APIs in der Praxis") == "LLM-APIs in der Praxis"
    assert strip_week_prefix("Week 5 – Agents") == "Agents"
    assert strip_week_prefix("Agents") == "Agents"
    assert strip_week_prefix("Woche 1") == "Woche 1"
