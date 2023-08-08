from pathlib import Path

from clm.core.directory_kind import GeneralDirectory
from clm.core.course_layout import (
    CourseLayout,
)
from clm.specs.directory_kinds import (
    LegacyExampleDirectory,
    NotebookDirectory,
)


def legacy_python_course_layout(base_path: Path) -> CourseLayout:
    return CourseLayout(
        base_path=base_path,
        default_directory_kind=GeneralDirectory(),
        directory_patterns=[
            ('examples', LegacyExampleDirectory),
            ('python_courses/slides/*', NotebookDirectory),
            ('python_courses/workshops', NotebookDirectory),
        ],
    )
