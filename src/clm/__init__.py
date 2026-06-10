"""CLM - Coding-Academy Lecture Manager eXperimental.

A course content processing system that converts educational materials
(Jupyter notebooks, PlantUML diagrams, Draw.io diagrams) into multiple output formats.
"""

from typing import TYPE_CHECKING

from clm.__version__ import __version__

if TYPE_CHECKING:
    from clm.core.course import Course
    from clm.core.course_file import CourseFile
    from clm.core.course_spec import CourseSpec
    from clm.core.section import Section
    from clm.core.topic import Topic

# Convenience exports for common classes, resolved lazily (PEP 562).
# ``from clm import Course`` keeps working, but importing the ``clm``
# package no longer drags in the whole core object model — that import
# chain (course -> course_spec -> infrastructure) costs ~200ms and ran
# on every CLI invocation because every submodule import executes this
# ``__init__``.
_LAZY_EXPORTS = {
    "Course": ("clm.core.course", "Course"),
    "CourseFile": ("clm.core.course_file", "CourseFile"),
    "CourseSpec": ("clm.core.course_spec", "CourseSpec"),
    "Section": ("clm.core.section", "Section"),
    "Topic": ("clm.core.topic", "Topic"),
}

__all__ = [
    "__version__",
    "Course",
    "Section",
    "Topic",
    "CourseFile",
    "CourseSpec",
]


def __getattr__(name: str):
    try:
        module_name, attr = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    import importlib

    value = getattr(importlib.import_module(module_name), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
