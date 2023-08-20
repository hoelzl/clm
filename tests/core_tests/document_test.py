from pathlib import Path

import pytest

from clm.core.course_spec import CourseSpec
from clm.core.document import Document
from clm.core.document_spec import DocumentSpec


@pytest.fixture
def course_spec():
    return CourseSpec(Path("/course").absolute(), Path("/out/").absolute())


def test_document_from_spec_for_relative_path(course_spec):
    ds = DocumentSpec("my_doc.py", "nb", "Notebook", 1)
    document = Document.from_spec(course_spec, ds)

    assert document.source_file.as_posix().endswith("/course/my_doc.py")
    assert document.target_dir_fragment == "nb"
    assert document.prog_lang == "python"
    assert document.file_num == 1


def test_document_from_spec_for_absolute_path(course_spec):
    ds = DocumentSpec("/foo/my_doc.py", "nb", "Notebook", 1)
    document = Document.from_spec(course_spec, ds)

    assert document.source_file.as_posix().endswith("/foo/my_doc.py")
    assert document.target_dir_fragment == "nb"
    assert document.prog_lang == "python"
    assert document.file_num == 1


def test_document_from_spec_for_image_file(course_spec):
    ds = DocumentSpec("foo.png", "img", "DataFile", 1)
    document = Document.from_spec(course_spec, ds)

    assert document.source_file.as_posix().endswith("/course/foo.png")
    assert document.target_dir_fragment == "img"
    assert document.prog_lang == "python"
    assert document.file_num == 1


def test_document_from_spec_for_folder(course_spec):
    ds = DocumentSpec("my-folder", "data", "Folder", 1)
    document = Document.from_spec(course_spec, ds)

    assert document.source_file.as_posix().endswith("/course/my-folder")
    assert document.target_dir_fragment == "data"
    assert document.prog_lang == "python"
    assert document.file_num == 1
