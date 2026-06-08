"""Parsing tests for the optional ``<subsection>`` day-of-week layer (issue #261)."""

import io

import pytest

from clm.core.course_spec import (
    WEEKDAY_ORDER,
    CourseSpec,
    CourseSpecError,
    SubsectionSpec,
)
from clm.core.utils.text_utils import Text


def _spec_xml(topics_block: str) -> str:
    return f"""
    <course>
        <name><de>Mein Kurs</de><en>My Course</en></name>
        <prog-lang>python</prog-lang>
        <description><de>d</de><en>d</en></description>
        <certificate><de>c</de><en>c</en></certificate>
        <sections>
            <section>
                <name><de>Woche 1</de><en>Week 1</en></name>
                <topics>
{topics_block}
                </topics>
            </section>
        </sections>
    </course>
    """


def test_subsection_defaults():
    sub = SubsectionSpec()
    assert sub.topics == []
    assert sub.weekdays == ()
    assert sub.weekday is None
    assert sub.name is None
    assert sub.enabled is True
    assert sub.optional is False


def test_subsection_topics_flatten_into_topics_list():
    xml = _spec_xml(
        """
        <subsection weekday="mon">
            <topic>mon_a</topic>
            <topic>mon_b</topic>
        </subsection>
        <subsection weekday="tue">
            <topic>tue_a</topic>
        </subsection>
        """
    )
    spec = CourseSpec.from_file(io.StringIO(xml))
    assert [t.id for t in spec.sections[0].topics] == ["mon_a", "mon_b", "tue_a"]


def test_bare_topics_and_subsections_interleave_in_document_order():
    xml = _spec_xml(
        """
        <topic>bare_one</topic>
        <subsection weekday="mon">
            <topic>mon_a</topic>
        </subsection>
        <topic>bare_two</topic>
        """
    )
    spec = CourseSpec.from_file(io.StringIO(xml))
    assert [t.id for t in spec.sections[0].topics] == ["bare_one", "mon_a", "bare_two"]


def test_byte_identical_flatten_matches_wrapper_removed_spec():
    """A spec with subsections flattens to the same topic list as the
    equivalent spec with the <subsection> wrappers removed."""
    wrapped = _spec_xml(
        """
        <topic>bare_one</topic>
        <subsection weekday="mon">
            <topic>mon_a</topic>
            <topic>mon_b</topic>
        </subsection>
        """
    )
    flat = _spec_xml(
        """
        <topic>bare_one</topic>
        <topic>mon_a</topic>
        <topic>mon_b</topic>
        """
    )
    wrapped_spec = CourseSpec.from_file(io.StringIO(wrapped))
    flat_spec = CourseSpec.from_file(io.StringIO(flat))
    assert wrapped_spec.sections[0].topics == flat_spec.sections[0].topics


def test_subsection_structure_retained():
    xml = _spec_xml(
        """
        <subsection weekday="mon">
            <topic>mon_a</topic>
            <topic>mon_b</topic>
        </subsection>
        <subsection weekday="tue">
            <name><de>Dienstag</de><en>Tuesday</en></name>
            <topic>tue_a</topic>
        </subsection>
        """
    )
    spec = CourseSpec.from_file(io.StringIO(xml))
    subs = spec.sections[0].subsections
    assert len(subs) == 2
    assert subs[0].weekday == "mon"
    assert subs[0].name is None
    assert [t.id for t in subs[0].topics] == ["mon_a", "mon_b"]
    assert subs[1].weekday == "tue"
    assert subs[1].name == Text(de="Dienstag", en="Tuesday")


def test_subsection_topic_objects_shared_with_flat_list():
    xml = _spec_xml('<subsection weekday="mon"><topic>mon_a</topic></subsection>')
    spec = CourseSpec.from_file(io.StringIO(xml))
    section = spec.sections[0]
    assert section.topics[0] is section.subsections[0].topics[0]


def test_no_subsections_means_empty_list():
    xml = _spec_xml("<topic>only_bare</topic>")
    spec = CourseSpec.from_file(io.StringIO(xml))
    assert spec.sections[0].subsections == []
    assert [t.id for t in spec.sections[0].topics] == ["only_bare"]


def test_disabled_subsection_dropped_by_default():
    xml = _spec_xml(
        """
        <subsection weekday="mon">
            <topic>mon_a</topic>
        </subsection>
        <subsection weekday="tue" enabled="false">
            <topic>tue_disabled</topic>
        </subsection>
        """
    )
    spec = CourseSpec.from_file(io.StringIO(xml))
    section = spec.sections[0]
    assert [t.id for t in section.topics] == ["mon_a"]
    assert [s.weekday for s in section.subsections] == ["mon"]


