import logging

from attrs import frozen

from clx.operations.convert_source_output_file import ConvertSourceOutputFileOperation
from clx_common.messaging.drawio_classes import DrawioPayload

logger = logging.getLogger(__name__)


@frozen
class ConvertDrawIoFileOperation(ConvertSourceOutputFileOperation):
    def object_type(self) -> str:
        return "DrawIO file"

    @property
    def service_name(self) -> str:
        return "drawio-converter"

    def payload(self) -> DrawioPayload:
        with open(self.input_file.path, "r", encoding="utf-8") as f:
            data = f.read()
        return DrawioPayload(
            data=data, input_file=self.input_file.path, output_file=self.output_file
        )
