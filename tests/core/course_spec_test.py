import io
import logging

import pytest

from clm.core.course_spec import (
    CourseSpec,
    CourseSpecError,
    GitHubSpec,
    TopicSpec,
    parse_multilang,
)
from clm.core.utils.text_utils import Text

# COURSE_1_XML is a module-level constant defined in tests/conftest.py
# We need to import it here for test_from_file()
# course_1_xml is a fixture that will be injected by pytest when used as test parameters

# Copy of COURSE_1_XML from conftest.py
COURSE_1_XML = """
<course>
    <github>
        <project-slug>my-course</project-slug>
        <repository-base>https://github.com/hoelzl</repository-base>
        <include-speaker>false</include-speaker>
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


def test_parse_topic_with_prog_lang_attribute():
    """Test that prog-lang attribute on <topic> is parsed into TopicSpec."""
    from xml.etree import ElementTree as ETree

    xml = """
    <course>
        <name><de>Test</de><en>Test</en></name>
        <prog-lang>python</prog-lang>
        <description><de></de><en></en></description>
        <certificate><de></de><en></en></certificate>
        <sections>
            <section>
                <name><de>S1</de><en>S1</en></name>
                <topics>
                    <topic prog-lang="java">my_topic</topic>
                    <topic>other_topic</topic>
                </topics>
            </section>
        </sections>
    </course>
    """
    root = ETree.fromstring(xml)
    sections = CourseSpec.parse_sections(root)
    assert sections[0].topics[0].prog_lang == "java"
    assert sections[0].topics[1].prog_lang == ""


def test_parse_dictionaries(course_1_xml):
    dir_groups = CourseSpec.parse_dir_groups(course_1_xml)
    assert len(dir_groups) == 3

    assert dir_groups[0].name == Text(de="Code/Solutions", en="Code/Solutions")
    assert dir_groups[0].path == "code/solutions"
    assert dir_groups[0].subdirs == ["Example_1", "Example_3"]
    assert dir_groups[0].include_root_files is False

    assert dir_groups[1].name == Text(de="Bonus", en="Bonus")
    assert dir_groups[1].path == "div/workshops"
    assert dir_groups[1].subdirs == []
    assert dir_groups[1].include_root_files is False

    assert dir_groups[2].name == Text(de="", en="")
    assert dir_groups[2].path == "root-files"
    assert dir_groups[2].subdirs == []
    assert dir_groups[2].include_root_files is False


def test_parse_dir_group_with_include_root_files():
    """Test parsing include-root-files attribute on dir-group."""
    from xml.etree import ElementTree as ETree

    from clm.core.course_spec import DirGroupSpec

    # Test with include-root-files="true"
    xml_with_attr = """
    <dir-group include-root-files="true">
        <name>Code/Completed</name>
        <path>code/completed</path>
        <subdirs>
            <subdir>Example_1</subdir>
            <subdir>Example_2</subdir>
        </subdirs>
    </dir-group>
    """
    element = ETree.fromstring(xml_with_attr)
    spec = DirGroupSpec.from_element(element)
    assert spec.include_root_files is True
    assert spec.subdirs == ["Example_1", "Example_2"]
    assert spec.path == "code/completed"

    # Test with include-root-files="false"
    xml_false = """
    <dir-group include-root-files="false">
        <name>Code/Completed</name>
        <path>code/completed</path>
    </dir-group>
    """
    element_false = ETree.fromstring(xml_false)
    spec_false = DirGroupSpec.from_element(element_false)
    assert spec_false.include_root_files is False

    # Test without attribute (default should be False)
    xml_no_attr = """
    <dir-group>
        <name>Code/Completed</name>
        <path>code/completed</path>
    </dir-group>
    """
    element_no_attr = ETree.fromstring(xml_no_attr)
    spec_no_attr = DirGroupSpec.from_element(element_no_attr)
    assert spec_no_attr.include_root_files is False


def test_parse_dir_group_with_recursive_attribute():
    """Test parsing recursive attribute on dir-group."""
    from xml.etree import ElementTree as ETree

    from clm.core.course_spec import DirGroupSpec

    # Test with recursive="false"
    xml_false = """
    <dir-group recursive="false">
        <name>Code</name>
        <path>code</path>
    </dir-group>
    """
    element = ETree.fromstring(xml_false)
    spec = DirGroupSpec.from_element(element)
    assert spec.recursive is False

    # Test with recursive="true"
    xml_true = """
    <dir-group recursive="true">
        <name>Code</name>
        <path>code</path>
    </dir-group>
    """
    element_true = ETree.fromstring(xml_true)
    spec_true = DirGroupSpec.from_element(element_true)
    assert spec_true.recursive is True

    # Test without attribute (default should be True)
    xml_no_attr = """
    <dir-group>
        <name>Code</name>
        <path>code</path>
    </dir-group>
    """
    element_no_attr = ETree.fromstring(xml_no_attr)
    spec_no_attr = DirGroupSpec.from_element(element_no_attr)
    assert spec_no_attr.recursive is True

    # Test combined with include-root-files
    xml_combined = """
    <dir-group include-root-files="true" recursive="false">
        <name>Code</name>
        <path>code</path>
    </dir-group>
    """
    element_combined = ETree.fromstring(xml_combined)
    spec_combined = DirGroupSpec.from_element(element_combined)
    assert spec_combined.include_root_files is True
    assert spec_combined.recursive is False


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
    # project_slug is promoted to CourseSpec level (from github section, with deprecation warning)
    assert course.project_slug == "my-course"
    assert course.github.project_slug == "my-course"
    assert course.github.repository_base == "https://github.com/hoelzl"
    assert course.github.include_speaker is False
    assert course.github.is_configured
    assert len(course.dictionaries) == 3
    assert course.dictionaries[0].name == Text(de="Code/Solutions", en="Code/Solutions")
    assert course.dictionaries[1].name == Text(de="Bonus", en="Bonus")
    assert course.dictionaries[2].name == Text(de="", en="")


class TestGitHubSpecDeriveDirName:
    """Tests for GitHubSpec.derive_dir_name."""

    def test_derive_dir_name_with_slug(self):
        spec = GitHubSpec(project_slug="ml-course", repository_base="https://github.com/Org")
        assert spec.derive_dir_name("de") == "ml-course-de"
        assert spec.derive_dir_name("en") == "ml-course-en"

    def test_derive_dir_name_without_slug(self):
        spec = GitHubSpec()
        assert spec.derive_dir_name("de") is None
        assert spec.derive_dir_name("en") is None


class TestCourseSpecOutputDirName:
    """Tests for CourseSpec.output_dir_name property."""

    def test_output_dir_name_with_github_slug(self):
        """When github project-slug is configured, use it for dir names."""
        xml_stream = io.StringIO(COURSE_1_XML)
        spec = CourseSpec.from_file(xml_stream)
        assert spec.output_dir_name == Text(de="my-course-de", en="my-course-en")

    def test_output_dir_name_fallback_without_slug(self):
        """Without github project-slug, fall back to sanitized course name."""
        xml = """<?xml version="1.0"?>
