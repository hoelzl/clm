from io import StringIO
from pathlib import Path, PurePosixPath
import pytest


@pytest.fixture
def course_files():
    return [
        PurePosixPath('/tmp/course/slides/module_10_intro/topic_10_python.py'),
        PurePosixPath('/tmp/course/slides/module_10_intro/ws_10_python.py'),
        PurePosixPath('/tmp/course/slides/module_10_intro/python_file.py'),
        PurePosixPath('/tmp/course/slides/module_10_intro/img/my_img.png'),
        PurePosixPath('/tmp/course/examples/non_affine_file.py'),
        PurePosixPath(
            '/tmp/course/slides/module_20_data_types/topic_10_ints.py'
        ),
        PurePosixPath('/tmp/course/slides/module_20_data_types/ws_10_ints.py'),
        PurePosixPath(
            '/tmp/course/slides/module_20_data_types/topic_20_floats.py'
        ),
        PurePosixPath(
            '/tmp/course/slides/module_20_data_types/ws_20_floats.py'
        ),
        PurePosixPath(
            '/tmp/course/slides/module_20_data_types/topic_30_lists.py'
        ),
        PurePosixPath(
            '/tmp/course/slides/module_20_data_types/ws_30_lists.py'
        ),
    ]


_CSV_SOURCE = """\
Base Dir:,course/
Target Dir:,output/
Template Dir:,other-course/templates/
Language:,de

/tmp/course/slides/module_10_intro/topic_10_python.py,my_dir,Notebook
/tmp/course/slides/module_10_intro/ws_10_python.py,my_dir,Notebook
/tmp/course/slides/module_10_intro/python_file.py,my_dir,Notebook
/tmp/course/slides/module_10_intro/img/my_img.png,my_dir,DataFile
/tmp/course/examples/non_affine_file.py,my_dir,DataFile
/tmp/course/slides/module_20_data_types/topic_10_ints.py,my_dir,Notebook
/tmp/course/slides/module_20_data_types/ws_10_ints.py,my_dir,Notebook
/tmp/course/slides/module_20_data_types/topic_20_floats.py,my_dir,Notebook
/tmp/course/slides/module_20_data_types/ws_20_floats.py,my_dir,Notebook
/tmp/course/slides/module_20_data_types/topic_30_lists.py,my_dir,Notebook
/tmp/course/slides/module_20_data_types/ws_30_lists.py,my_dir,Notebook
"""


@pytest.fixture
def course_spec_csv_stream():
    return StringIO(_CSV_SOURCE)


def _create_document_spec_data(
    start_index, end_index, part_index, doc_number=1
):
    """Create a list of triples representing args for `DocumentSpec`.

    >>> _create_document_spec_data(1, 3, 1)
    [('/a/b/topic_1.py', 'part-1', 'Notebook', 1),
    ('/a/b/topic_2.py', 'part-1', 'Notebook', 1),
    ('/a/b/topic_3.py', 'part-1', 'Notebook', 1)]
    """
    return [
        (f'/a/b/topic_{index}.py', f'part-{part_index}', 'Notebook', 1)
        for index in range(start_index, end_index + 1)
    ]


@pytest.fixture
def course_spec_1():
    from clm.core.course_specs import CourseSpec, DocumentSpec

    document_specs = [
        DocumentSpec(*args) for args in _create_document_spec_data(1, 4, 1, 1)
    ]
    return CourseSpec(
        Path('/a'), Path('/out/dir'), document_specs=document_specs
    )


@pytest.fixture
def course_spec_2():
    from clm.core.course_specs import CourseSpec, DocumentSpec

    document_specs = [
        DocumentSpec(*args) for args in _create_document_spec_data(3, 6, 2)
    ]
    return CourseSpec(
        Path('/a'), Path('/out/dir'), document_specs=document_specs
    )
