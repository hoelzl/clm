"""
The core classes of the CLM package.

These classes represent the domain model of the application: Courses, documents and 
everything associated with these concepts.

Modules in this package may only depend on `clm.core`.

## Modules

- `clm.core.document`: The main user-visible data structure.
- `clm.core.document_kind`: Implementation of processors for various document kinds.
- `clm.core.output_kind`: Specification of the desired output.
"""

from clm.core.course_specs import CourseSpec
from clm.core.output_spec import OutputSpec
from clm.core.document import DocumentSpec, Document
from clm.core.course import Course