<course>
    <name>
        <de>Mein Kurs (AZAV)</de>
        <en>My Course (AZAV)</en>
    </name>
    <prog-lang>python</prog-lang>
</course>"""
        spec = CourseSpec.from_file(io.StringIO(xml))
        assert spec.output_dir_name == Text(
            de="Mein Kurs (AZAV)-de",
            en="My Course (AZAV)-en",
        )


class TestProjectSlugResolution:
    """Tests for project-slug resolution from top-level vs github section."""

    def test_top_level_slug_only(self):
        """Top-level project-slug is used when no github slug exists."""
        xml = """<?xml version="1.0"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <project-slug>my-top-level-slug</project-slug>
    <github>
        <repository-base>https://github.com/Org</repository-base>
    </github>
</course>"""
        spec = CourseSpec.from_file(io.StringIO(xml))
        assert spec.project_slug == "my-top-level-slug"
        assert spec.output_dir_name == Text(de="my-top-level-slug-de", en="my-top-level-slug-en")

    def test_github_slug_deprecated(self, caplog):
        """Slug only in <github> works but logs a deprecation warning."""
        xml = """<?xml version="1.0"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <github>
        <project-slug>github-only-slug</project-slug>
        <repository-base>https://github.com/Org</repository-base>
    </github>
</course>"""
        with caplog.at_level(logging.WARNING, logger="clm.core.course_spec"):
            spec = CourseSpec.from_file(io.StringIO(xml))

        assert spec.project_slug == "github-only-slug"
        assert spec.output_dir_name == Text(de="github-only-slug-de", en="github-only-slug-en")
        assert any("deprecated" in record.message.lower() for record in caplog.records)

    def test_both_slugs_top_level_wins(self, caplog):
        """When both locations have a slug, top-level wins with a warning."""
        xml = """<?xml version="1.0"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <project-slug>top-level-slug</project-slug>
    <github>
        <project-slug>github-slug</project-slug>
        <repository-base>https://github.com/Org</repository-base>
    </github>
