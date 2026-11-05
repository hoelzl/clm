"""Parsing tests for the agenda layer: ``<block>``, ``<agenda>``, ``<agenda-targets>``.

Issue #1163. A ``<block>`` is a time block with teaching units below a day
``<subsection>``. Blocks are presentation/planning only: their topics flatten
into the subsection (and from there into the section's build list) in document
order, so a spec with ``<block>`` wrappers builds exactly like the same spec
without them.
"""

import io
from pathlib import Path

import pytest

from clm.core.course import Course
from clm.core.course_spec import (
    AgendaSpec,
    AgendaTargetsSpec,
    BlockSpec,
    CourseSpec,
    CourseSpecError,
)
from clm.core.utils.text_utils import Text

TEST_DATA = Path("tests/test-data")


def _spec_xml(topics_block: str, *, course_extra: str = "", section_extra: str = "") -> str:
    return f"""
    <course>
        <name><de>Mein Kurs</de><en>My Course</en></name>
        <prog-lang>python</prog-lang>
        <description><de>d</de><en>d</en></description>
        <certificate><de>c</de><en>c</en></certificate>
        {course_extra}
        <sections>
            <section>
                <name><de>Woche 1</de><en>Week 1</en></name>
                {section_extra}
                <topics>
{topics_block}
                </topics>
            </section>
        </sections>
    </course>
    """


def _parse(xml: str, **kwargs) -> CourseSpec:
    return CourseSpec.from_file(io.StringIO(xml), **kwargs)


# ---------------------------------------------------------------------------
# Flattening (the build contract)
# ---------------------------------------------------------------------------
def test_block_topics_flatten_in_document_order_interleaved_with_bare_topics():
    xml = _spec_xml(
        """
        <subsection weekday="mon">
            <topic>first</topic>
            <block units="2"><topic>b1_a</topic><topic>b1_b</topic></block>
            <topic>between</topic>
            <block units="2"><topic>b2_a</topic></block>
            <topic>last</topic>
        </subsection>
        """
    )
    spec = _parse(xml)
    expected = ["first", "b1_a", "b1_b", "between", "b2_a", "last"]
    assert [t.id for t in spec.sections[0].topics] == expected
    assert [t.id for t in spec.sections[0].subsections[0].topics] == expected


def test_spec_with_blocks_parses_identically_to_spec_without_them():
    """Regression test for #1163: blocks never change the build list."""
    with_blocks = _spec_xml(
        """
        <topic>bare_one</topic>
        <subsection weekday="mon">
            <block units="4" form="theory">
                <name><de>A</de><en>A</en></name>
                <contents><de>x</de><en>x</en></contents>
                <topic>mon_a</topic>
                <topic>mon_b</topic>
            </block>
            <block units="4" form="practice" status="planned"/>
            <topic>mon_c</topic>
        </subsection>
        """
    )
    without_blocks = _spec_xml(
        """
        <topic>bare_one</topic>
        <subsection weekday="mon">
            <topic>mon_a</topic>
            <topic>mon_b</topic>
            <topic>mon_c</topic>
        </subsection>
        """
    )
    assert _parse(with_blocks).sections[0].topics == _parse(without_blocks).sections[0].topics


def test_block_spec_builds_the_same_course_as_subsection_spec():
    """The block-wrapped fixture resolves to the very same build topics."""
    root = TEST_DATA
    blocks = CourseSpec.from_file(root / "course-specs" / "block-spec.xml")
    plain = CourseSpec.from_file(root / "course-specs" / "subsection-spec.xml")
    assert [[t.id for t in s.topics] for s in blocks.sections] == [
        [t.id for t in s.topics] for s in plain.sections
    ]
    course_blocks = Course.from_spec(blocks, root, output_root=None)
    course_plain = Course.from_spec(plain, root, output_root=None)
    assert [[t.path for t in s.topics] for s in course_blocks.sections] == [
        [t.path for t in s.topics] for s in course_plain.sections
    ]
    assert [[nb.path for t in s.topics for nb in t.notebooks] for s in course_blocks.sections] == [
        [nb.path for t in s.topics for nb in t.notebooks] for s in course_plain.sections
    ]


