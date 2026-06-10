"""Core course processing module.

This module contains the domain logic for course processing, including
course structure, file types, and operations.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clm.core.course import Course
    from clm.core.course_file import CourseFile
    from clm.core.course_spec import CourseSpec, CourseSpecError
    from clm.core.dir_group import DirGroup
    from clm.core.section import Section
    from clm.core.topic import Topic

# Convenience exports, resolved lazily (PEP 562) so that importing any
# ``clm.core.*`` submodule does not eagerly construct the whole course
# object model. Eager imports here also create a circular import:
# ``clm.infrastructure.backend`` imports ``clm.core.image_registry``,
# which would execute this ``__init__`` and re-enter
# ``clm.infrastructure`` via ``course_file`` before ``Backend`` exists.
_LAZY_EXPORTS = {
    "Course": ("clm.core.course", "Course"),
    "CourseFile": ("clm.core.course_file", "CourseFile"),
    "CourseSpec": ("clm.core.course_spec", "CourseSpec"),
    "CourseSpecError": ("clm.core.course_spec", "CourseSpecError"),
    "DirGroup": ("clm.core.dir_group", "DirGroup"),
    "Section": ("clm.core.section", "Section"),
    "Topic": ("clm.core.topic", "Topic"),
}

__all__ = [
    "Course",
    "CourseFile",
    "CourseSpec",
    "CourseSpecError",
    "DirGroup",
    "Section",
    "Topic",
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
