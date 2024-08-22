import io
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ETree

import pytest

from clx.utils.text_utils import Text

if TYPE_CHECKING:
    from clx.course import Course
    from clx.section import Section
    from clx.topic import Topic

COURSE_1_XML = """
<course>
    <github>
        <de>https://github.com/hoelzl/my-course-de</de>
        <en>https://github.com/hoelzl/my-course-en</en>
    </github>
    <name>
        <de>Mein Kurs</de>
        <en>My Course</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Ein Kurs über ein Thema</de>
        <en>A course about a topic</en>
    </description>
    <certificate>
        <de>...</de>
        <en>...</en>
    </certificate>
    <sections>
        <section>
            <name>
                <de>Woche 1</de>
                <en>Week 1</en>
            </name>
            <topics>
                <topic>
                    some_topic_from_test_1
                    <dict-group>
                        <name>Code/Solutions</name>
                        <path>code/solutions</path>
                        <subdirs>
                            <subdir>Example_1</subdir>
                            <subdir>Example_3</subdir>
                        </subdirs>
                    </dict-group>
                </topic>
                <topic>a_topic_from_test_2</topic>
            </topics>
        </section>
        <section>
            <name>
                <de>Woche 2</de>
                <en>Week 2</en>
            </name>
            <topics>
                <topic>another_topic_from_test_1</topic>
            </topics>
        </section>
    </sections>
    <dict-groups>
        <dict-group>
            <name>Bonus</name>
            <path>div/workshops</path>
        </dict-group>
        <!-- We can have an empty name to copy files into the course root -->
        <dict-group>
            <name/>
            <path>root-files</path>
        </dict-group>
    </dict-groups>
</course>
"""

COURSE_2_XML = """
<course>
    <github>
        <de>https://github.com/hoelzl/my-course-de</de>
        <en>https://github.com/hoelzl/my-course-en</en>
    </github>
    <name>
        <de>Kurs 2</de>
        <en>Kurs 2</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Der zweite Kurs</de>
        <en>The second course</en>
    </description>
    <certificate>
        <de>...</de>
        <en>...</en>
    </certificate>
    <sections>
        <section>
            <name>
                <de>Woche 1</de>
                <en>Week 1</en>
            </name>
            <topics>
                <topic>slides_in_test_3</topic>
            </topics>
        </section>
    </sections>
</course>
"""


DATA_DIR = Path(__file__).parent / "data"
OUTPUT_DIR = Path(__file__).parent / "output"


@pytest.fixture
def course_1_xml():
    return ETree.fromstring(COURSE_1_XML)


@pytest.fixture
def course_2_xml():
    return ETree.fromstring(COURSE_2_XML)


@pytest.fixture
def course_1_spec():
    from clx.course_spec import CourseSpec

    xml_stream = io.StringIO(COURSE_1_XML)

    return CourseSpec.from_file(xml_stream)


@pytest.fixture
def course_2_spec():
    from clx.course_spec import CourseSpec

    xml_stream = io.StringIO(COURSE_2_XML)

    return CourseSpec.from_file(xml_stream)


@pytest.fixture
def course_1(course_1_spec):
    from clx.course import Course

    course = Course(course_1_spec, DATA_DIR, Path("/output"))
    return course


@pytest.fixture
def course_2(course_2_spec):
    from clx.course import Course

    course = Course(course_2_spec, DATA_DIR, Path("/output"))
    return course


@pytest.fixture
def section_1(course_1):
    from clx.course import Section

    return Section(name=Text(en="Week 1", de="Woche 1"), course=course_1)


@pytest.fixture
def topic_1(section_1):
    from clx.course import Topic

    path = DATA_DIR / "slides/module_000_test_1/topic_100_some_topic_from_test_1"
    return Topic.from_id(id="some_topic", section=section_1, path=path)
