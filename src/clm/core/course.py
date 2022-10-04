from abc import ABC, abstractmethod
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any, Iterable, TYPE_CHECKING

from clm.core.output_kind import OutputKind
from clm.utils.path import PathOrStr

if TYPE_CHECKING:
    from clm.core.document import Document

if TYPE_CHECKING:
    # Make PyCharm happy, since it doesn't understand the pytest extensions to doctests.
    def getfixture(_name: str) -> Any:
        ...


# %%
class DocumentProvider(ABC):
    """The interface for getting the source files of courses.

    We don't simply use a list of Paths as source documents for courses, since it's
    likely that we will need more elaborate structures, e.g., with videos stored in
    a content-management system.
    """

    @property
    @abstractmethod
    def documents(self):
        ...


class PathLikeDocumentProvider(DocumentProvider, ABC):
    """A document provider that transform path-likes into documents."""

    @property
    @abstractmethod
    def files(self) -> list[PathLike]:
        """Return the files that this document provider converts into documents."""
        ...

    def __repr__(self):
        return f"{type(self).__name__}(<{len(self.files)} documents>)"

    @property
    def documents(self) -> list["Document"]:
        """Return the documents of this provider.

        >>> from conftest import concrete_instance_of
        >>> source_files = getfixture("course_files")
        >>> c = concrete_instance_of(PathLikeDocumentProvider, initargs=(source_files,))
        >>> len(c.documents) == len(source_files)
        True
        >>> c.documents[:5]
        [Document(source_path='.../topic_10_python.py', kind=LectureSlide()),
         Document(source_path='.../ws_10_python.py', kind=Workshop()),
         Document(source_path='.../python_file.py', kind=PythonComplement()),
         Document(source_path='.../img/my_img.png', kind=Image()),
         Document(source_path='.../non_affine_file.py', kind=PythonFile())]
        """
        # Import locally to avoid potential problems with cyclic imports.
        from clm.core.document import Document

        return [Document(path) for path in self.files]


# %%
class FileDocumentProvider(PathLikeDocumentProvider):
    def __init__(self, files: Iterable[PathOrStr]):
        self._files = [Path(path) for path in files]

    @property
    def files(self):
        return self._files


# %%
@dataclass()
class Course:
    """A course comprises the set of all files that should be processed.

    >>> dp = FileDocumentProvider(["/tmp/slides/lecture_1.py", "/tmp/slides/ws_1.py"])
    >>> Course(dp, "/tmp/out/")
    Course(document_provider=FileDocumentProvider(<2 documents>),
           target_dir=...Path('/tmp/out'))
    """

    document_provider: DocumentProvider
    target_dir: Path

    def __init__(
        self, source_document_provider: DocumentProvider, target_dir: PathOrStr
    ):
        self.document_provider = source_document_provider
        self.target_dir = Path(target_dir)

    @property
    def source_documents(self) -> list["Document"]:
        """Return the documents corresponding to the source files of this course.

        >>> source_files = getfixture("course_files")
        >>> c = Course(FileDocumentProvider(source_files), target_dir="/tmp")
        >>> len(c.source_documents) == len(source_files)
        True
        >>> c.source_documents[:5]
        [Document(source_path='.../topic_10_python.py', kind=LectureSlide()),
         Document(source_path='.../ws_10_python.py', kind=Workshop()),
         Document(source_path='.../python_file.py', kind=PythonComplement()),
         Document(source_path='.../img/my_img.png', kind=Image()),
         Document(source_path='.../non_affine_file.py', kind=PythonFile())]
        """
        return self.document_provider.documents

    def process(self, output_kind: OutputKind):
        for doc in self.source_documents:
            doc.process(output_kind, target_dir=self.target_dir)
