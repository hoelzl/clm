import logging

from attrs import frozen

from clx.backend import Backend
from clx.operations.convert_file import ConvertFileOperation

logger = logging.getLogger(__name__)


@frozen
class ConvertPlantUmlFileOperation(ConvertFileOperation):
    def object_type(self) -> str:
        return "PlantUML file"

    def backend_service(self) -> str:
        return "plantuml-converter"