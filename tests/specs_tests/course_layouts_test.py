from pathlib import Path

from clm.specs.course_layouts import legacy_python_course_layout
from clm.specs.directory_kinds import NotebookDirectory


def test_legacy_python_course_layout():
    base_path = Path("tests/test_data/legacy_python").absolute()
    layout = legacy_python_course_layout(base_path)

    assert layout.base_path == base_path
    assert type(layout.default_directory_kind).__name__ == "GeneralDirectory"
    assert ("python_courses/slides/*", NotebookDirectory) in layout.directory_patterns
