"""
The core classes of the CLM package.

These classes represent the domain model of the application: Courses, documents and
everything associated with these concepts.

Modules in this package may only depend on `clm.core`.

## Modules

- `clm.core.course`: A complete course.
- `clm.core.course_layout`: A description of the layout of a course.
- `clm.core.course_spec`: A serializable description of a course.
- `clm.core.directory_kind`: A description of how files in a directory should be classified.
- `clm.core.document`: A single document.
- `clm.core.document_paths`: Abstractions for paths to documents.
- `clm.core.document_spec`: A serializable description of a single document.
- `clm.core.notifier`: A mechanism for notifying the user of events.
- `clm.core.output`: The superclass of results from processing documents.
- `clm.core.output_spec`: A serializable description of an output format.
"""