def test_disabled_subsection_retained_with_keep_disabled():
    xml = _spec_xml(
        """
        <subsection weekday="mon">
            <topic>mon_a</topic>
        </subsection>
        <subsection weekday="tue" enabled="false">
            <topic>tue_disabled</topic>
        </subsection>
        """
    )
    spec = CourseSpec.from_file(io.StringIO(xml), keep_disabled=True)
    section = spec.sections[0]
    # The disabled subsection is retained in `subsections` (so tooling can
    # surface it), but its topics must NOT enter the flat build list — even
    # under keep_disabled — to preserve build byte-identity (`--only-sections`
    # parses with keep_disabled=True) and keep disabled decks out of releases.
    assert [t.id for t in section.topics] == ["mon_a"]
    assert [(s.weekday, s.enabled) for s in section.subsections] == [
        ("mon", True),
        ("tue", False),
    ]


def test_disabled_subsection_topics_never_in_build_list_under_keep_disabled():
    """Regression for the byte-identity bug: the flattened build list
    (section.topics / iter_topic_bindings) must exclude disabled-subsection
    topics regardless of keep_disabled, since the build path has no per-topic
    enabled gate."""
    xml = _spec_xml(
        """
        <topic>bare</topic>
        <subsection weekday="mon">
            <topic>mon_a</topic>
        </subsection>
        <subsection weekday="tue" enabled="false">
            <topic>tue_disabled</topic>
        </subsection>
        """
    )
    for keep_disabled in (False, True):
        spec = CourseSpec.from_file(io.StringIO(xml), keep_disabled=keep_disabled)
        build_topic_ids = [b.topic_id for b in spec.iter_topic_bindings()]
        assert "tue_disabled" not in build_topic_ids
        assert build_topic_ids == ["bare", "mon_a"]


def test_nested_subsection_topics_are_ignored():
    """Non-goal: recursive nesting is not supported (one level only). A
    <subsection> inside a <subsection> contributes no topics."""
    xml = _spec_xml(
        """
        <subsection weekday="mon">
            <topic>outer_topic</topic>
            <subsection weekday="tue">
                <topic>inner_topic</topic>
            </subsection>
        </subsection>
        """
    )
    spec = CourseSpec.from_file(io.StringIO(xml))
    section = spec.sections[0]
    assert [t.id for t in section.topics] == ["outer_topic"]
    assert len(section.subsections) == 1
    assert [t.id for t in section.subsections[0].topics] == ["outer_topic"]


def test_empty_name_element_treated_as_absent():
    """An all-empty <name> must read as 'no override' so the weekday fallback
    still produces a label."""
    xml = _spec_xml(
        """
        <subsection weekday="mon"><name></name><topic>t</topic></subsection>
        <subsection weekday="tue"><name><de></de><en></en></name><topic>u</topic></subsection>
        """
    )
    spec = CourseSpec.from_file(io.StringIO(xml))
    subs = spec.sections[0].subsections
    assert subs[0].name is None
    assert subs[1].name is None


def test_subsection_without_weekday_uses_name_only():
    xml = _spec_xml(
        """
        <subsection>
            <name><de>Thema A</de><en>Theme A</en></name>
            <topic>topic_a</topic>
        </subsection>
        """
    )
    spec = CourseSpec.from_file(io.StringIO(xml))
    sub = spec.sections[0].subsections[0]
    assert sub.weekday is None
    assert sub.name == Text(de="Thema A", en="Theme A")


@pytest.mark.parametrize("weekday", list(WEEKDAY_ORDER))
def test_all_weekday_tokens_accepted(weekday):
    xml = _spec_xml(f'<subsection weekday="{weekday}"><topic>t</topic></subsection>')
    spec = CourseSpec.from_file(io.StringIO(xml))
    assert spec.sections[0].subsections[0].weekday == weekday


def test_weekday_case_insensitive():
    xml = _spec_xml('<subsection weekday="MON"><topic>t</topic></subsection>')
    spec = CourseSpec.from_file(io.StringIO(xml))
    assert spec.sections[0].subsections[0].weekday == "mon"


def test_invalid_weekday_rejected():
    xml = _spec_xml('<subsection weekday="monday"><topic>t</topic></subsection>')
    with pytest.raises(CourseSpecError, match="Invalid weekday"):
        CourseSpec.from_file(io.StringIO(xml))


def test_multiple_weekdays_parsed_in_order():
    xml = _spec_xml('<subsection weekday="mon,tue,wed"><topic>t</topic></subsection>')
    spec = CourseSpec.from_file(io.StringIO(xml))
    sub = spec.sections[0].subsections[0]
    assert sub.weekdays == ("mon", "tue", "wed")
    # The back-compat single-day property returns the first token.
    assert sub.weekday == "mon"


