"""Spec-driven write containment: layer 1 (parse/validation).

Adversarial-review finding **S11** (umbrella #798): a course spec drives
filesystem writes — ``<output-target><path>``, ``<dir-group><path>``,
``<dir-group><name>`` and ``<dir-group><subdirs><subdir>`` — and none of
those were validated the way ``<include>`` paths already are. A one-
character typo (``<path>.</path>``) pointed the whole output tree at the
course root itself, where the post-build sweep and ``--clean`` would then
delete the course sources.

Layer 1 refuses such a spec *before any job runs*: absolute paths, ``..``
segments, and an output path that equals or contains the course data dir
are validation errors. Layer 2 (the ownership gate on destructive
operations) lives in ``tests/cli/test_output_ownership.py``.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from clm.core.course_spec import (
    CourseSpec,
    CourseSpecError,
    DirGroupSpec,
    OutputTargetSpec,
)
from clm.core.output_target import OutputTarget
from clm.core.utils.text_utils import sanitize_file_name

SPEC_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <output-targets>
        <output-target name="students">
            <path>{path}</path>
        </output-target>
    </output-targets>
</course>
"""


def _spec_with_output_path(path: str) -> CourseSpec:
    return CourseSpec.from_file(io.StringIO(SPEC_TEMPLATE.format(path=path)))


def _errors_for(path: str, course_root: Path) -> list[str]:
    return _spec_with_output_path(path).validate(course_root=course_root)


