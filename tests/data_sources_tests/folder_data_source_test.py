from clm.core.dependency import find_dependencies
from clm.data_sinks.folder_data_sink import FolderDataSink


def test_folder_data_source_target_name(
    employee_sk_data_source, python_course, completed_output_spec
):
    assert (
        employee_sk_data_source.get_target_name(python_course, completed_output_spec)
        == "EmployeeStarterKit"
    )


def test_folder_data_source_process(
    employee_sk_data_source, python_course, completed_output_spec
):
    data_sink = employee_sk_data_source.process(python_course, completed_output_spec)
    assert isinstance(data_sink, FolderDataSink)
    assert data_sink.data_source == employee_sk_data_source
    assert data_sink.target_loc == (
        python_course.target_loc / "public/examples/EmployeeStarterKit"
    )
    assert not data_sink.target_loc.exists() or data_sink.target_loc.is_dir()


def test_folder_data_source_dependencies(employee_sk_data_source, python_course):
    assert employee_sk_data_source.dependencies == []
