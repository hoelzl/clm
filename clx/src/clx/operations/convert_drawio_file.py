import logging
from typing import Any

from attrs import frozen

from clx.backend import Backend
from clx.operations.convert_file import ConvertFileOperation

logger = logging.getLogger(__name__)


@frozen
class ConvertDrawIoFileOperation(ConvertFileOperation):
    def object_type(self) -> str:
        return "DrawIO file"

    @property
    def backend_service(self) -> str:
        return "drawio-converter"