def test_block_topic_objects_shared_with_subsection_and_section():
    xml = _spec_xml(
        '<subsection weekday="mon"><block units="1"><topic>a</topic></block></subsection>'
    )
    spec = _parse(xml)
    section = spec.sections[0]
    sub = section.subsections[0]
    assert section.topics[0] is sub.topics[0] is sub.blocks[0].topics[0]
    assert sub.block_for(sub.topics[0]) is sub.blocks[0]


def test_block_for_bare_topic_is_none():
    xml = _spec_xml(
        """
        <subsection weekday="mon">
            <topic>bare</topic>
            <block units="1"><topic>inside</topic></block>
        </subsection>
        """
    )
    sub = _parse(xml).sections[0].subsections[0]
    assert sub.block_for(sub.topics[0]) is None
    assert sub.block_for(sub.topics[1]) is sub.blocks[0]
    assert [t.id for t in sub.unblocked_topics] == ["bare"]


def test_disabled_subsection_block_topics_never_in_build_list():
    xml = _spec_xml(
        """
        <subsection weekday="mon" enabled="false">
            <block units="8"><topic>hidden</topic></block>
        </subsection>
        <subsection weekday="tue"><block units="8"><topic>shown</topic></block></subsection>
        """
    )
    assert [t.id for t in _parse(xml).sections[0].topics] == ["shown"]
    kept = _parse(xml, keep_disabled=True)
    assert [t.id for t in kept.sections[0].topics] == ["shown"]
    assert [t.id for t in kept.sections[0].subsections[0].blocks[0].topics] == ["hidden"]


def test_dir_group_nested_in_block_topic_is_collected():
    xml = _spec_xml(
        """
        <subsection weekday="mon">
            <block units="2">
                <topic id="with_dg">
                    <dir-group><name>Code</name><path>code/dg</path></dir-group>
                </topic>
            </block>
        </subsection>
        """
    )
    assert [dg.path for dg in _parse(xml).dictionaries] == ["code/dg"]


def test_include_on_block_topic_is_captured():
    xml = _spec_xml(
        """
        <subsection weekday="mon">
            <block units="2">
                <topic id="t"><include source="shared/x.py" as="x.py"/></topic>
            </block>
        </subsection>
        """
    )
    topic = _parse(xml).sections[0].topics[0]
    assert topic.id == "t"
    assert len(topic.includes) == 1


# ---------------------------------------------------------------------------
# Block attributes
# ---------------------------------------------------------------------------
def test_block_defaults():
    block = BlockSpec()
    assert block.topics == []
    assert block.units is None
    assert block.form is None
    assert block.status == "ready"
    assert block.standalone is False
    assert block.pace == "full"
    assert block.name is None
    assert block.contents is None


def test_block_attributes_and_texts_parsed():
    xml = _spec_xml(
        """
        <subsection weekday="mon">
            <block units="4" form="theory-practice" status="adapt" standalone="true" pace="recap">
                <name><de>Lernkontrolle</de><en>Learning check</en></name>
                <contents>Quiz</contents>
            </block>
        </subsection>
        """
    )
    block = _parse(xml).sections[0].subsections[0].blocks[0]
    assert block.units == 4
    assert block.form == "theory-practice"
    assert block.status == "adapt"
    assert block.standalone is True
    assert block.pace == "recap"
    assert block.name == Text(de="Lernkontrolle", en="Learning check")
    assert block.contents == Text(de="Quiz", en="Quiz")
    assert block.topics == []


def test_block_name_single_language_falls_back():
    xml = _spec_xml(
        '<subsection weekday="mon"><block units="1"><name><de>Nur Deutsch</de></name>'
        "</block></subsection>"
    )
    block = _parse(xml).sections[0].subsections[0].blocks[0]
    assert block.name == Text(de="Nur Deutsch", en="Nur Deutsch")


def test_block_without_units_parses():
    """A missing units value is a validation finding, not a parse error."""
    xml = _spec_xml('<subsection weekday="mon"><block form="theory"/></subsection>')
    assert _parse(xml).sections[0].subsections[0].blocks[0].units is None


@pytest.mark.parametrize("value", ["0", "-1", "four", "2.5", ""])
def test_invalid_units_rejected(value):
    xml = _spec_xml(f'<subsection weekday="mon"><block units="{value}"/></subsection>')
    with pytest.raises(CourseSpecError, match="units"):
        _parse(xml)


