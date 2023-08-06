"""
The core classes of the CLM package.

These classes represent the domain model of the application: Courses, documents and
everything associated with these concepts.

Modules in this package may only depend on `clm.core`.

## Modules

- `clm.core.course`: A complete course.
- `clm.core.course_spec`: A serializable description of a course.
- `clm.core.document`: A single document.
- `clm.core.document_spec`: A serializable description of a single document.
- `clm.core.output_spec`: A serializable description of an output format.
"""

from clm.core.course import Course
from clm.core.document_spec import DocumentSpec
from clm.core.course_spec import CourseSpec
from clm.core.document import Document
from clm.core.output_spec import OutputSpec