</course>"""
        with caplog.at_level(logging.WARNING, logger="clm.core.course_spec"):
            spec = CourseSpec.from_file(io.StringIO(xml))

        assert spec.project_slug == "top-level-slug"
        assert spec.output_dir_name == Text(de="top-level-slug-de", en="top-level-slug-en")
        assert any("ignored" in record.message.lower() for record in caplog.records)

    def test_no_slug_fallback(self):
        """Without any slug, fall back to sanitized course name."""
        xml = """<?xml version="1.0"?>
<course>
    <name><de>Mein Kurs</de><en>My Course</en></name>
    <prog-lang>python</prog-lang>
</course>"""
        spec = CourseSpec.from_file(io.StringIO(xml))
        assert spec.project_slug is None
        assert spec.output_dir_name == Text(de="Mein Kurs-de", en="My Course-en")

    def test_top_level_slug_used_for_remote_url(self):
        """Top-level slug is passed through to derive_remote_url."""
        xml = """<?xml version="1.0"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <project-slug>top-slug</project-slug>
    <github>
        <repository-base>https://github.com/Org</repository-base>
    </github>
</course>"""
        spec = CourseSpec.from_file(io.StringIO(xml))
        url = spec.github.derive_remote_url("public", "de", project_slug=spec.project_slug)
        assert url == "https://github.com/Org/top-slug-de"


class TestAuthorAndOrganization:
    """Tests for author and organization parsing."""

    def test_defaults_when_not_specified(self):
        """Without <author>/<organization>, defaults are used."""
        xml = """<?xml version="1.0"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
</course>"""
        spec = CourseSpec.from_file(io.StringIO(xml))
        assert spec.author == "Dr. Matthias Hölzl"
        assert spec.organization == Text(de="Coding-Akademie München", en="Coding-Academy Munich")

    def test_custom_author(self):
        """Custom <author> element overrides default."""
        xml = """<?xml version="1.0"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <author>Dr. Jane Smith</author>
</course>"""
        spec = CourseSpec.from_file(io.StringIO(xml))
        assert spec.author == "Dr. Jane Smith"

    def test_custom_organization(self):
        """Custom <organization> element overrides default."""
        xml = """<?xml version="1.0"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <organization>
        <de>Meine Akademie</de>
        <en>My Academy</en>
    </organization>
</course>"""
        spec = CourseSpec.from_file(io.StringIO(xml))
        assert spec.organization == Text(de="Meine Akademie", en="My Academy")

    def test_topic_level_author(self):
        """Topic-level author attribute is parsed."""
        xml = """<?xml version="1.0"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <author>Dr. Jane Smith</author>
    <sections>
        <section>
            <name><de>Woche 1</de><en>Week 1</en></name>
            <topics>
                <topic author="Prof. Bob Expert">special_topic</topic>
                <topic>normal_topic</topic>
            </topics>
        </section>
    </sections>
