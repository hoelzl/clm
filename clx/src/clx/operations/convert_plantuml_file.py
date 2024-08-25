import logging

from attrs import frozen

from clx.operations.convert_source_output_file import ConvertSourceOutputFileOperation

logger = logging.getLogger(__name__)


@frozen
class ConvertPlantUmlFileOperation(ConvertSourceOutputFileOperation):
    def object_type(self) -> str:
        return "PlantUML file"

    def backend_service(self) -> str:
        return "plantuml-converter"