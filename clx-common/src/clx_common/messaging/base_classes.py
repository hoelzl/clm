from abc import ABC
from typing import Any, Literal, Union

from pydantic import BaseModel


class TransferModel(BaseModel, ABC):
    correlation_id: str

    def model_dump(self, **kwargs) -> dict[str, Any]:
        return super().model_dump(serialize_as_any=True, **kwargs)

    def model_dump_json(self, **kwargs) -> str:
        return super().model_dump_json(serialize_as_any=True, **kwargs)


class Payload(TransferModel):
    # We encode files as strings, since passing Path objects to different
    # operating systems may lead to errors. That is also the reason we need
    # the name of the input file, since it is more work to extract it in a
    # OS-neutral way than it's worth
    input_file: str
    input_file_name: str
    output_file: str
    data: str


class Result(TransferModel):
    result_type: Literal["result"] = "result"
    output_file: str


class ImageResult(Result):
    image_format: str = "png"
    result: bytes


class ProcessingError(TransferModel):
    result_type: Literal["error"] = "error"
    error: str
    input_file: str
    input_file_name: str
    output_file: str
    traceback: str = ""


ImageResultOrError = Union[ImageResult, ProcessingError]
