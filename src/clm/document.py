"""
A `Document` is a single file that can be processed into a complete output.

How a document is processed depends on its document kind, which is determined
according to its path (including its file name and extension).

What kind of output is generated depends on the output kind. For notebooks this
determines which cells of the notebook are included in the output document. It
may also control other factors, e.g., whether a notebook input is processed into
a notebook or a Python source file.
"""

# %%
import dataclasses
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
import re

from clm.class_utils import all_concrete_subclasses
from clm.jupytext_utils import Cell, get_tags, set_tags, get_cell_type


# %%
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)


# %%
@dataclass
class OutputKind(ABC):
    """Description of the output that should be contained in a document.

    Outputs can either be public or private.  In public outputs some data is not
    included, e.g., speaker notes. Private outputs can potentially contain all
    data.

    ## Methods:

    - `is_cell_included()`: Returns whether a cell should be included in the
      output.
    - `is_cell_contents_included()`: Returns whether the contents of a cell
      should be included or cleared.
    - `target_dir_fragment()`: A path fragment that can be used to place
      different outputs into a folder hierarchy

    ## Properties:

    - `is_public`: Is the output meant for public consumption?
    - `is_private`: Is the output for the lecturer only?
    """

    @property
    @abstractmethod
    def is_public(self) -> bool:
        """Return `True` if the document is public, `False` if it is private."""
        ...

    @property
    def is_private(self) -> bool:
        """Return `True` if the document is private, `False` if it is public.

        >>> from conftest import concrete_instance_of
        >>> ok = concrete_instance_of(OutputKind)

        >>> ok.is_public != ok.is_private
        True
        """
        return not self.is_public

    def is_cell_included(self, cell: Cell) -> bool:
        """Return whether the cell should be included or completely removed.

        If this method returns `False` the complete cell is removed from the
        output. This is used to, e.g., remove speaker notes or alternate
        solutions from public outputs.

        The default implementation of this method returns `True`.

        >>> from conftest import concrete_instance_of
        >>> ok = concrete_instance_of(OutputKind, "is_cell_included")

        >>> cell = getfixture("code_cell")
        >>> ok.is_cell_included(cell)
        True
        """
        return True

    def is_cell_contents_included(self, cell: Cell) -> bool:
        """Return whether the cell contents should be included or cleared.

        If this method returns `False` the contents of the cell is cleared, the
        cell itself is still included. This is used to, e.g., remove code from
        most code cells in codealong notebooks.

        The default implementation of this method returns `True` for all cells.

        If this method is overridden, `self.are_any_cell_contents_cleared` has
        to be overridden as well to return `True`.

        >>> from conftest import concrete_instance_of
        >>> ok = concrete_instance_of(OutputKind, "is_cell_contents_included")

        >>> markdown_cell = getfixture("markdown_cell")
        >>> ok.is_cell_contents_included(markdown_cell)
        True

        >>> code_cell = getfixture("code_cell")
        >>> ok.is_cell_contents_included(code_cell)
        True
        """
        return True

    @property
    def are_any_cell_contents_cleared(self) -> bool:
        """Return whether the contents of any type of cell is cleared.

        This is false by default; it is true for outputs such as codealong
        notebooks where most code cells are cleared.

        >>> from conftest import concrete_instance_of
        >>> ok = concrete_instance_of(OutputKind, "are_any_cell_contents_cleared")

        >>> ok.are_any_cell_contents_cleared
        False
        """
        return False

    @property
    @abstractmethod
    def target_dir_fragment(self) -> str:
        """Return a string to use as part of a path or file name."""
        ...


# %%
@dataclass
class PublicOutput(OutputKind):
    """Superclass for output types for documents shared with the public."""

    @property
    def is_public(self) -> bool:
        """Always returns `True`."""
        return True

    @property
    def target_dir_fragment(self) -> str:
        return "public"


# %%
@dataclass
class CompletedOutput(PublicOutput):
    """Output kind for documents containing all data shared with the public.

    This means they contain everything except speaker notes.
    """


