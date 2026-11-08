"""An ``<include>``d diagram's job must name the file that exists on disk.

``input_file`` is what Docker workers read through the /source mount, and a
topic's virtual ``drawio/x.drawio`` is not on disk, so the job failed in Docker.
"""

import pytest

from clm.core.course_file import CourseFile
from clm.core.operations.convert_drawio_file import ConvertDrawIoFileOperation
from clm.core.operations.convert_plantuml_file import ConvertPlantUmlFileOperation


@pytest.mark.parametrize(
    ("operation", "as_path", "content"),
    [
        (ConvertDrawIoFileOperation, "drawio/shared.drawio", "<mxfile/>"),
        (ConvertPlantUmlFileOperation, "pu/shared.pu", "@startuml\n@enduml\n"),
    ],
)
async def test_included_diagram_job_names_the_real_source(
    course_1, topic_1, tmp_path, operation, as_path, content
):
    origin = tmp_path / "owner" / as_path
    origin.parent.mkdir(parents=True)
    origin.write_text(content, encoding="utf-8")
    virtual_path = topic_1.path / as_path

    file = CourseFile.from_virtual(
        course_1, virtual_path=virtual_path, source_origin=origin, topic=topic_1
    )
    output = topic_1.path / "img-generated" / "shared.png"

    payload = await operation(input_file=file, output_file=output).payload()

    assert payload.input_file == str(origin)
    assert payload.input_file_name == "shared" + origin.suffix
    assert payload.output_file == str(output)
    assert payload.data == content
