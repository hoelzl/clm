from pathlib import Path

from configurator import Config

from clm.core.course_layout import course_layout_registry
from clm.core.directory_kind import GeneralDirectory
from clm.specs.course_layouts import (
    legacy_python_course_layout,
    create_layouts_from_config,
)
from clm.specs.directory_kinds import NotebookDirectory, LegacyExampleDirectory

_course_layouts = [
    {
        "name": "test-layout",
        "default_directory_kind": "GeneralDirectory",
        "directory_patterns": [
            ("code/completed", "LegacyExampleDirectory"),
            ("slides/*", "NotebookDirectory"),
            ("slides/*/img/**", "GeneralDirectory"),
        ],
    },
]


def test_create_layouts_from_config():
    config = Config({"course_layouts": _course_layouts})
    create_layouts_from_config(config)
    assert course_layout_registry.get("test-layout") is not None
    layout = course_layout_registry["test-layout"]
    assert layout.name == "test-layout"
    assert layout.default_directory_kind == GeneralDirectory()
    assert layout.directory_patterns == (
        ("code/completed", LegacyExampleDirectory),
        ("slides/*", NotebookDirectory),
        ("slides/*/img/**", GeneralDirectory),
    )


def test_legacy_python_course_layout():
    layout = legacy_python_course_layout()

    assert type(layout.default_directory_kind).__name__ == "GeneralDirectory"
    assert ("python_courses/slides/*", NotebookDirectory) in layout.directory_patterns
