from pathlib import Path, PurePosixPath

import pytest

from clm.core.course_spec import CourseSpec
from clm.core.document_spec import DocumentSpec
from clm.specs.course_layouts import legacy_python_course_layout


@pytest.fixture
def course_files():
    return [
        PurePosixPath("/tmp/course/slides/module_10_intro/topic_10_python.py"),
        PurePosixPath("/tmp/course/slides/module_10_intro/ws_10_python.py"),
        PurePosixPath("/tmp/course/slides/module_10_intro/python_file.py"),
        PurePosixPath("/tmp/course/slides/module_10_intro/img/my_img.png"),
        PurePosixPath("/tmp/course/examples/non_affine_file.py"),
        PurePosixPath("/tmp/course/slides/module_20_data_types/topic_10_ints.py"),
        PurePosixPath("/tmp/course/slides/module_20_data_types/ws_10_ints.py"),
        PurePosixPath("/tmp/course/slides/module_20_data_types/topic_20_floats.py"),
        PurePosixPath("/tmp/course/slides/module_20_data_types/ws_20_floats.py"),
        PurePosixPath("/tmp/course/slides/module_20_data_types/topic_30_lists.py"),
        PurePosixPath("/tmp/course/slides/module_20_data_types/ws_30_lists.py"),
    ]


def _create_document_spec_data(start_index, end_index, part_index, doc_number=1):
    """Create a list of triples representing args for `DocumentSpec`.

    >>> _create_document_spec_data(1, 3, 1)
    [('/a/b/topic_1.py', 'part-1', 'Notebook', 1),
    ('/a/b/topic_2.py', 'part-1', 'Notebook', 1),
    ('/a/b/topic_3.py', 'part-1', 'Notebook', 1)]
    """
    return [
        (
            f"/a/b/topic_{index}.py",
            f"part-{part_index}",
            "Notebook",
            doc_number,
        )
        for index in range(start_index, end_index + 1)
    ]


def course_spec_1():
    document_specs = [
        DocumentSpec(*args) for args in _create_document_spec_data(1, 4, 1, 1)
    ]
    return CourseSpec(
        Path("/a"),
        Path("/out/dir"),
        legacy_python_course_layout(Path("/a")),
        document_specs=document_specs,
    )


def course_spec_2():
    document_specs = [
        DocumentSpec(*args) for args in _create_document_spec_data(3, 6, 2)
    ]
    return CourseSpec(
        Path("/a"),
        Path("/out/dir"),
        legacy_python_course_layout(Path("/a")),
        document_specs=document_specs,
    )


@pytest.fixture
def merged_course_specs():
    return course_spec_1().merge(course_spec_2())


def test_merge(merged_course_specs):
    new_specs, deleted_specs = merged_course_specs
    assert [spec.source_file for spec in new_specs] == [
        "/a/b/topic_3.py",
        "/a/b/topic_4.py",
        "/a/b/topic_5.py",
        "/a/b/topic_6.py",
    ]
    assert [spec.target_dir_fragment for spec in new_specs] == [
        "part-1",
        "part-1",
        "part-2",
        "part-2",
    ]
    assert [spec.label for spec in new_specs] == ["Notebook"] * 4
    assert [spec.file_num for spec in new_specs] == [1] * 4

    assert [spec.source_file for spec in deleted_specs] == [
        "/a/b/topic_1.py",
        "/a/b/topic_2.py",
    ]
    assert [spec.target_dir_fragment for spec in deleted_specs] == [
        "part-1",
        "part-1",
    ]
    assert [spec.label for spec in deleted_specs] == ["Notebook"] * 2
    assert [spec.file_num for spec in deleted_specs] == [1] * 2


def test_merged_spec_has_correct_lengths(merged_course_specs):
    new_specs, deleted_specs = merged_course_specs
    assert len(new_specs) == 4
    assert len(deleted_specs) == 2


def test_merged_spec_has_correct_source_files(merged_course_specs):
    new_specs, deleted_specs = merged_course_specs
    new_source_files = [spec.source_file for spec in new_specs]
    deleted_source_files = [spec.source_file for spec in deleted_specs]
    assert new_source_files == [
        "/a/b/topic_3.py",
        "/a/b/topic_4.py",
        "/a/b/topic_5.py",
        "/a/b/topic_6.py",
    ]
    assert deleted_source_files == [
        "/a/b/topic_1.py",
        "/a/b/topic_2.py",
    ]


def test_merged_spec_has_correct_target_dir_fragments(merged_course_specs):
    new_specs, deleted_specs = merged_course_specs
    new_dir_fragments = [spec.target_dir_fragment for spec in new_specs]
    deleted_dir_fragments = [spec.target_dir_fragment for spec in deleted_specs]

    assert new_dir_fragments == ["part-1", "part-1", "part-2", "part-2"]
    assert deleted_dir_fragments == ["part-1", "part-1"]
