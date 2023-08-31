"""
The core classes of the CLM package.

These classes represent the domain model of the application: Courses, data_sources and
everything associated with these concepts.

Modules in this package may only depend on `clm.core`.

## Modules

- `clm.core.course`: A complete course.
- `clm.core.course_layout`: A description of the layout of a course.
- `clm.core.course_spec`: A serializable description of a course.
- `clm.core.data_sink`: A structured representation of the data we want to write.
- `clm.core.data_source`: A source file that we want to process.
- `clm.core.data_source_paths`: Abstractions for paths to data_sources.
- `clm.core.data_source_spec`: A serializable description of a data_source.
- `clm.core.directory_kind`: A description of how files in a directory should be classified.
- `clm.core.notifier`: A mechanism for notifying the user of events.
- `clm.core.output_spec`: A serializable description of an output format.
"""
