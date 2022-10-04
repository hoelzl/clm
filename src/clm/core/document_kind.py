"""
A `DocumentKind` is a representation of a type of document that describes how to
process this kind of document.

# Classes:

- `DocumentKind`: The abstract base class of all document kinds.
- `NotebookAffine`: The base class of document kinds that follow the notebook layout.
- `LectureSlide`: The document kind of lecture slides, processed by jupytext.
- `Workshop`: The document kind of workshops, processed by jupytext.
- `PythonComplement`: A Python file that belongs together with notebooks.
- `Image`: An image or a source for an image.
- `PythonFile`: A Python file that does not fit into the notebook layout.
"""

# %%
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from clm.utils.path import PathOrStr
from clm.core.output_kind import OutputKind

# %%
if TYPE_CHECKING:
    from clm.core.document import Document

# %%
# Make PyCharm happy, since it doesn't understand the pytest extensions to doctests.
if TYPE_CHECKING:

    def getfixture(_name: str) -> Any:
        ...


# %%
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)


# %%
@dataclass
class DocumentKind(ABC):
    """A description of the contents or use of a document."""

    default_slide_fragment = Path("Slides")
    default_codealong_fragment = Path("Codealong")

    @classmethod
    def is_valid_file_path(cls, path: PathOrStr) -> bool:
        """Return whether a path is valid for this kind of document.

        In the current layout most documents kinds will use only the file name,
        but we pass in the full path so that we can support both the legacy layout
        (while parts of it still exist) and potential future reorganizations of the
        input files.

        This method is only called for concrete classes. When defining new
        subclasses, care must be taken that the valid file paths of different
        document kinds are actually disjoint, otherwise one of the matching
        document kinds will be assigned at random.

        >>> DocumentKind.is_valid_file_path("foo/bar.py")
        False
        """
        return False

    @classmethod
    def target_dir_fragment(cls, output_kind: OutputKind) -> Path:
        """Return a path to insert into the target path.

        This may depend on the output kind for which we are currently generating
        the document.

        In general, only notebook-affine documents create `target_dir_fragments`
        different from `Path()`.

        >>> import clm.core.output_kind as ok
        >>> DocumentKind.target_dir_fragment(ok.SpeakerOutput()).as_posix()
        '.'
        >>> DocumentKind.target_dir_fragment(ok.CompletedOutput()).as_posix()
        '.'
        >>> DocumentKind.target_dir_fragment(ok.CodeAlongOutput()).as_posix()
        '.'
        """
        return Path()

    @abstractmethod
    def process_document(self, doc: "Document", output_kind: OutputKind, target_path):
        ...


# %%
@dataclass
class NotebookAffine(DocumentKind, ABC):
    """Superclass for document kinds that are notebook affine.

    We call files *notebook affine* if they live in a directory hierarchy that
    follows the notebook hierarchy. This can either be a directory also
    containing notebooks (or a subdirectory of such a directory) or in a
    parallel hierarchy that mirrors the notebook folder hierarchy (e.g., a
    parallel hierarchy for Python packages that are affiliated with the same
    modules as notebooks).

    In some aspects these files should be processed similar to notebooks, e.g.,
    their output should be placed into the notebook target directory instead of
    the generic data directory.
    """

    notebook_affine_dirs = ["slides", "workshops", "modules"]
    name_regex = re.compile(r".*")

    @classmethod
    def is_valid_file_path(cls, path: PathOrStr) -> bool:
        """Return whether `path` is in a directory containing notebooks.

        >>> NotebookAffine.is_valid_file_path("/usr/slides/any.file")
        True
        >>> NotebookAffine.is_valid_file_path("slides/")
        True
        >>> NotebookAffine.is_valid_file_path("workshops/ws_234.py")
        True
        >>> NotebookAffine.is_valid_file_path("/usr/foo/lecture_123.txt")
        False
        >>> NotebookAffine.is_valid_file_path("foo/lecture_123.txt")
        False
        """
        path = Path(path)
        is_path_in_correct_dir = any(
            part in cls.notebook_affine_dirs for part in path.parts
        )
        does_path_match_pattern = bool(cls.name_regex.match(path.name))
        return is_path_in_correct_dir and does_path_match_pattern

    @classmethod
    def target_dir_fragment(cls, output_kind: OutputKind) -> Path:
        """Return a string to use as part of the target path.

        This may depend on the output kind for which we are currently generating
        the document.

        >>> import clm.core.output_kind as ok
        >>> NotebookAffine.target_dir_fragment(ok.SpeakerOutput()).as_posix()
        'Slides'
        >>> NotebookAffine.target_dir_fragment(ok.CompletedOutput()).as_posix()
        'Slides'
        >>> NotebookAffine.target_dir_fragment(ok.CodeAlongOutput()).as_posix()
        'Codealong'
        """
        if output_kind.are_any_cell_contents_cleared:
            return cls.default_codealong_fragment
        else:
            return cls.default_slide_fragment


