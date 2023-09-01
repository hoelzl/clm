from pathlib import Path

from clm.specs.course_layouts import legacy_python_course_layout
from clm.specs.directory_kinds import NotebookDirectory


def test_legacy_python_course_layout():
    layout = legacy_python_course_layout()

    assert type(layout.default_directory_kind).__name__ == "GeneralDirectory"
    assert ("python_courses/slides/*", NotebookDirectory) in layout.directory_patterns
