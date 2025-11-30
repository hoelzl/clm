"""Core course processing module.

This module contains the domain logic for course processing, including
course structure, file types, and operations.
"""

from clx.core.course import Course
from clx.core.course_file import CourseFile
from clx.core.course_spec import CourseSpec, CourseSpecError
from clx.core.dir_group import DirGroup
from clx.core.section import Section
from clx.core.topic import Topic

__all__ = [
    "Course",
    "CourseFile",
    "CourseSpec",
    "CourseSpecError",
    "DirGroup",
    "Section",
    "Topic",
]