# %%
@dataclass
class LectureSlide(NotebookAffine):
    """Slides for lectures."""

    name_regex = re.compile(r"^(lecture|topic)_.*\.py$")

    @classmethod
    def is_valid_file_path(cls, path: PathOrStr) -> bool:
        """Return whether `path` is valid for lecture slides.

        We check that the path is in the correct dictionary, starts with
        `lecture_` and has the suffix `.py`. This is not the full pattern that a
        lecture slide path should match to validate correctly, but it is enough
        to unambiguously identify the file as a lecture slide.

        >>> LectureSlide.is_valid_file_path("/usr/slides/lecture_123.py")
        True
        >>> LectureSlide.is_valid_file_path("slides/lecture_123.py")
        True
        >>> LectureSlide.is_valid_file_path("slides/ws_234.py")
        False
        >>> LectureSlide.is_valid_file_path("slides/lecture_123.txt")
        False
        >>> LectureSlide.is_valid_file_path("foo/lecture_123.py")
        False
        """
        return super().is_valid_file_path(path)

    def process_document(self, doc: "Document", output_kind: OutputKind, target_path):
        print("Processing LectureSlide.")


# %%
@dataclass
class Workshop(NotebookAffine):
    """Slides for workshops."""

    name_regex = re.compile(r"^(ws|workshop)_.*\.py$")

    @classmethod
    def is_valid_file_path(cls, path: PathOrStr) -> bool:
        """Return whether `path` is valid for workshop slides.

        We check that the path is in the correct dictionary, starts with `ws_`
        or `workshop_` and has the suffix `.py`. This is not the full pattern
        that a workshop path should match to validate correctly, but it is
        enough to unambiguously identify the file as a workshop.

        >>> Workshop.is_valid_file_path("/usr/workshops/ws_123.py")
        True
        >>> Workshop.is_valid_file_path("workshops/ws_123.py")
        True
        >>> Workshop.is_valid_file_path("workshops/workshop_123.py")
        True
        >>> Workshop.is_valid_file_path("workshops/lecture_234.py")
        False
        >>> Workshop.is_valid_file_path("workshops/ws_123.txt")
        False
        >>> Workshop.is_valid_file_path("/home/foo/ws_123.py")
        False
        >>> Workshop.is_valid_file_path("foo/ws_123.py")
        False
        """
        return super().is_valid_file_path(path)

    def process_document(self, doc: "Document", output_kind: OutputKind, target_path):
        print("Processing Workshop.")


