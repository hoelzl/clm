import io

from clx.core.course_spec import CourseSpec, TopicSpec, parse_multilang
from clx.core.utils.text_utils import Text

# COURSE_1_XML is a module-level constant defined in tests/conftest.py
# We need to import it here for test_from_file()
# course_1_xml is a fixture that will be injected by pytest when used as test parameters

# Copy of COURSE_1_XML from conftest.py
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
                    <dir-group>
                        <name>Code/Solutions</name>
                        <path>code/solutions</path>
                        <subdirs>
                            <subdir>Example_1</subdir>
                            <subdir>Example_3</subdir>
                        </subdirs>
                    </dir-group>
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
    <dir-groups>
        <dir-group>
            <name>Bonus</name>
            <path>div/workshops</path>
        </dir-group>
        <!-- We can have an empty name to copy files into the course root -->
        <dir-group>
            <name/>
            <path>root-files</path>
        </dir-group>
    </dir-groups>
</course>
"""


def test_parse_multilang(course_1_xml):
    assert parse_multilang(course_1_xml, "name") == Text(de="Mein Kurs", en="My Course")
    assert parse_multilang(course_1_xml, "github") == Text(
        de="https://github.com/hoelzl/my-course-de",
        en="https://github.com/hoelzl/my-course-en",
    )


def test_parse_sections(course_1_xml):
    sections = CourseSpec.parse_sections(course_1_xml)
    assert len(sections) == 2
    assert sections[0].name == Text(de="Woche 1", en="Week 1")
    assert sections[0].topics == [
        TopicSpec(id="some_topic_from_test_1"),
        TopicSpec(id="a_topic_from_test_2"),
    ]
    assert sections[1].name == Text(de="Woche 2", en="Week 2")
    assert sections[1].topics == [TopicSpec("another_topic_from_test_1")]


def test_parse_dictionaries(course_1_xml):
    dir_groups = CourseSpec.parse_dir_groups(course_1_xml)
    assert len(dir_groups) == 3

    assert dir_groups[0].name == Text(de="Code/Solutions", en="Code/Solutions")
    assert dir_groups[0].path == "code/solutions"
    assert dir_groups[0].subdirs == ["Example_1", "Example_3"]

    assert dir_groups[1].name == Text(de="Bonus", en="Bonus")
    assert dir_groups[1].path == "div/workshops"
    assert dir_groups[1].subdirs == []

    assert dir_groups[2].name == Text(de="", en="")
    assert dir_groups[2].path == "root-files"
    assert dir_groups[2].subdirs == []


def test_from_file():
    xml_stream = io.StringIO(COURSE_1_XML)
    course = CourseSpec.from_file(xml_stream)
    assert course.name == Text(de="Mein Kurs", en="My Course")
    assert course.prog_lang == "python"
    assert course.description == Text(de="Ein Kurs über ein Thema", en="A course about a topic")
    assert course.sections[0].name == Text(de="Woche 1", en="Week 1")
    assert course.sections[0].topics == [
        TopicSpec(id="some_topic_from_test_1"),
        TopicSpec(id="a_topic_from_test_2"),
    ]
    assert course.sections[1].name == Text(de="Woche 2", en="Week 2")
    assert course.sections[1].topics == [TopicSpec(id="another_topic_from_test_1")]
    assert course.github_repo == Text(
        de="https://github.com/hoelzl/my-course-de",
        en="https://github.com/hoelzl/my-course-en",
    )
    assert len(course.dictionaries) == 3
    assert course.dictionaries[0].name == Text(de="Code/Solutions", en="Code/Solutions")
    assert course.dictionaries[1].name == Text(de="Bonus", en="Bonus")
    assert course.dictionaries[2].name == Text(de="", en="")
