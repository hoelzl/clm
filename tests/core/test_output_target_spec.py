"""Tests for OutputTargetSpec class in course_spec.py."""

import io

import pytest

from clx.core.course_spec import (
    VALID_FORMATS,
    VALID_KINDS,
    VALID_LANGUAGES,
    CourseSpec,
    GitHubSpec,
    OutputTargetSpec,
)


class TestOutputTargetSpec:
    """Tests for OutputTargetSpec class."""

    def test_from_element_basic(self):
        """Test parsing a basic output target element."""
        from xml.etree import ElementTree as ET

        xml_str = """
        <output-target name="students">
            <path>./output/students</path>
        </output-target>
        """
        element = ET.fromstring(xml_str)
        target = OutputTargetSpec.from_element(element)

        assert target.name == "students"
        assert target.path == "./output/students"
        assert target.kinds is None  # Not specified = all
        assert target.formats is None  # Not specified = all
        assert target.languages is None  # Not specified = all

    def test_from_element_with_kinds(self):
        """Test parsing output target with kinds filter."""
        from xml.etree import ElementTree as ET

        xml_str = """
        <output-target name="solutions">
            <path>./output/solutions</path>
            <kinds>
                <kind>completed</kind>
                <kind>speaker</kind>
            </kinds>
        </output-target>
        """
        element = ET.fromstring(xml_str)
        target = OutputTargetSpec.from_element(element)

        assert target.name == "solutions"
        assert target.path == "./output/solutions"
        assert target.kinds == ["completed", "speaker"]
        assert target.formats is None
        assert target.languages is None

    def test_from_element_with_all_filters(self):
        """Test parsing output target with all filters specified."""
        from xml.etree import ElementTree as ET

        xml_str = """
        <output-target name="instructor">
            <path>./output/instructor</path>
            <kinds>
                <kind>speaker</kind>
            </kinds>
            <formats>
                <format>html</format>
                <format>notebook</format>
            </formats>
            <languages>
                <language>en</language>
            </languages>
        </output-target>
        """
        element = ET.fromstring(xml_str)
        target = OutputTargetSpec.from_element(element)

        assert target.name == "instructor"
        assert target.path == "./output/instructor"
        assert target.kinds == ["speaker"]
        assert target.formats == ["html", "notebook"]
        assert target.languages == ["en"]

    def test_validate_success(self):
        """Test validation of a valid target spec."""
        target = OutputTargetSpec(
            name="valid-target",
            path="./output",
            kinds=["code-along", "completed"],
            formats=["html", "notebook"],
            languages=["de", "en"],
        )

        errors = target.validate()
        assert errors == []

    def test_validate_invalid_kind(self):
        """Test validation catches invalid kind values."""
        target = OutputTargetSpec(
            name="test",
            path="./output",
            kinds=["completed", "invalid-kind"],
        )

        errors = target.validate()
        assert len(errors) == 1
        assert "Invalid kind 'invalid-kind'" in errors[0]
        assert "test" in errors[0]

    def test_validate_invalid_format(self):
        """Test validation catches invalid format values."""
        target = OutputTargetSpec(
            name="test",
            path="./output",
            formats=["html", "pdf"],  # pdf is not valid
        )

        errors = target.validate()
        assert len(errors) == 1
        assert "Invalid format 'pdf'" in errors[0]

    def test_validate_invalid_language(self):
        """Test validation catches invalid language values."""
        target = OutputTargetSpec(
            name="test",
            path="./output",
            languages=["en", "fr"],  # fr is not valid
        )

        errors = target.validate()
        assert len(errors) == 1
        assert "Invalid language 'fr'" in errors[0]

    def test_validate_missing_name(self):
        """Test validation catches missing name."""
        target = OutputTargetSpec(
            name="",
            path="./output",
        )

        errors = target.validate()
        assert len(errors) == 1
        assert "must have a name" in errors[0]

    def test_validate_missing_path(self):
        """Test validation catches missing path."""
        target = OutputTargetSpec(
            name="test",
            path="",
        )

        errors = target.validate()
        assert len(errors) == 1
        assert "must have a <path> element" in errors[0]