class TestOutputTargetPathValidation:
    """``<output-target><path>`` is validated at spec load."""

    def test_relative_path_is_accepted(self, tmp_path: Path) -> None:
        assert _errors_for("output/students", tmp_path) == []

    def test_leading_dot_slash_is_accepted(self, tmp_path: Path) -> None:
        assert _errors_for("./output/students", tmp_path) == []

    def test_bare_dot_is_rejected(self, tmp_path: Path) -> None:
        """The typo from the finding: ``<path>.</path>`` is the course root."""
        errors = _errors_for(".", tmp_path)
        assert errors, "a <path>.</path> target must fail validation"
        joined = " ".join(errors)
        assert "<path>" in joined
        assert "students" in joined

    def test_parent_traversal_is_rejected(self, tmp_path: Path) -> None:
        errors = _errors_for("../elsewhere", tmp_path)
        assert errors
        assert ".." in " ".join(errors)

    def test_interior_traversal_is_rejected(self, tmp_path: Path) -> None:
        assert _errors_for("output/../../elsewhere", tmp_path)

    @pytest.mark.parametrize(
        "path",
        [
            "/somewhere",
            "\\somewhere",
            "C:/somewhere",
            "C:\\somewhere",
        ],
    )
    def test_absolute_paths_are_rejected(self, path: str, tmp_path: Path) -> None:
        """Rooted and drive-qualified paths are refused on every platform.

        ``Path("C:/x").is_absolute()`` is False on POSIX and
        ``Path("/x").is_absolute()`` is False on Windows, so the check
        cannot lean on ``is_absolute`` alone — both forms must be
        rejected everywhere or a spec authored on one platform escapes
        the containment on the other.
        """
        errors = _errors_for(path, tmp_path)
        assert errors, f"{path!r} must fail validation"

    def test_path_resolving_onto_the_data_dir_via_symlink_is_rejected(self, tmp_path: Path) -> None:
        """The overlap check is symlink-correct, not a string comparison."""
        course_root = tmp_path / "course"
        course_root.mkdir()
        link = course_root / "loop"
        try:
            link.symlink_to(course_root, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks unavailable (Windows without Developer Mode)")
        assert _errors_for("loop", course_root)

    def test_validation_without_course_root_still_checks_shape(self) -> None:
        """Callers that have no course root still get the shape checks."""
        assert _spec_with_output_path("../elsewhere").validate()


class TestOutputTargetRuntimeRefusal:
    """``OutputTarget.from_spec`` refuses too — defense in depth.

    ``CourseSpec.validate`` is not on every path that constructs a
    ``Course`` (``clm git``, release tooling, the MCP server), so the
    resolution point enforces the same rule.
    """

    def test_from_spec_raises_for_dot_path(self, tmp_path: Path) -> None:
        spec = OutputTargetSpec(name="students", path=".")
        with pytest.raises(CourseSpecError):
            OutputTarget.from_spec(spec, tmp_path)

    def test_from_spec_raises_for_absolute_path(self, tmp_path: Path) -> None:
        spec = OutputTargetSpec(name="students", path=str(tmp_path / "elsewhere"))
        with pytest.raises(CourseSpecError):
            OutputTarget.from_spec(spec, tmp_path)

    def test_from_spec_accepts_relative_path(self, tmp_path: Path) -> None:
        spec = OutputTargetSpec(name="students", path="output/students")
        target = OutputTarget.from_spec(spec, tmp_path)
        assert target.output_root == (tmp_path / "output" / "students").resolve()


class TestDirGroupPathValidation:
    """``<dir-group>`` source paths and subdirs are course-root relative."""

    @staticmethod
    def _element(path: str = "code/examples", subdir: str | None = None):
        from xml.etree import ElementTree as ET

        subdirs = f"<subdirs><subdir>{subdir}</subdir></subdirs>" if subdir else ""
        return ET.fromstring(
            f"<dir-group><name>Examples</name><path>{path}</path>{subdirs}</dir-group>"
        )

    def test_relative_path_is_accepted(self) -> None:
        spec = DirGroupSpec.from_element(self._element())
        assert spec.path == "code/examples"

    @pytest.mark.parametrize("path", ["../outside", "code/../../outside", "/etc", "C:\\Windows"])
    def test_escaping_source_path_is_rejected(self, path: str) -> None:
        with pytest.raises(CourseSpecError):
            DirGroupSpec.from_element(self._element(path=path))

    @pytest.mark.parametrize("subdir", ["../outside", "/etc", "C:\\Windows"])
    def test_escaping_subdir_is_rejected(self, subdir: str) -> None:
        with pytest.raises(CourseSpecError):
            DirGroupSpec.from_element(self._element(subdir=subdir))

    def test_nested_subdir_is_accepted(self) -> None:
        spec = DirGroupSpec.from_element(self._element(subdir="a/b"))
        assert spec.subdirs == ["a/b"]


class TestDirGroupNameSanitization:
    """``<dir-group><name>`` is sanitized the way section names are."""

    def _dir_group(self, name: str):
        from unittest.mock import MagicMock

        from clm.core.dir_group import DirGroup
        from clm.core.utils.text_utils import Text

        course = MagicMock()
        course.output_dir_name = Text(de="kurs-de", en="course-en")
        return DirGroup(
            name=Text.from_string(name),
            source_dirs=(),
            relative_paths=(),
            course=course,
        )

    def test_traversal_name_cannot_escape_the_output_root(self, tmp_path: Path) -> None:
        out = self._dir_group("..").output_path(
            False, "en", output_root=tmp_path, skip_toplevel=True
        )
        resolved_root = (tmp_path / "course-en").resolve()
        assert out.resolve() == resolved_root or resolved_root in out.resolve().parents

    def test_separator_in_name_cannot_escape_the_output_root(self, tmp_path: Path) -> None:
        out = self._dir_group("../../etc").output_path(
            False, "en", output_root=tmp_path, skip_toplevel=True
        )
        assert tmp_path.resolve() in out.resolve().parents

    def test_ordinary_name_is_unchanged(self, tmp_path: Path) -> None:
        out = self._dir_group("Examples").output_path(
            False, "en", output_root=tmp_path, skip_toplevel=True
        )
        assert out.name == "Examples"


class TestSanitizeFileName:
    """``sanitize_file_name`` must never hand back a traversal component."""

    @pytest.mark.parametrize("text", [".", "..", " .. ", "..."])
    def test_dot_components_are_replaced(self, text: str) -> None:
        result = sanitize_file_name(text)
        assert result not in (".", "..")

    def test_ordinary_names_are_untouched(self) -> None:
        assert sanitize_file_name("Section 1") == "Section 1"
        assert sanitize_file_name("file.txt") == "file.txt"
        assert sanitize_file_name("C# Basics") == "CSharp Basics"
