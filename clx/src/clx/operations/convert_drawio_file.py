import logging

from attrs import frozen

from clx.operations.convert_source_output_file import ConvertSourceOutputFileOperation

logger = logging.getLogger(__name__)


@frozen
class ConvertDrawIoFileOperation(ConvertSourceOutputFileOperation):
    def object_type(self) -> str:
        return "DrawIO file"

    @property
    def backend_service(self) -> str:
        return "drawio-converter"

