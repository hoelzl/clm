"""
Specs are descriptions of objects that can be edited as text.

A `DocumentSpec` is a description of a single document.
"""
from typing import NamedTuple


class DocumentSpec(NamedTuple):
    """A description how to build a document.

    Document specs are the intermediate representation from which we generate the
    actual course.

    The idea is that we auto-generate a file containing document specs that can be
    manually edited to serve as input for the actual course. Therefore, we set the
    relative target dir to `"-"` which means "don't create a document for this
    source". For documents that should be included in the course, this value can then
    be changed to the actual subdirectory in which the generated document should live
    (e.g., "week1", "week2", etc. for online courses).
    """

    source_file: str
    target_dir_fragment: str
    kind: str
    file_num: int
