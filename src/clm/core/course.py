from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, TYPE_CHECKING

from clm.core.output_kind import OutputKind
from clm.utils.path import PathOrStr

if TYPE_CHECKING:
    from clm.core.document import Document

# %%
if TYPE_CHECKING:
    # Make PyCharm happy, since it doesn't understand the pytest extensions to doctests.
    def getfixture(_name: str) -> Any:
        ...


@dataclass(init=False)
class Course:
    """A course comprises the set of all files that should be processed.

    >>> Course(["/tmp/slides/lecture_1.py", "/tmp/slides/ws_1.py"], "/tmp/out/")
    Course(target_dir=...Path('/tmp/out'))
    """

    source_files: list[Path] = field(repr=False)
    target_dir: Path

    def __init__(self, source_files: Iterable[PathOrStr], target_dir: PathOrStr):
        """Create a course.

        The `source_files` and `target_dir` arguments are converted to `list[Path]` and
         `Path`, respectivvely.

        >>> source_files = getfixture("course_files")
        >>> c = Course(source_files, target_dir="/tmp")
        >>> c.source_files == source_files
        True
        >>> len(c.source_files)
        11
        >>> [c.target_dir]
        [...Path('/tmp')]
        """
        self.source_files = [Path(path) for path in source_files]
        self.target_dir = Path(target_dir)

    @property
    def source_documents(self) -> list["Document"]:
        """Return the documents corresponding to the source files of this course.

        >>> source_files = getfixture("course_files")
        >>> c = Course(source_files, target_dir="/tmp")
        >>> len(c. source_documents) == len(c.source_files)
        True
        >>> c.source_documents[:5]
        [Document(source_path='.../topic_10_python.py', kind=LectureSlide()),
         Document(source_path='.../ws_10_python.py', kind=Workshop()),
         Document(source_path='.../python_file.py', kind=PythonComplement()),
         Document(source_path='.../img/my_img.png', kind=Image()),
         Document(source_path='.../non_affine_file.py', kind=PythonFile())]
        """
        # Import locally to avoid potential problems with cyclic imports.
        from clm.core.document import Document

        return [Document(path) for path in self.source_files]

    def process(self, output_kind: OutputKind):
        for doc in self.source_documents:
            doc.process(output_kind, target_dir=self.target_dir)
