"""Core course processing module.

This module contains the domain logic for course processing, including
course structure, file types, and operations.
"""

from clm.core.course import Course
from clm.core.course_file import CourseFile
from clm.core.course_spec import CourseSpec, CourseSpecError
from clm.core.dir_group import DirGroup
from clm.core.section import Section
from clm.core.topic import Topic

__all__ = [
    "Course",
    "CourseFile",
    "CourseSpec",
    "CourseSpecError",
    "DirGroup",
    "Section",
    "Topic",
]
