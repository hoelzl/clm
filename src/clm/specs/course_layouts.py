from clm.core.course_layout import (
    CourseLayout,
    course_layout_registry,
    course_layout_from_dict,
)
from clm.core.directory_kind import GeneralDirectory  # type: ignore
from clm.specs.directory_kinds import *  # type: ignore

from clm.utils.config import config


def create_layouts_from_config(config):
    for layout in config["course_layouts"].data:
        course_layout_registry[
            layout["name"]
        ] = lambda base_path: course_layout_from_dict(
            {**layout, "base_path": base_path}
        )


create_layouts_from_config(config)


def legacy_python_course_layout(base_path: Path) -> CourseLayout:
    return course_layout_registry["legacy_python"](base_path)