# %%
@dataclass
class CodeAlongOutput(PublicOutput):
    """Output kind for public documents that can be completed during the course.

    Only code cells marked with the "keep" tag have contents in them, all other
    code cells are empty.
    """

    code_tags_to_keep = {"keep"}

    def is_cell_contents_included(self, cell: Cell) -> bool:
        """Return whether the cell contents should be included or cleared.

        Returns `True` for non-code cells and for code cells marked with the
        `keep` tag.

        >>> ok = CodeAlongOutput()

        >>> markdown_cell = getfixture("markdown_cell")
        >>> ok.is_cell_contents_included(markdown_cell)
        True

        >>> code_cell = getfixture("code_cell")
        >>> ok.is_cell_contents_included(code_cell)
        False

        >>> set_tags(code_cell, ["keep"])
        >>> ok.is_cell_contents_included(code_cell)
        True
        """
        if get_cell_type(cell) == "code":
            return len(self.code_tags_to_keep & set(get_tags(cell))) != 0
        else:
            return True


    @property
    def are_any_cell_contents_cleared(self) -> bool:
        """Return `True`, since some cells are cleared.

        >>> ok = CodeAlongOutput()
        >>> ok.are_any_cell_contents_cleared
        True
        """
        return True



# %%
@dataclass
class SpeakerOutput(OutputKind):
    """Output kind for documents containing all public and private data."""

    @property
    def is_public(self) -> bool:
        return False

    @property
    def target_dir_fragment(self) -> str:
        return "speaker"


# %%
@dataclass
class DocumentKind(ABC):
    """A description of the contents or use of a document."""

    default_slide_fragment = Path("Slides")
    default_codealong_fragment = Path("Codealong")

    @classmethod
    def is_valid_file_path(cls, path: PathLike) -> bool:
        """Return `True` if path is valid for this kind of document.

        In the current layout most documents kinds will use only the file name,
        but we pass in the full path so that we can support both the legacy
        layout (while parts of it still exist) as well as potential future
        reorganizations of the input files.

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
        """Return path to insert into the target path.

        This may depend on the output kind for which we are currently generating
        the document.

        In general, only notebook affine documents create
        `target_dir_fragments`.

        >>> DocumentKind.target_dir_fragment(SpeakerOutput()).as_posix()
        '.'
        >>> DocumentKind.target_dir_fragment(CompletedOutput()).as_posix()
        '.'
        >>> DocumentKind.target_dir_fragment(CodeAlongOutput()).as_posix()
        '.'
        """
        return Path()

    @abstractmethod
    def process_document(self, doc: "Document", output_kind: OutputKind):
        ...


# %%
@dataclass
class NotebookAffine(DocumentKind):
    """Superclass for document kinds that follow the notebook folder structure."""

    notebook_dirs = ["slides", "workshops"]
    name_regex = re.compile(r".*")

    @classmethod
    def is_valid_file_path(cls, path: PathLike) -> bool:
        """Return `True` if `path` is in a notebook directory.

        We check that the path is in one of the designated notebook directories.

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
        is_path_in_correct_dir = any(part in cls.notebook_dirs for part in path.parts)
        does_path_match_pattern = bool(cls.name_regex.match(path.name))
        return is_path_in_correct_dir and does_path_match_pattern

    @classmethod
    def target_dir_fragment(cls, output_kind: OutputKind) -> Path:
        """Return a string to use as part of the target path.

        This may depend on the output kind for which we are currently generating
        the document.

        >>> NotebookAffine.target_dir_fragment(SpeakerOutput()).as_posix()
        'Slides'
        >>> NotebookAffine.target_dir_fragment(CompletedOutput()).as_posix()
        'Slides'
        >>> NotebookAffine.target_dir_fragment(CodeAlongOutput()).as_posix()
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

    name_regex = re.compile(r"^lecture_.*\.py$")

    @classmethod
    def is_valid_file_path(cls, path: PathLike) -> bool:
        """Return `True` if `path` is valid for a lecture slide.

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

    def process_document(self, doc: "Document", output_kind: OutputKind):
        print("Processing LectureSlide.")


