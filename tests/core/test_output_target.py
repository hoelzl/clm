"""Tests for OutputTarget runtime class."""

from pathlib import Path

import pytest

from clm.core.course_spec import VALID_FORMATS, JupyterLiteConfig, OutputTargetSpec
from clm.core.output_target import (
    ALL_KINDS,
    ALL_LANGUAGES,
    DEFAULT_FORMATS,
    OutputTarget,
)


class TestOutputTargetConstants:
    """Tests for OutputTarget constants."""

    def test_all_kinds(self):
        """Test ALL_KINDS contains all valid kinds."""
        assert ALL_KINDS == frozenset({"code-along", "completed", "speaker"})

    def test_default_formats_is_literal_three_set(self):
        """Pin DEFAULT_FORMATS to the literal {html, notebook, code}.

        DEFAULT_FORMATS is the set used when a target omits <formats>. It must
        stay decoupled from VALID_FORMATS so that adding a new opt-in format
        (e.g., jupyterlite) cannot silently change what existing courses build.
        Any change to this set is a breaking change to every course spec that
        relies on the "formats unspecified ⇒ all default formats" shorthand.
        """
        assert DEFAULT_FORMATS == frozenset({"html", "notebook", "code"})

    def test_default_formats_excludes_opt_in_formats(self):
        """DEFAULT_FORMATS must be a strict subset of VALID_FORMATS.

        Any format present in VALID_FORMATS but absent from DEFAULT_FORMATS is
        opt-in: it only runs when a target lists it explicitly. This test is
        the load-bearing assertion for the JupyterLite opt-in gate.
        """
        assert DEFAULT_FORMATS < VALID_FORMATS
        opt_in = VALID_FORMATS - DEFAULT_FORMATS
        assert "jupyterlite" in opt_in

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
        assert target.formats == DEFAULT_FORMATS
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
        assert target.formats == DEFAULT_FORMATS
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
            formats=DEFAULT_FORMATS,
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
            formats=DEFAULT_FORMATS,
            languages=frozenset({"de", "en"}),
        )

        filtered = target.with_cli_filters(languages=["en"], kinds=None)

        assert filtered.languages == frozenset({"en"})
        assert filtered.kinds == ALL_KINDS  # unchanged
        assert filtered.formats == DEFAULT_FORMATS  # unchanged
        assert filtered.output_root == target.output_root  # unchanged

    def test_with_cli_filters_kinds(self, tmp_path):
        """Test applying kinds filter."""
        target = OutputTarget(
            name="test",
            output_root=tmp_path / "output",
            kinds=ALL_KINDS,
            formats=DEFAULT_FORMATS,
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
            formats=DEFAULT_FORMATS,
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


class TestEffectiveJupyterLiteConfig:
    """Tests for OutputTarget.effective_jupyterlite_config() precedence."""

    @pytest.fixture
    def course_cfg(self) -> JupyterLiteConfig:
        return JupyterLiteConfig(
            kernel="xeus-python",
            wheels=["wheels/rich-13.7.1-py3-none-any.whl"],
        )

    @pytest.fixture
    def target_cfg(self) -> JupyterLiteConfig:
        return JupyterLiteConfig(
            kernel="xeus-python",
            wheels=["wheels/pytest-8.3.3-py3-none-any.whl"],
        )

    def test_returns_none_when_neither_set(self, tmp_path):
        """No course-level and no target-level config ⇒ None."""
        spec = OutputTargetSpec(name="t", path="./out")
        target = OutputTarget.from_spec(spec, tmp_path)
        assert target.effective_jupyterlite_config() is None

    def test_returns_course_level_when_only_course_set(self, tmp_path, course_cfg):
        """Target inherits course-level config when it has none of its own."""
        spec = OutputTargetSpec(name="t", path="./out")
        target = OutputTarget.from_spec(spec, tmp_path, course_jupyterlite=course_cfg)
        assert target.effective_jupyterlite_config() is course_cfg

    def test_returns_target_level_when_only_target_set(self, tmp_path, target_cfg):
        """Target-level config wins when no course-level config exists."""
        spec = OutputTargetSpec(name="t", path="./out", jupyterlite=target_cfg)
        target = OutputTarget.from_spec(spec, tmp_path)
        assert target.effective_jupyterlite_config() is target_cfg

    def test_target_level_overrides_course_level_wholesale(self, tmp_path, course_cfg, target_cfg):
        """Target-level config replaces course-level wholesale (no field merge)."""
        spec = OutputTargetSpec(name="t", path="./out", jupyterlite=target_cfg)
        target = OutputTarget.from_spec(spec, tmp_path, course_jupyterlite=course_cfg)

        effective = target.effective_jupyterlite_config()
        assert effective is target_cfg
        # Wholesale replacement: the target's wheel list, not the course's.
        assert effective is not None
        assert effective.wheels == ["wheels/pytest-8.3.3-py3-none-any.whl"]

    def test_with_cli_filters_preserves_jupyterlite_config(self, tmp_path, course_cfg):
        """CLI filter pass-through must not drop the config references."""
        spec = OutputTargetSpec(name="t", path="./out")
        target = OutputTarget.from_spec(spec, tmp_path, course_jupyterlite=course_cfg)
        filtered = target.with_cli_filters(languages=["en"], kinds=None)
        assert filtered.effective_jupyterlite_config() is course_cfg


class TestJupyterLiteFormatIsOptIn:
    """Regression tests that nail down the opt-in gate."""

    def test_jupyterlite_is_valid_but_not_default(self):
        """jupyterlite must be recognized (not rejected) but absent from DEFAULT_FORMATS."""
        assert "jupyterlite" in VALID_FORMATS
        assert "jupyterlite" not in DEFAULT_FORMATS

    def test_target_without_formats_does_not_include_jupyterlite(self, tmp_path):
        """Omitting <formats> expands to DEFAULT_FORMATS only — never jupyterlite."""
        spec = OutputTargetSpec(name="t", path="./out")  # formats=None
        target = OutputTarget.from_spec(spec, tmp_path)
        assert not target.includes_format("jupyterlite")

    def test_target_with_empty_formats_does_not_include_jupyterlite(self, tmp_path):
        """``formats=[]`` is treated like unspecified — still no jupyterlite."""
        spec = OutputTargetSpec(name="t", path="./out", formats=[])
        target = OutputTarget.from_spec(spec, tmp_path)
        assert not target.includes_format("jupyterlite")

    def test_target_with_explicit_jupyterlite_format_includes_it(self, tmp_path):
        """The only way in: list jupyterlite explicitly in <formats>."""
        spec = OutputTargetSpec(name="t", path="./out", formats=["notebook", "jupyterlite"])
        target = OutputTarget.from_spec(spec, tmp_path)
        assert target.includes_format("jupyterlite")
