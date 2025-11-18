from typing import TYPE_CHECKING

from attr import Factory
from attrs import define

from clx.core.course_file import CourseFile
from clx.core.utils.notebook_mixin import NotebookMixin
from clx.core.utils.text_utils import Text

if TYPE_CHECKING:
    from clx.core.course import Course
    from clx.core.topic import Topic


@define
class Section(NotebookMixin):
    name: Text
    course: "Course"
    topics: list["Topic"] = Factory(list)

    @property
    def files(self) -> list[CourseFile]:
        return [file for topic in self.topics for file in topic.files]

    def add_notebook_numbers(self):
        for index, nb in enumerate(self.notebooks, 1):
            nb.number_in_section = index