# %%
@dataclass
class Workshop(NotebookAffine):
    """Slides for workshops."""

    name_regex = re.compile(r"^(ws|workshop)_.*\.py$")

    @classmethod
    def is_valid_file_path(cls, path: PathLike) -> bool:
        """Return `True` if `path` is valid for a workshop.

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

    def process_document(self, doc: "Document", output_kind: OutputKind):
        print("Processing Workshop.")


# %%
@dataclass
class PythonComplement(NotebookAffine):
    """Python files that are notebook affine because they are connected to notebooks."""

    name_regex = re.compile(r".*\.py$")

    @classmethod
    def is_valid_file_path(cls, path: PathLike) -> bool:
        """Return `True` if `path` is a regular Python file.

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

    def process_document(self, doc: "Document", output_kind: OutputKind):
        print("Processing Workshop.")


# %%
@dataclass
class Image(NotebookAffine):
    """Images that should be copied but not processed."""

    name_regex = re.compile(r".*\.(png|jpe?g|gif|svg)$")

    @classmethod
    def is_valid_file_path(cls, path: PathLike) -> bool:
        """Return `True` if `path` is a valid image file.

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

        >>> Image.target_dir_fragment(SpeakerOutput()).as_posix()
        'Slides/img'
        >>> Image.target_dir_fragment(CompletedOutput()).as_posix()
        'Slides/img'
        >>> Image.target_dir_fragment(CodeAlongOutput()).as_posix()
        'Codealong/img'
        """
        return super().target_dir_fragment(output_kind) / "img"

    def process_document(self, doc: "Document", output_kind: OutputKind):
        print("Processing Image.")


# %%
@dataclass
class PythonFile(DocumentKind):
    """Python files that are not notebook affine."""

    name_regex = re.compile(r".*\.py$")

    @classmethod
    def is_valid_file_path(cls, path: PathLike) -> bool:
        """Return `True` if `path` is a regular Python file.

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

    def process_document(self, doc: "Document", output_kind: OutputKind):
        print("Processing Workshop.")


# %%
@dataclass(repr=False)
class Document:
    """Representation of a document existing as file.

    Most of the actual work is performed by the DocumentKind instance.

    >>> Document("slides/lecture_01.py")
    Document(source_path='slides/lecture_01.py', kind=LectureSlide())
    >>> Document("slides/ws_01.py")
    Document(source_path='slides/ws_01.py', kind=Workshop())
    >>> Document("slides/my_module.py")
    Document(source_path='slides/my_module.py', kind=PythonComplement())
    >>> Document("example/my_module.py")
    Document(source_path='example/my_module.py', kind=PythonFile())
    >>> Document("not-a-file-type-i-understand")
    Traceback (most recent call last):
    ...
    ValueError: Found no document kind for Document(...).
    """

    source_path: Path
    kind: DocumentKind = dataclasses.field(init=False, repr=False)

    def __post_init__(self):
        kind = self._determine_document_kind()
        if kind is None:
            raise ValueError(f"Found no document kind for {self}.")
        self.kind = kind

    def _determine_document_kind(self) -> DocumentKind | None:
        kind = None
        for cls in all_concrete_subclasses(DocumentKind):
            if cls.is_valid_file_path(self.source_path):
                self._assert_kind_is_not_yet_set(kind, cls)
                kind = cls()  # type: ignore
        return kind

    def _assert_kind_is_not_yet_set(self, kind, cls):
        if kind is not None:
            raise ValueError(f"Found {cls} as document kind, but already have {kind}.")

    def __repr__(self) -> str:
        attrs = f"source_path={self.source_path!r}"
        if hasattr(self, "kind"):
            attrs += f", kind={self.kind!r}"
        return f"{type(self).__name__}({attrs})"

    def process(self, output_kind: OutputKind):
        self.kind.process_document(self, output_kind=output_kind)


# %%
