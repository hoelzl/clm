from clm.core.dependency import find_dependencies
from clm.data_sinks.plain_file_data_sink import PlainFileDataSink


def test_plain_file_data_source_target_name(
    plain_file_data_source, python_course, completed_output_spec
):
    assert (
        plain_file_data_source.get_target_name(python_course, completed_output_spec)
        == "python_file.py"
    )


def test_plain_file_data_source_process(
    plain_file_data_source, python_course, completed_output_spec
):
    data_sink = plain_file_data_source.process(python_course, completed_output_spec)
    assert isinstance(data_sink, PlainFileDataSink)
    assert data_sink.data_source == plain_file_data_source
    assert data_sink.target_loc == (
        python_course.target_loc / "public/Notebooks/Folien/Intro/python_file.py"
    )
    assert not data_sink.target_loc.exists() or data_sink.target_loc.is_dir()


def test_plain_file_data_source_dependencies(plain_file_data_source, python_course):
    assert find_dependencies(plain_file_data_source, python_course) == []


def test_plain_file_data_source_dependencies_for_pu_file(
    pu_file_data_source, python_course
):
    loc = pu_file_data_source.source_loc
    assert find_dependencies(pu_file_data_source, python_course) == [
        (loc, loc.with_suffix(".svg"))
    ]


def test_plain_file_data_source_dependencies_for_drawio_file(
    drawio_file_data_source, python_course
):
    loc = drawio_file_data_source.source_loc
    assert find_dependencies(drawio_file_data_source, python_course) == [
        (loc, loc.with_suffix(".svg"))
    ]
