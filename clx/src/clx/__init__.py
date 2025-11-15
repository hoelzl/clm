"""CLX - Coding-Academy Lecture Manager eXperimental.

A course content processing system that converts educational materials
(Jupyter notebooks, PlantUML diagrams, Draw.io diagrams) into multiple output formats.
"""

from clx.__version__ import __version__

# Convenience imports for common classes
# This provides backward compatibility and a cleaner API
from clx.core.course import Course
from clx.core.section import Section
from clx.core.topic import Topic
from clx.core.course_file import CourseFile
from clx.core.course_spec import CourseSpec

__all__ = [
    "__version__",
    "Course",
    "Section",
    "Topic",
    "CourseFile",
    "CourseSpec",
]
