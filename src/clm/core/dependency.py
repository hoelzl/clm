from functools import singledispatch

from clm.core.course import Course
from clm.core.data_source import DataSource
from clm.utils.location import Location


@singledispatch
def find_dependencies(obj, _course: Course) -> list[tuple[Location, Location], ...]:
    raise NotImplementedError(f"Don't know how to find dependents of {obj!r}")


@find_dependencies.register
def _(_obj: DataSource, _course: Course) -> list[tuple[Location, Location], ...]:
    return []
