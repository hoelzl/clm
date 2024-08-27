from pathlib import Path
from typing import Any, Literal, Union

from pydantic import BaseModel

class TransferModel(BaseModel):
    def model_dump(self, **kwargs) -> dict[str, Any]:
        return super().model_dump(serialize_as_any=True, **kwargs)

    def model_dump_json(self, **kwargs) -> str:
        return super().model_dump_json(serialize_as_any=True, **kwargs)

class Payload(TransferModel):
    pass

class Result(TransferModel):
    result_type: Literal["result"] = "result"
    output_file: Path

class ImageResult(Result):
    image_format: str = "png"
    result: bytes

class ProcessingError(TransferModel):
    result_type: Literal["error"] = "error"
    error: str

ImageResultOrError = Union[ImageResult, ProcessingError]
