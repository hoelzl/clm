"""
A `Document` is a single file that can be processed into a complete output.

How a document is processed depends on its document kind, which is determined
according to its path (including its file name and extension).

What kind of output is generated depends on the output kind. For notebooks this
determines which cells of the notebook are included in the output document. It
may also control other factors, e.g., whether a notebook input is processed into
a notebook or a Python source file.

## Class

- `Document`: The representation of a document.
"""

# %%
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

from clm.utils.introspection import all_concrete_subclasses
from clm.utils.path import PathOrStr
from clm.core.output_kind import OutputKind
from clm.core.document_kind import DocumentKind

# %%
if TYPE_CHECKING:
    # Make PyCharm happy, since it doesn't understand the pytest extensions to doctests.
    def getfixture(_name: str) -> Any:
        ...


# %%
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)


# %%
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

    ## Methods

    - `process()`: Process this document.

    ## Properties:

    - `source_path`: The path of the source file.
    - `kind`: The document kind.
    """

    def __init__(self, source_path: PathOrStr):
        self.source_path = Path(source_path)

        kind = self._determine_document_kind()
        if kind is None:
            raise ValueError(f"Found no document kind for {self}.")
        self.kind = kind

    def __repr__(self) -> str:
        attrs = f"source_path={self.source_path.as_posix()!r}"
        if hasattr(self, "kind"):
            attrs += f", kind={self.kind!r}"
        return f"{type(self).__name__}({attrs})"

    def _determine_document_kind(self) -> DocumentKind | None:
        kind = None
        for cls in all_concrete_subclasses(DocumentKind):
            if cls.is_valid_file_path(self.source_path):
                self._assert_kind_is_not_yet_set(kind, cls)
                kind = cls()  # type: ignore
        return kind

    @staticmethod
    def _assert_kind_is_not_yet_set(kind, cls):
        if kind is not None:
            raise ValueError(f"Found {cls} as document kind, but already have {kind}.")

    def process(self, output_kind: OutputKind, target_dir: PathOrStr) -> None:
        """Process the document according to its kind."""
        self.kind.process_document(self, output_kind=output_kind, target_dir=target_dir)


# %%