def test_multiple_weekdays_whitespace_and_case_tolerant():
    xml = _spec_xml('<subsection weekday=" MON , Tue ,wed"><topic>t</topic></subsection>')
    spec = CourseSpec.from_file(io.StringIO(xml))
    assert spec.sections[0].subsections[0].weekdays == ("mon", "tue", "wed")


def test_duplicate_weekday_within_attr_collapsed():
    xml = _spec_xml('<subsection weekday="mon,mon,tue"><topic>t</topic></subsection>')
    spec = CourseSpec.from_file(io.StringIO(xml))
    assert spec.sections[0].subsections[0].weekdays == ("mon", "tue")


def test_invalid_weekday_in_list_rejected():
    xml = _spec_xml('<subsection weekday="mon,funday"><topic>t</topic></subsection>')
    with pytest.raises(CourseSpecError, match="Invalid weekday"):
        CourseSpec.from_file(io.StringIO(xml))


def test_empty_weekday_attr_is_no_days():
    xml = _spec_xml(
        '<subsection weekday=""><name><de>x</de><en>x</en></name><topic>t</topic></subsection>'
    )
    spec = CourseSpec.from_file(io.StringIO(xml))
    sub = spec.sections[0].subsections[0]
    assert sub.weekdays == ()
    assert sub.weekday is None


def test_subsection_optional_attribute_parsed():
    xml = _spec_xml(
        """
        <subsection weekday="mon"><topic>a</topic></subsection>
        <subsection weekday="tue" optional="true"><topic>b</topic></subsection>
        """
    )
    spec = CourseSpec.from_file(io.StringIO(xml))
    subs = spec.sections[0].subsections
    assert [s.optional for s in subs] == [False, True]
    # optional is presentation-only: both topics still flatten into the build.
    assert [t.id for t in spec.sections[0].topics] == ["a", "b"]


def test_section_optional_attribute_parsed():
    xml = """
    <course>
        <name><de>K</de><en>C</en></name>
        <prog-lang>python</prog-lang>
        <description><de>d</de><en>d</en></description>
        <certificate><de>c</de><en>c</en></certificate>
        <sections>
            <section optional="true">
                <name><de>W1</de><en>W1</en></name>
                <topics><topic>a</topic></topics>
            </section>
        </sections>
    </course>
    """
    spec = CourseSpec.from_file(io.StringIO(xml))
    assert spec.sections[0].optional is True
    # Default is False.
    assert SubsectionSpec().optional is False


def test_invalid_subsection_enabled_value_rejected():
    xml = _spec_xml('<subsection enabled="maybe"><topic>t</topic></subsection>')
    with pytest.raises(CourseSpecError, match="enabled"):
        CourseSpec.from_file(io.StringIO(xml))


def test_subsection_topic_grammar_supports_attributes():
    xml = _spec_xml(
        """
        <subsection weekday="mon">
            <topic author="Prof. X" prog-lang="cpp">styled_topic</topic>
        </subsection>
        """
    )
    spec = CourseSpec.from_file(io.StringIO(xml))
    topic = spec.sections[0].subsections[0].topics[0]
    assert topic.id == "styled_topic"
    assert topic.author == "Prof. X"
    assert topic.prog_lang == "cpp"


def test_subsection_topic_with_include_child_captured():
    xml = _spec_xml(
        """
        <subsection weekday="mon">
            <topic id="with_include">
                <include source="examples/foo" as="foo"/>
            </topic>
        </subsection>
        """
    )
    spec = CourseSpec.from_file(io.StringIO(xml))
    topic = spec.sections[0].subsections[0].topics[0]
    assert topic.id == "with_include"
    assert len(topic.includes) == 1


def test_dir_group_nested_in_subsection_is_collected():
    xml = _spec_xml(
        """
        <subsection weekday="mon">
            <topic id="with_dg">
                <dir-group>
                    <name>Code</name>
                    <path>code/dg</path>
                </dir-group>
            </topic>
        </subsection>
        """
    )
    spec = CourseSpec.from_file(io.StringIO(xml))
    assert [dg.path for dg in spec.dictionaries] == ["code/dg"]


def test_dir_group_in_disabled_subsection_dropped_by_default():
    xml = _spec_xml(
        """
        <subsection weekday="mon" enabled="false">
            <topic id="with_dg">
                <dir-group>
                    <name>Code</name>
                    <path>code/dg</path>
                </dir-group>
            </topic>
        </subsection>
        """
    )
    spec = CourseSpec.from_file(io.StringIO(xml))
    assert spec.dictionaries == []
    kept = CourseSpec.from_file(io.StringIO(xml), keep_disabled=True)
    assert [dg.path for dg in kept.dictionaries] == ["code/dg"]
