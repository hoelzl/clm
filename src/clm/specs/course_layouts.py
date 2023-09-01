from clm.core.course_layout import (
    CourseLayout,
    course_layout_registry,
    course_layout_from_dict,
)
from clm.core.directory_kind import GeneralDirectory  # type: ignore
from clm.specs.directory_kinds import *  # type: ignore

from clm.utils.config import config


def create_layouts_from_config(config):
    for layout in config["course_layouts"]:
        course_layout = course_layout_from_dict(layout)
        course_layout_registry[layout.name] = course_layout


create_layouts_from_config(config)


def legacy_python_course_layout() -> CourseLayout:
    return course_layout_registry["legacy_python"]
