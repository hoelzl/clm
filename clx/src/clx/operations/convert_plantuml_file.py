import logging

from attrs import frozen

from clx.operations.convert_source_output_file import ConvertSourceOutputFileOperation
from clx_common.messaging.plantuml_classes import PlantUmlPayload

logger = logging.getLogger(__name__)


@frozen
class ConvertPlantUmlFileOperation(ConvertSourceOutputFileOperation):
    def object_type(self) -> str:
        return "PlantUML file"

    def service_name(self) -> str:
        return "plantuml-converter"

    def payload(self) -> PlantUmlPayload:
        with open(self.input_file.path, "r", encoding="utf-8") as f:
            data = f.read()
        return PlantUmlPayload(data=data, output_file=self.output_file)
