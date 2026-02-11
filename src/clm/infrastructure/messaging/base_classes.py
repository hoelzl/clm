import hashlib
from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field


class ProcessingWarning(BaseModel):
    """A warning that occurred during processing.

    Warnings represent non-fatal issues that should be reported to the user
    but don't prevent processing from completing (or failing for other reasons).
    """

    category: str
    message: str
    severity: Literal["high", "medium", "low"] = "medium"
    file_path: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


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

    def content_hash(self) -> str:
        return hashlib.sha256(self.data.encode("utf-8")).hexdigest()

    def output_metadata(self) -> str:
        return "default"


class ImagePayload(Payload):
    output_format: str = "png"

    def output_metadata(self) -> str:
        return self.output_format


class Result(TransferModel):
    result_type: Literal["result"] = "result"
    output_file: str
    input_file: str
    content_hash: str
    warnings: list[ProcessingWarning] = Field(default_factory=list)

    @abstractmethod
    def result_bytes(self) -> bytes: ...

    @abstractmethod
    def output_metadata(self) -> str: ...


class ImageResult(Result):
    image_format: str = "png"
    result: bytes

    def result_bytes(self) -> bytes:
        return self.result

    def output_metadata(self) -> str:
        return self.image_format


class ProcessingError(TransferModel):
    result_type: Literal["error"] = "error"
    error: str
    input_file: str
    input_file_name: str
    output_file: str
    traceback: str = ""
    warnings: list[ProcessingWarning] = Field(default_factory=list)


ImageResultOrError = ImageResult | ProcessingError
