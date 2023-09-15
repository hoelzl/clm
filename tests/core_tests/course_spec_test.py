import pytest

from clm.core.course_spec import CourseSpec
from clm.core.data_source_spec import DataSourceSpec
from clm.specs.course_layouts import legacy_python_course_layout
from clm.utils.in_memory_filesystem import convert_to_in_memory_filesystem
from clm.utils.location import InMemoryLocation

_merged_file_system = convert_to_in_memory_filesystem(
    {
        "a": {
            "b": {
                "topic_3.py": "",
                "topic_4.py": "",
                "topic_5.py": "",
                "topic_6.py": "",
            }
        }
    }
)


def _create_data_source_spec_data(start_index, end_index, part_index, doc_number=1):
    """Create a list of triples representing args for a data-source spec.

    >>> _create_data_source_spec_data(1, 3, 1)
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


@pytest.fixture
def course_spec_1():
    data_source_specs = [
        DataSourceSpec(*args) for args in _create_data_source_spec_data(1, 4, 1, 1)
    ]
    return CourseSpec(
        InMemoryLocation("/tmp", "", _merged_file_system),
        InMemoryLocation("/out/dir", "", _merged_file_system),
        legacy_python_course_layout(),
        data_source_specs=data_source_specs,
    )


@pytest.fixture
def course_spec_2():
    data_source_specs = [
        DataSourceSpec(*args) for args in _create_data_source_spec_data(3, 6, 2)
    ]
    return CourseSpec(
        InMemoryLocation("/tmp", "", _merged_file_system),
        InMemoryLocation("/out/dir", "", _merged_file_system),
        legacy_python_course_layout(),
        data_source_specs=data_source_specs,
    )


def test_merge(course_spec_1, course_spec_2):
    new_specs, deleted_specs = course_spec_1.merge(course_spec_2)
    assert [spec.source_loc for spec in new_specs] == [
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

    assert [spec.source_loc for spec in deleted_specs] == [
        "/a/b/topic_1.py",
        "/a/b/topic_2.py",
    ]
    assert [spec.target_dir_fragment for spec in deleted_specs] == [
        "part-1",
        "part-1",
    ]
    assert [spec.label for spec in deleted_specs] == ["Notebook"] * 2
    assert [spec.file_num for spec in deleted_specs] == [1] * 2


def test_merged_spec_has_correct_lengths(course_spec_1, course_spec_2):
    new_specs, deleted_specs = course_spec_1.merge(course_spec_2)
    assert len(new_specs) == 4
    assert len(deleted_specs) == 2


def test_merged_spec_has_correct_source_files(course_spec_1, course_spec_2):
    new_specs, deleted_specs = course_spec_1.merge(course_spec_2)
    new_source_files = [spec.source_loc for spec in new_specs]
    deleted_source_files = [spec.source_loc for spec in deleted_specs]
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


def test_merged_spec_has_correct_target_dir_fragments(course_spec_1, course_spec_2):
    new_specs, deleted_specs = course_spec_1.merge(course_spec_2)
    new_dir_fragments = [spec.target_dir_fragment for spec in new_specs]
    deleted_dir_fragments = [spec.target_dir_fragment for spec in deleted_specs]

    assert new_dir_fragments == ["part-1", "part-1", "part-2", "part-2"]
    assert deleted_dir_fragments == ["part-1", "part-1"]
