"""CLM - Coding-Academy Lecture Manager eXperimental.

A course content processing system that converts educational materials
(Jupyter notebooks, PlantUML diagrams, Draw.io diagrams) into multiple output formats.
"""

from clm.__version__ import __version__

# Convenience imports for common classes
# This provides backward compatibility and a cleaner API
from clm.core.course import Course
from clm.core.course_file import CourseFile
from clm.core.course_spec import CourseSpec
from clm.core.section import Section
from clm.core.topic import Topic

__all__ = [
    "__version__",
    "Course",
    "Section",
    "Topic",
    "CourseFile",
    "CourseSpec",
]
