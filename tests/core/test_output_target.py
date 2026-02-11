"""Tests for OutputTarget runtime class."""

from pathlib import Path

import pytest

from clm.core.course_spec import OutputTargetSpec
from clm.core.output_target import (
    ALL_FORMATS,
    ALL_KINDS,
    ALL_LANGUAGES,
    OutputTarget,
)


class TestOutputTargetConstants:
    """Tests for OutputTarget constants."""

    def test_all_kinds(self):
        """Test ALL_KINDS contains all valid kinds."""
        assert ALL_KINDS == frozenset({"code-along", "completed", "speaker"})

    def test_all_formats(self):
        """Test ALL_FORMATS contains all valid formats."""
        assert ALL_FORMATS == frozenset({"html", "notebook", "code"})

    def test_all_languages(self):
        """Test ALL_LANGUAGES contains all valid languages."""
        assert ALL_LANGUAGES == frozenset({"de", "en"})


class TestOutputTargetFromSpec:
    """Tests for OutputTarget.from_spec() factory method."""

    def test_from_spec_basic(self, tmp_path):
        """Test creating OutputTarget from basic spec."""
        spec = OutputTargetSpec(name="test", path="./output")
        target = OutputTarget.from_spec(spec, tmp_path)

        assert target.name == "test"
        assert target.output_root == (tmp_path / "output").resolve()
        assert target.kinds == ALL_KINDS
        assert target.formats == ALL_FORMATS
        assert target.languages == ALL_LANGUAGES

    def test_from_spec_with_filters(self, tmp_path):
        """Test creating OutputTarget from spec with filters."""
        spec = OutputTargetSpec(
            name="filtered",
            path="./out",
            kinds=["completed"],
            formats=["html", "notebook"],
            languages=["en"],
        )
        target = OutputTarget.from_spec(spec, tmp_path)

        assert target.name == "filtered"
        assert target.kinds == frozenset({"completed"})
        assert target.formats == frozenset({"html", "notebook"})
        assert target.languages == frozenset({"en"})

    def test_from_spec_absolute_path(self, tmp_path):
        """Test creating OutputTarget with absolute path."""
        abs_path = tmp_path / "absolute_output"
        spec = OutputTargetSpec(name="abs", path=str(abs_path))
        target = OutputTarget.from_spec(spec, tmp_path)

        assert target.output_root == abs_path.resolve()


class TestOutputTargetDefaultTarget:
    """Tests for OutputTarget.default_target() factory method."""

    def test_default_target(self, tmp_path):
        """Test creating default target."""
        output_root = tmp_path / "output"
        target = OutputTarget.default_target(output_root)

        assert target.name == "default"
        assert target.output_root == output_root.resolve()
        assert target.kinds == ALL_KINDS
        assert target.formats == ALL_FORMATS
        assert target.languages == ALL_LANGUAGES


class TestOutputTargetFiltering:
    """Tests for OutputTarget filtering methods."""

    @pytest.fixture
    def full_target(self, tmp_path):
        """Create a target with all kinds/formats/languages."""
        return OutputTarget(
            name="full",
            output_root=tmp_path / "output",
            kinds=ALL_KINDS,
            formats=ALL_FORMATS,
            languages=ALL_LANGUAGES,
        )

    @pytest.fixture
    def filtered_target(self, tmp_path):
        """Create a filtered target."""
        return OutputTarget(
            name="filtered",
            output_root=tmp_path / "output",
            kinds=frozenset({"completed"}),
            formats=frozenset({"html", "notebook"}),
            languages=frozenset({"en"}),
        )

    def test_includes_kind(self, full_target, filtered_target):
        """Test includes_kind() method."""
        assert full_target.includes_kind("code-along")
        assert full_target.includes_kind("completed")
        assert full_target.includes_kind("speaker")

        assert not filtered_target.includes_kind("code-along")
        assert filtered_target.includes_kind("completed")
        assert not filtered_target.includes_kind("speaker")

    def test_includes_format(self, full_target, filtered_target):
        """Test includes_format() method."""
        assert full_target.includes_format("html")
        assert full_target.includes_format("notebook")
        assert full_target.includes_format("code")

        assert filtered_target.includes_format("html")
        assert filtered_target.includes_format("notebook")
        assert not filtered_target.includes_format("code")

    def test_includes_language(self, full_target, filtered_target):
        """Test includes_language() method."""
        assert full_target.includes_language("de")
        assert full_target.includes_language("en")

        assert not filtered_target.includes_language("de")
        assert filtered_target.includes_language("en")

    def test_should_generate(self, full_target, filtered_target):
        """Test should_generate() method."""
        # Full target should generate all combinations
        assert full_target.should_generate("de", "html", "code-along")
        assert full_target.should_generate("en", "code", "speaker")

        # Filtered target should only generate matching combinations
        assert filtered_target.should_generate("en", "html", "completed")
        assert not filtered_target.should_generate("de", "html", "completed")  # wrong lang
        assert not filtered_target.should_generate("en", "code", "completed")  # wrong format
        assert not filtered_target.should_generate("en", "html", "speaker")  # wrong kind