</course>"""
        spec = CourseSpec.from_file(io.StringIO(xml))
        assert spec.author == "Dr. Jane Smith"
        topics = spec.sections[0].topics
        assert topics[0].author == "Prof. Bob Expert"
        assert topics[1].author == ""

    def test_existing_xml_has_default_author(self):
        """Existing COURSE_1_XML (no <author>) gets default author."""
        spec = CourseSpec.from_file(io.StringIO(COURSE_1_XML))
        assert spec.author == "Dr. Matthias Hölzl"
        assert spec.organization.de == "Coding-Akademie München"
        assert spec.organization.en == "Coding-Academy Munich"


class TestSectionEnabledAndId:
    """Tests for the section `enabled` and `id` attributes (phase 1 of
    section filtering)."""

    @staticmethod
    def _parse(xml: str, *, keep_disabled: bool = False):
        from xml.etree import ElementTree as ETree

        root = ETree.fromstring(xml)
        return CourseSpec.parse_sections(root, keep_disabled=keep_disabled)

    def test_default_enabled_is_true(self):
        """Sections without an `enabled` attribute default to enabled."""
        xml = """
        <course>
            <sections>
                <section>
                    <name><de>Woche 1</de><en>Week 1</en></name>
                    <topics>
                        <topic>t1</topic>
                    </topics>
                </section>
            </sections>
        </course>
        """
        sections = self._parse(xml)
        assert len(sections) == 1
        assert sections[0].enabled is True
        assert sections[0].id is None

    def test_enabled_true_is_kept(self):
        """An explicit `enabled="true"` is kept and parses as enabled."""
        xml = """
        <course>
            <sections>
                <section enabled="true">
                    <name><de>Woche 1</de><en>Week 1</en></name>
                    <topics><topic>t1</topic></topics>
                </section>
            </sections>
        </course>
        """
        sections = self._parse(xml)
        assert len(sections) == 1
        assert sections[0].enabled is True

    def test_disabled_section_dropped_by_default(self):
        """`enabled="false"` drops the section by default."""
        xml = """
        <course>
            <sections>
                <section>
                    <name><de>W1</de><en>W1</en></name>
                    <topics><topic>t1</topic></topics>
                </section>
                <section enabled="false">
                    <name><de>W2</de><en>W2</en></name>
                    <topics><topic>t2</topic></topics>
                </section>
                <section>
                    <name><de>W3</de><en>W3</en></name>
                    <topics><topic>t3</topic></topics>
                </section>
            </sections>
        </course>
        """
        sections = self._parse(xml)
        assert [s.name.en for s in sections] == ["W1", "W3"]
        assert all(s.enabled for s in sections)

    def test_keep_disabled_retains_sections(self):
        """`keep_disabled=True` retains disabled sections with `enabled=False`."""
        xml = """
        <course>
            <sections>
                <section>
                    <name><de>W1</de><en>W1</en></name>
                    <topics><topic>t1</topic></topics>
                </section>
                <section enabled="false">
                    <name><de>W2</de><en>W2</en></name>
                    <topics><topic>t2</topic></topics>
                </section>
            </sections>
        </course>
        """
        sections = self._parse(xml, keep_disabled=True)
        assert [s.name.en for s in sections] == ["W1", "W2"]
        assert sections[0].enabled is True
        assert sections[1].enabled is False

    def test_enabled_case_insensitive(self):
        """`enabled` values are matched case-insensitively."""
        xml = """
        <course>
            <sections>
                <section enabled="TRUE">
                    <name><de>A</de><en>A</en></name>
                    <topics><topic>t1</topic></topics>
                </section>
                <section enabled="False">
                    <name><de>B</de><en>B</en></name>
                    <topics><topic>t2</topic></topics>
                </section>
                <section enabled="fAlSe">
                    <name><de>C</de><en>C</en></name>
                    <topics><topic>t3</topic></topics>
                </section>
            </sections>
        </course>
        """
        sections = self._parse(xml, keep_disabled=True)
        assert [s.enabled for s in sections] == [True, False, False]

    def test_enabled_invalid_value_raises(self):
        """Invalid `enabled` values raise CourseSpecError with helpful text."""
        xml = """
        <course>
            <sections>
                <section enabled="maybe">
                    <name><de>W1</de><en>W1</en></name>
                    <topics><topic>t1</topic></topics>
                </section>
            </sections>
        </course>
        """
        with pytest.raises(CourseSpecError) as exc_info:
            self._parse(xml)
        message = str(exc_info.value)
        assert "enabled" in message
        assert "maybe" in message
        assert "true" in message.lower() and "false" in message.lower()

    def test_ordering_preserved_with_disabled_section(self):
        """Removing a disabled section from the middle keeps declared order."""
        xml = """
        <course>
            <sections>
                <section id="w01">
                    <name><de>W1</de><en>W1</en></name>
                    <topics><topic>t1</topic></topics>
                </section>
                <section id="w02" enabled="false">
                    <name><de>W2</de><en>W2</en></name>
                    <topics><topic>t2</topic></topics>
                </section>
                <section id="w03">
                    <name><de>W3</de><en>W3</en></name>
                    <topics><topic>t3</topic></topics>
                </section>
            </sections>
        </course>
        """
        sections = self._parse(xml)
        assert [s.id for s in sections] == ["w01", "w03"]

    def test_disabled_section_without_topics_element(self):
        """A disabled section may omit the <topics> element entirely."""
        xml = """
        <course>
            <sections>
                <section enabled="false">
                    <name><de>Roadmap</de><en>Roadmap</en></name>
                </section>
                <section>
                    <name><de>Live</de><en>Live</en></name>
                    <topics><topic>t1</topic></topics>
                </section>
            </sections>
        </course>
        """
        # Default: disabled section is dropped, no warning/error.
        sections = self._parse(xml)
        assert [s.name.en for s in sections] == ["Live"]

        # keep_disabled: retained with an empty topic list.
        kept = self._parse(xml, keep_disabled=True)
        assert [s.name.en for s in kept] == ["Roadmap", "Live"]
        assert kept[0].enabled is False
        assert kept[0].topics == []

    def test_disabled_section_with_nonexistent_topics_parses(self):
        """A disabled section's topics are never validated at parse time."""
        xml = """
        <course>
            <sections>
                <section enabled="false">
                    <name><de>Future</de><en>Future</en></name>
                    <topics>
                        <topic>does_not_exist_yet</topic>
                        <topic>also_missing</topic>
                    </topics>
                </section>
            </sections>
        </course>
        """
        # Parsing must not raise; at this level the parser does not touch
        # the filesystem to check topic existence.
        sections = self._parse(xml)
        assert sections == []

        # With keep_disabled, the section comes back with the raw topic ids.
        kept = self._parse(xml, keep_disabled=True)
        assert len(kept) == 1
        assert [t.id for t in kept[0].topics] == [
            "does_not_exist_yet",
            "also_missing",
        ]

    def test_section_id_round_trip(self):
        """`id` attribute round-trips into SectionSpec."""
        xml = """
        <course>
            <sections>
                <section id="w03">
                    <name><de>Woche 3</de><en>Week 3</en></name>
                    <topics><topic>t1</topic></topics>
                </section>
                <section>
                    <name><de>Woche 4</de><en>Week 4</en></name>
                    <topics><topic>t2</topic></topics>
                </section>
            </sections>
        </course>
        """
        sections = self._parse(xml)
        assert sections[0].id == "w03"
        assert sections[1].id is None

    def test_enabled_malformed_value_roundtrip_from_file(self):
        """Invalid `enabled` values fail the full CourseSpec.from_file path."""
        xml = """<?xml version="1.0"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <description><de></de><en></en></description>
    <certificate><de></de><en></en></certificate>
    <sections>
        <section enabled="yes">
            <name><de>W1</de><en>W1</en></name>
            <topics><topic>t1</topic></topics>
        </section>
    </sections>
</course>"""
        with pytest.raises(CourseSpecError):
            CourseSpec.from_file(io.StringIO(xml))
