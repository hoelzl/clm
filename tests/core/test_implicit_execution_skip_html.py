"""``html="no"`` topics never get an implicit cache-producer execution (#871).

A build restricted to targets without ``recording`` HTML adds an *implicit*
Recording-HTML execution per notebook to populate the executed-notebook
cache for the consumer HTML it *did* request. That block ignored the topic's
``html="no"`` flag, while the explicit output list honours it — so a topic
that has **no HTML outputs at all** (and therefore never executes in a full
build) was executed anyway as soon as the target set was narrowed: with
``-T shared`` an ``html="no"`` deck that had never run, and by construction
has no HTTP-replay cassette, hit the network live.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from clm.core.course import Course
from clm.core.course_spec import CourseSpec
from clm.core.operation import Concurrently, NoOperation
from clm.core.utils.execution_utils import HTML_SPEAKER_STAGE

pytestmark = pytest.mark.asyncio

DATA_DIR = Path(__file__).parent.parent / "test-data"

# One target, ``completed`` HTML only: the recording producer is *not*
# requested, so the resolver demands an implicit execution for it.
_SPEC_TEMPLATE = """
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <description><de>d</de><en>d</en></description>
    <certificate><de>c</de><en>c</en></certificate>
    <output-targets>
        <output-target name="shared">
            <path>output/shared</path>
            <kinds><kind>completed</kind></kinds>
            <formats><format>html</format><format>notebook</format></formats>
            <languages><language>en</language></languages>
        </output-target>
    </output-targets>
    <sections>
        <section>
            <name><de>W1</de><en>W1</en></name>
            <topics>{topics}</topics>
        </section>
    </sections>
</course>
"""


def _course(tmp_path: Path, topics_xml: str) -> Course:
    spec = CourseSpec.from_file(io.StringIO(_SPEC_TEMPLATE.format(topics=topics_xml)))
    return Course.from_spec(spec, DATA_DIR, tmp_path)


async def _notebook_ops(course: Course, stage: int):
    """Every notebook operation the build submits for *stage* — mirrors
    ``Course.process_stage_for_target``'s iteration."""

    def flatten(op):
        if isinstance(op, NoOperation):
            return []
        if isinstance(op, Concurrently):
            return [inner for o in op.operations for inner in flatten(o)]
        return [op]

    ops = []
    for target in course.output_targets:
        implicit = course.implicit_executions_for_stage(stage, target)
        for file in course.files:
            op = await file.get_processing_operation(
                target.output_root, stage=stage, target=target, implicit_executions=implicit
            )
            ops.extend(flatten(op))
    return [o for o in ops if type(o).__name__ == "ProcessNotebookOperation"]


async def test_control_normal_topic_gets_the_implicit_producer(tmp_path):
    """Fixture sanity: without ``html="no"`` the narrowed target set does
    schedule the implicit recording execution (this is what #871's full
    build relied on)."""
    course = _course(tmp_path, "<topic>some_topic_from_test_1</topic>")
    assert course.implicit_executions, "resolver should demand a recording producer"

    ops = await _notebook_ops(course, HTML_SPEAKER_STAGE)
    implicit = [o for o in ops if o.is_implicit_execution]
    assert [(o.language, o.format, o.kind) for o in implicit] == [("en", "html", "recording")]


async def test_html_no_topic_gets_no_implicit_producer(tmp_path):
    """Regression test for #871: a topic with no HTML outputs has no HTML
    consumer to serve, so nothing may execute it to warm the cache."""
    course = _course(tmp_path, '<topic html="no">some_topic_from_test_1</topic>')
    assert course.implicit_executions, "resolver still demands a producer course-wide"

    ops = await _notebook_ops(course, HTML_SPEAKER_STAGE)
    assert [o for o in ops if o.is_implicit_execution] == []
    # And nothing else in the producer stage either — the deck must not run.
    assert [o for o in ops if o.format == "html"] == []


async def test_mixed_topics_only_the_html_topic_gets_the_producer(tmp_path):
    """Neighbour: the gate is per topic. In a section mixing an ``html="no"``
    topic with a normal one, exactly the normal topic's deck is executed."""
    course = _course(
        tmp_path,
        '<topic html="no">some_topic_from_test_1</topic><topic>another_topic_from_test_1</topic>',
    )

    ops = await _notebook_ops(course, HTML_SPEAKER_STAGE)
    implicit = [o for o in ops if o.is_implicit_execution]
    assert [Path(o.input_file.path).stem for o in implicit] == [
        "topic_110_another_topic_from_test_1"
    ]


async def test_html_no_topic_still_produces_its_non_html_outputs(tmp_path):
    """The fix removes only the execution, not the topic: its notebook
    output for the requested target is still scheduled (stage 1)."""
    course = _course(tmp_path, '<topic html="no">some_topic_from_test_1</topic>')

    all_ops = await _notebook_ops(course, stage=None)  # type: ignore[arg-type]
    assert all_ops, "the html='no' topic must still be built"
    assert {o.format for o in all_ops} == {"notebook"}
    assert not any(o.is_implicit_execution for o in all_ops)