class TestCourseSpecOutputTargets:
    """Tests for CourseSpec output targets parsing and validation."""

    def test_parse_output_targets_none(self):
        """Test parsing spec without output targets."""
        xml_str = """
        <course>
            <name>
                <de>Test Kurs</de>
                <en>Test Course</en>
            </name>
            <prog-lang>python</prog-lang>
            <description><de>Beschreibung</de><en>Description</en></description>
            <certificate><de>Zertifikat</de><en>Certificate</en></certificate>
            <github><de>repo-de</de><en>repo-en</en></github>
            <sections></sections>
        </course>
        """
        spec = CourseSpec.from_file(io.StringIO(xml_str))

        assert spec.output_targets == []

    def test_parse_output_targets_single(self):
        """Test parsing spec with a single output target."""
        xml_str = """
        <course>
            <name>
                <de>Test Kurs</de>
                <en>Test Course</en>
            </name>
            <prog-lang>python</prog-lang>
            <description><de>Beschreibung</de><en>Description</en></description>
            <certificate><de>Zertifikat</de><en>Certificate</en></certificate>
            <github><de>repo-de</de><en>repo-en</en></github>
            <sections></sections>
            <output-targets>
                <output-target name="students">
                    <path>./output/students</path>
                    <kinds>
                        <kind>code-along</kind>
                    </kinds>
                </output-target>
            </output-targets>
        </course>
        """
        spec = CourseSpec.from_file(io.StringIO(xml_str))

        assert len(spec.output_targets) == 1
        assert spec.output_targets[0].name == "students"
        assert spec.output_targets[0].path == "./output/students"
        assert spec.output_targets[0].kinds == ["code-along"]

    def test_parse_output_targets_multiple(self):
        """Test parsing spec with multiple output targets."""
        xml_str = """
        <course>
            <name>
                <de>Test Kurs</de>
                <en>Test Course</en>
            </name>
            <prog-lang>python</prog-lang>
            <description><de>Beschreibung</de><en>Description</en></description>
            <certificate><de>Zertifikat</de><en>Certificate</en></certificate>
            <github><de>repo-de</de><en>repo-en</en></github>
            <sections></sections>
            <output-targets>
                <output-target name="students">
                    <path>./output/students</path>
                    <kinds><kind>code-along</kind></kinds>
                </output-target>
                <output-target name="solutions">
                    <path>./output/solutions</path>
                    <kinds><kind>completed</kind></kinds>
                </output-target>
                <output-target name="instructor">
                    <path>./output/instructor</path>
                    <kinds><kind>speaker</kind></kinds>
                </output-target>
            </output-targets>
        </course>
        """
        spec = CourseSpec.from_file(io.StringIO(xml_str))

        assert len(spec.output_targets) == 3
        assert spec.output_targets[0].name == "students"
        assert spec.output_targets[1].name == "solutions"
        assert spec.output_targets[2].name == "instructor"

    def test_validate_duplicate_names(self):
        """Test validation catches duplicate target names."""
        spec = CourseSpec(
            name={"de": "Test", "en": "Test"},
            prog_lang="python",
            description={"de": "Desc", "en": "Desc"},
            certificate={"de": "Cert", "en": "Cert"},
            sections=[],
            github=GitHubSpec(),
            output_targets=[
                OutputTargetSpec(name="duplicate", path="./output1"),
                OutputTargetSpec(name="duplicate", path="./output2"),
            ],
        )

        errors = spec.validate()
        assert any("Duplicate output target name" in e for e in errors)

    def test_validate_duplicate_paths(self):
        """Test validation catches duplicate target paths."""
        spec = CourseSpec(
            name={"de": "Test", "en": "Test"},
            prog_lang="python",
            description={"de": "Desc", "en": "Desc"},
            certificate={"de": "Cert", "en": "Cert"},
            sections=[],
            github=GitHubSpec(),
            output_targets=[
                OutputTargetSpec(name="target1", path="./same/path"),
                OutputTargetSpec(name="target2", path="./same/path"),
            ],
        )

        errors = spec.validate()
        assert any("Duplicate output target path" in e for e in errors)


class TestValidConstants:
    """Test the valid values constants."""

    def test_valid_kinds(self):
        """Test VALID_KINDS contains expected values."""
        assert VALID_KINDS == frozenset({"code-along", "completed", "speaker"})

    def test_valid_formats(self):
        """Test VALID_FORMATS contains expected values."""
        assert VALID_FORMATS == frozenset({"html", "notebook", "code"})

    def test_valid_languages(self):
        """Test VALID_LANGUAGES contains expected values."""
        assert VALID_LANGUAGES == frozenset({"de", "en"})
