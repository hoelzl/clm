from pathlib import Path

from clm.core.course_layout import (
    CourseLayout,
    course_layout_registry,
)
from clm.core.directory_kind import GeneralDirectory
from clm.specs.directory_kinds import (
    LegacyExampleDirectory,
    NotebookDirectory,
)


def legacy_python_course_layout(base_path: Path) -> CourseLayout:
    return CourseLayout(
        name="legacy_python",
        base_path=base_path,
        default_directory_kind=GeneralDirectory(),
        directory_patterns=[
            ("examples", LegacyExampleDirectory),
            ("metadata", GeneralDirectory),
            ("python_courses/slides/*", NotebookDirectory),
            ("python_courses/slides/*/img/**", GeneralDirectory),
            ("python_courses/slides/*/data/**", GeneralDirectory),
            ("python_courses/workshops", NotebookDirectory),
        ],
    )


course_layout_registry["legacy_python"] = legacy_python_course_layout