@pytest.mark.parametrize(
    ("attr", "value"),
    [
        ("form", "lecture"),
        ("form", "theory/practice"),
        ("status", "done"),
        ("pace", "slow"),
        ("standalone", "maybe"),
    ],
)
def test_unknown_enum_values_rejected(attr, value):
    xml = _spec_xml(f'<subsection weekday="mon"><block {attr}="{value}"/></subsection>')
    with pytest.raises(CourseSpecError, match=attr):
        _parse(xml)


def test_enum_values_case_insensitive():
    xml = _spec_xml(
        '<subsection weekday="mon"><block form="Theory" status="PLANNED" pace="Recap"/>'
        "</subsection>"
    )
    block = _parse(xml).sections[0].subsections[0].blocks[0]
    assert (block.form, block.status, block.pace) == ("theory", "planned", "recap")


# ---------------------------------------------------------------------------
# Misplaced blocks are hard errors (never a silent drop of their topics)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "topics_block",
    [
        '<block units="2"><topic>a</topic></block>',
        '<subsection weekday="mon"><block units="2"><block><topic>a</topic></block></block>'
        "</subsection>",
        '<subsection weekday="mon"><topic id="t"><block units="1"/></topic></subsection>',
    ],
)
def test_block_outside_subsection_is_a_hard_error(topics_block):
    with pytest.raises(CourseSpecError, match="<block>"):
        _parse(_spec_xml(topics_block))


def test_block_directly_in_section_is_a_hard_error():
    xml = _spec_xml("<topic>a</topic>", section_extra='<block units="2"/>')
    with pytest.raises(CourseSpecError, match="<block>"):
        _parse(xml)


def test_block_in_disabled_section_still_a_hard_error():
    xml = """
    <course>
        <name>C</name><prog-lang>python</prog-lang>
        <sections>
            <section enabled="false">
                <name>Off</name>
                <topics><block units="2"><topic>a</topic></block></topics>
            </section>
        </sections>
    </course>
    """
    with pytest.raises(CourseSpecError, match="<block>"):
        _parse(xml)


# ---------------------------------------------------------------------------
# <agenda> and <agenda-targets>
# ---------------------------------------------------------------------------
def test_section_agenda_parsed():
    xml = _spec_xml(
        "<topic>a</topic>",
        section_extra="""
        <agenda>
            <subtitle><de>Vom Chat zum Code</de><en>From chat to code</en></subtitle>
            <outcome><de>Eigener Chatbot</de></outcome>
        </agenda>
        """,
    )
    agenda = _parse(xml).sections[0].agenda
    assert agenda == AgendaSpec(
        subtitle=Text(de="Vom Chat zum Code", en="From chat to code"),
        outcome=Text(de="Eigener Chatbot", en="Eigener Chatbot"),
    )


def test_section_without_agenda_is_none():
    assert _parse(_spec_xml("<topic>a</topic>")).sections[0].agenda is None


def test_empty_agenda_has_empty_texts():
    xml = _spec_xml("<topic>a</topic>", section_extra="<agenda/>")
    agenda = _parse(xml).sections[0].agenda
    assert agenda == AgendaSpec(subtitle=Text(de="", en=""), outcome=Text(de="", en=""))


def test_agenda_targets_parsed():
    xml = _spec_xml(
        "<topic>a</topic>",
        course_extra=(
            '<agenda-targets unit-minutes="50" units-per-day="8" days-per-week="5" '
            'units-total="480"/>'
        ),
    )
    assert _parse(xml).agenda_targets == AgendaTargetsSpec(
        unit_minutes=50, units_per_day=8, days_per_week=5, units_total=480
    )


def test_agenda_targets_defaults():
    xml = _spec_xml("<topic>a</topic>", course_extra="<agenda-targets/>")
    assert _parse(xml).agenda_targets == AgendaTargetsSpec(
        unit_minutes=45, units_per_day=None, days_per_week=None, units_total=None
    )


def test_no_agenda_targets_is_none():
    assert _parse(_spec_xml("<topic>a</topic>")).agenda_targets is None


@pytest.mark.parametrize(
    "attrs",
    ['unit-minutes="0"', 'units-per-day="x"', 'days-per-week="-5"', 'units-total="1.5"'],
)
def test_invalid_agenda_targets_rejected(attrs):
    xml = _spec_xml("<topic>a</topic>", course_extra=f"<agenda-targets {attrs}/>")
    with pytest.raises(CourseSpecError, match="agenda-targets"):
        _parse(xml)
