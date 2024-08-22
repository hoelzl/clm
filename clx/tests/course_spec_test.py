import io

from clx.course_spec import CourseSpec, TopicSpec, parse_multilang
from clx.utils.text_utils import Text

from conftest import COURSE_1_XML, course_1_xml


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
    dict_groups = CourseSpec.parse_dict_groups(course_1_xml)
    assert len(dict_groups) == 3

    assert dict_groups[0].name == Text(de='Code/Solutions', en='Code/Solutions')
    assert dict_groups[0].path == "code/solutions"
    assert dict_groups[0].subdirs == ["Example_1", "Example_3"]

    assert dict_groups[1].name == Text(de="Bonus", en="Bonus")
    assert dict_groups[1].path == "div/workshops"
    assert dict_groups[1].subdirs == []

    assert dict_groups[2].name == Text(de="", en="")
    assert dict_groups[2].path == "root-files"
    assert dict_groups[2].subdirs == []


def test_from_file():
    xml_stream = io.StringIO(COURSE_1_XML)
    course = CourseSpec.from_file(xml_stream)
    assert course.name == Text(de="Mein Kurs", en="My Course")
    assert course.prog_lang == "python"
    assert course.description == Text(
        de="Ein Kurs über ein Thema", en="A course about a topic"
    )
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
    assert course.dictionaries[0].name == Text(de='Code/Solutions', en='Code/Solutions')
    assert course.dictionaries[1].name == Text(de="Bonus", en="Bonus")
    assert course.dictionaries[2].name == Text(de="", en="")