class TestOutputTargetWithCliFilters:
    """Tests for OutputTarget.with_cli_filters() method."""

    def test_with_cli_filters_language(self, tmp_path):
        """Test applying language filter."""
        target = OutputTarget(
            name="test",
            output_root=tmp_path / "output",
            kinds=ALL_KINDS,
            formats=ALL_FORMATS,
            languages=frozenset({"de", "en"}),
        )

        filtered = target.with_cli_filters(languages=["en"], kinds=None)

        assert filtered.languages == frozenset({"en"})
        assert filtered.kinds == ALL_KINDS  # unchanged
        assert filtered.formats == ALL_FORMATS  # unchanged
        assert filtered.output_root == target.output_root  # unchanged

    def test_with_cli_filters_kinds(self, tmp_path):
        """Test applying kinds filter."""
        target = OutputTarget(
            name="test",
            output_root=tmp_path / "output",
            kinds=ALL_KINDS,
            formats=ALL_FORMATS,
            languages=ALL_LANGUAGES,
        )

        filtered = target.with_cli_filters(languages=None, kinds=["speaker"])

        assert filtered.kinds == frozenset({"speaker"})
        assert filtered.languages == ALL_LANGUAGES  # unchanged

    def test_with_cli_filters_intersection(self, tmp_path):
        """Test CLI filter intersects with target filter."""
        # Target only includes completed and speaker
        target = OutputTarget(
            name="test",
            output_root=tmp_path / "output",
            kinds=frozenset({"completed", "speaker"}),
            formats=ALL_FORMATS,
            languages=ALL_LANGUAGES,
        )

        # CLI requests code-along and completed
        filtered = target.with_cli_filters(languages=None, kinds=["code-along", "completed"])

        # Intersection should only be completed
        assert filtered.kinds == frozenset({"completed"})

    def test_with_cli_filters_none_preserves_original(self, tmp_path):
        """Test that None filters preserve original values."""
        target = OutputTarget(
            name="test",
            output_root=tmp_path / "output",
            kinds=frozenset({"completed"}),
            formats=frozenset({"html"}),
            languages=frozenset({"en"}),
        )

        filtered = target.with_cli_filters(languages=None, kinds=None)

        assert filtered.kinds == target.kinds
        assert filtered.languages == target.languages


class TestOutputTargetExplicitFlag:
    """Tests for OutputTarget.is_explicit flag."""

    def test_default_target_is_not_explicit(self, tmp_path):
        """Test that default targets have is_explicit=False."""
        target = OutputTarget.default_target(tmp_path / "output")
        assert target.is_explicit is False

    def test_from_spec_target_is_explicit(self, tmp_path):
        """Test that targets from spec have is_explicit=True."""
        spec = OutputTargetSpec(name="test", path="./output")
        target = OutputTarget.from_spec(spec, tmp_path)
        assert target.is_explicit is True

    def test_with_cli_filters_preserves_explicit_flag(self, tmp_path):
        """Test that with_cli_filters preserves is_explicit flag."""
        # Create explicit target
        spec = OutputTargetSpec(name="test", path="./output")
        target = OutputTarget.from_spec(spec, tmp_path)
        assert target.is_explicit is True

        # Apply CLI filters
        filtered = target.with_cli_filters(languages=["en"], kinds=["completed"])

        # Should preserve is_explicit flag
        assert filtered.is_explicit is True

    def test_with_cli_filters_preserves_non_explicit_flag(self, tmp_path):
        """Test that with_cli_filters preserves is_explicit=False."""
        target = OutputTarget.default_target(tmp_path / "output")
        filtered = target.with_cli_filters(languages=["en"], kinds=None)

        assert filtered.is_explicit is False

    def test_manually_created_target_default_is_not_explicit(self, tmp_path):
        """Test that manually created targets have is_explicit=False by default."""
        target = OutputTarget(
            name="test",
            output_root=tmp_path / "output",
        )
        assert target.is_explicit is False

    def test_manually_created_target_can_be_explicit(self, tmp_path):
        """Test that is_explicit can be set manually."""
        target = OutputTarget(
            name="test",
            output_root=tmp_path / "output",
            is_explicit=True,
        )
        assert target.is_explicit is True


class TestOutputTargetRepr:
    """Tests for OutputTarget string representation."""

    def test_repr(self, tmp_path):
        """Test __repr__ method."""
        target = OutputTarget(
            name="test",
            output_root=tmp_path / "output",
            kinds=frozenset({"completed"}),
            formats=frozenset({"html"}),
            languages=frozenset({"en"}),
        )

        repr_str = repr(target)

        assert "OutputTarget" in repr_str
        assert "test" in repr_str
        assert "completed" in repr_str
        assert "html" in repr_str
        assert "en" in repr_str
