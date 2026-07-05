from pathlib import Path
from typing import TYPE_CHECKING

from attr import Factory
from attrs import define

from clm.core.course_file import CourseFile
from clm.core.utils.notebook_mixin import NotebookMixin
from clm.core.utils.text_utils import Text

if TYPE_CHECKING:
    from clm.core.course import Course
    from clm.core.topic import Topic


@define
class Section(NotebookMixin):
    name: Text
    course: "Course"
    topics: list["Topic"] = Factory(list)
    id: str | None = None

    @property
    def files(self) -> list[CourseFile]:
        return [file for topic in self.topics for file in topic.files]

    def add_notebook_numbers(self) -> None:
        """Assign a 1-based slot number to every notebook in this section.

        Phase 6: split companions (``slides_foo.de.py`` /
        ``slides_foo.en.py``) share one logical slot — they represent the
        same notebook in two source files. Numbering them independently
        would offset the EN-side filename by one and break the
        byte-identical parity with the bilingual companion. The family
        key derived from :func:`clm.infrastructure.utils.path_utils.slide_family_key`
        keys each split pair under its bilingual companion's filename so
        the assignment is stable regardless of iteration order.

        The key is scoped to the notebook's parent directory: split
        companions always live next to each other, but unrelated topics
        may reuse the same file name (e.g. several ``workshop.py``
        folders in one section), and those must each get their own slot.
        """
        from clm.infrastructure.utils.path_utils import slide_family_key

        family_number: dict[tuple[Path, str], int] = {}
        next_index = 1
        for nb in self.notebooks:
            key = (nb.path.parent, slide_family_key(nb.path) or nb.path.name)
            if key not in family_number:
                family_number[key] = next_index
                next_index += 1
            nb.number_in_section = family_number[key]
