from clm.core.course_layout import (
    CourseLayout,
    course_layout_registry,
    course_layout_from_dict,
)
from clm.core.directory_kind import GeneralDirectory  # type: ignore
from clm.specs.directory_kinds import *  # type: ignore

from clm.utils.config import config


def create_layout_factory(layout_data):
    def factory(base_path: Path) -> CourseLayout:
        return course_layout_from_dict(layout_data | {"base_path": base_path})

    return factory


def create_layouts_from_config(config):
    for layout in config["course_layouts"]:
        course_layout_registry[layout.name] = create_layout_factory(layout.data)


create_layouts_from_config(config)


def legacy_python_course_layout(base_path: Path) -> CourseLayout:
    return course_layout_registry["legacy_python"](base_path)