# %%
@dataclass
class PythonComplement(NotebookAffine):
    """Python files that are "notebook affine"."""

    name_regex = re.compile(r".*\.py$")

    @classmethod
    def is_valid_file_path(cls, path: PathOrStr) -> bool:
        """Return whether `path` is a notebook-affine, regular Python file.

        This document kind represents Python files that are not notebook affine.

        >>> PythonComplement.is_valid_file_path("/home/user/slides/my_python_file.py")
        True
        >>> PythonComplement.is_valid_file_path("slides/my_python_file.py")
        True
        >>> PythonComplement.is_valid_file_path("workshops/my_python_file.py")
        True
        >>> PythonComplement.is_valid_file_path("/usr/foo/ws_123.py")
        False
        >>> PythonComplement.is_valid_file_path("foo/ws_123.py")
        False
        >>> PythonComplement.is_valid_file_path("foo/lecture_234.py")
        False
        >>> PythonComplement.is_valid_file_path("foo/ws_123.txt")
        False
        >>> PythonComplement.is_valid_file_path("slides/lecture_234.py")
        False
        >>> PythonComplement.is_valid_file_path("workshops/ws_123.txt")
        False
        """
        path = Path(path)
        is_path_notebook_affine = NotebookAffine.is_valid_file_path(path)
        does_path_match_regex = bool(cls.name_regex.match(path.name))
        is_lecture_slide = LectureSlide.is_valid_file_path(path)
        is_workshop = Workshop.is_valid_file_path(path)
        return (
            is_path_notebook_affine
            and does_path_match_regex
            and not is_lecture_slide
            and not is_workshop
        )

    def process_document(self, doc: "Document", output_kind: OutputKind, target_path):
        print("Processing Workshop.")


# %%
@dataclass
class Image(NotebookAffine):
    """Images that should be copied but not processed."""

    name_regex = re.compile(r".*\.(png|jpe?g|gif|svg)$")

    @classmethod
    def is_valid_file_path(cls, path: PathOrStr) -> bool:
        """Return whether `path` is a valid image file.

        >>> Image.is_valid_file_path("/usr/img/bar.png")
        True
        >>> Image.is_valid_file_path("foo/img/bar.png")
        True
        >>> Image.is_valid_file_path("img/bar.jpg")
        True
        >>> Image.is_valid_file_path("img/bar.jpeg")
        True
        >>> Image.is_valid_file_path("img/bar.gif")
        True
        >>> Image.is_valid_file_path("img/bar.svg")
        True
        >>> Image.is_valid_file_path("foo/bar.png")
        False
        >>> Image.is_valid_file_path("img/bar.py")
        False
        >>> Image.is_valid_file_path("img/bar.txt")
        False
        """
        path = Path(path)
        return path.parent.name == "img" and bool(cls.name_regex.match(path.name))

    @classmethod
    def target_dir_fragment(cls, output_kind: OutputKind) -> Path:
        """Return a string to use as part of the target path.

        This may depend on the output kind for which we are currently generating
        the document.

        >>> import clm.core.output_kind as ok
        >>> Image.target_dir_fragment(ok.SpeakerOutput()).as_posix()
        'Slides/img'
        >>> Image.target_dir_fragment(ok.CompletedOutput()).as_posix()
        'Slides/img'
        >>> Image.target_dir_fragment(ok.CodeAlongOutput()).as_posix()
        'Codealong/img'
        """
        return super().target_dir_fragment(output_kind) / "img"

    def process_document(self, doc: "Document", output_kind: OutputKind, target_path):
        print("Processing Image.")


# %%
@dataclass
class PythonFile(DocumentKind):
    """Python files that are not notebook affine."""

    name_regex = re.compile(r".*\.py$")

    @classmethod
    def is_valid_file_path(cls, path: PathOrStr) -> bool:
        """Return whether `path` is a regular Python file.

        This document kind represents Python files that are not notebook affine.

        >>> PythonFile.is_valid_file_path("/usr/foo/ws_123.py")
        True
        >>> PythonFile.is_valid_file_path("foo/ws_123.py")
        True
        >>> PythonFile.is_valid_file_path("foo/lecture_234.py")
        True
        >>> PythonFile.is_valid_file_path("foo/ws_123.txt")
        False
        >>> PythonFile.is_valid_file_path("slides/lecture_234.py")
        False
        >>> PythonFile.is_valid_file_path("workshops/ws_123.txt")
        False
        """
        path = Path(path)
        is_path_notebook_affine = NotebookAffine.is_valid_file_path(path)
        does_path_match_regex = bool(cls.name_regex.match(path.name))
        return (not is_path_notebook_affine) and does_path_match_regex

    def process_document(self, doc: "Document", output_kind: OutputKind, target_path):
        print("Processing Workshop.")
