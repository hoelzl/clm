# %%
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from clm.core.document import Document
from clm.core.output_spec import OutputSpec
from clm.core.course_specs import CourseSpec
from clm.utils.path_utils import PathOrStr

if TYPE_CHECKING:
    # Make PyCharm happy, since it doesn't understand the pytest extensions to doctests.
    def getfixture(_name: str) -> Any:
        ...


# %%
@dataclass
class Course:
    """A course comprises all data that should be processed or referenced."""

    source_dir: Path
    target_dir: Path
    template_dir: Path = None
    documents: list[Document] = field(default_factory=list)
    notebook_indices: dict[str, int] = field(default_factory=dict)

    # noinspection PyTypeChecker
    def __post_init__(self):
        if self.template_dir is None:
            self.template_dir = self.source_dir / "templates"
        if not self.target_dir.is_absolute():
            raise ValueError(
                "Target directory for a course must be absolute."
            )  # TODO: should we force other paths to be absolute as well?

    def get_index(self, nb_path: PathOrStr):
        """Return an index that increases per directory.

        >>> cs = Course(Path("/tmp"), Path("/tmp"))
        >>> cs.get_index("/foo/bar.py")
        1
        >>> cs.get_index("/foo/baz.py")
        2
        >>> cs.get_index("/quux/foobar.py")
        1
        >>> cs.get_index("/foo/bar.py")
        1
        >>> cs.get_index("/foo/xxx.py")
        3
        """
        nb_path = Path(nb_path)

        nb_key = nb_path.as_posix()
        current_index = self.notebook_indices.get(nb_key, None)
        if current_index is None:
            parent_key = nb_path.parent.as_posix()
            current_index = self.notebook_indices.get(parent_key, 0) + 1
            self.notebook_indices[parent_key] = current_index
            self.notebook_indices[nb_key] = current_index
        self.notebook_indices[nb_key] = current_index
        return current_index

    @staticmethod
    def from_spec(course_spec: CourseSpec):
        source_dir = Path(course_spec.base_dir)
        target_dir = Path(course_spec.target_dir)
        template_dir = Path(course_spec.template_dir)
        documents = course_spec.documents
        return Course(
            source_dir=source_dir,
            target_dir=target_dir,
            template_dir=template_dir,
            documents=documents,
        )

    def process_for_output_spec(self, output_kind: OutputSpec):
        for doc in self.documents:
            doc.process(self, output_kind)
        executor = ProcessPoolExecutor(max_workers=8)
        for doc in self.documents:
            executor.submit(doc.copy_to_target, self, output_kind)
        executor.shutdown(wait=True)


# %%
if __name__ == "__main__":
    from clm.core.output_spec import CompletedOutput, CodeAlongOutput, SpeakerOutput

    base_dir = Path.home() / "programming/python/courses/own/python-courses/"
    course_spec = CourseSpec.read_csv(base_dir / "course-specs/python-beginner.csv")
    course = Course.from_spec(course_spec)

    output_specs = [
        CompletedOutput("de", "public/Folien"),
        CodeAlongOutput("de", "public/CodeAlong"),
        SpeakerOutput("de", "private/Speaker"),
    ]

    for output_kind in output_specs:
        course.process_for_output_spec(output_kind)

    print("Done.")
