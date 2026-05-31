import hashlib
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

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

    @classmethod
    def from_job_payload(
        cls,
        payload_data: Mapping[str, Any],
        *,
        content: str,
        input_file: str,
        output_file: str,
        fallback_correlation_id: str,
    ) -> Self:
        """Reconstruct a payload from a job's serialized JSON payload dict.

        The host serializes payloads with ``model_dump(mode="json")`` (see
        ``SqliteBackend``); this deserializes the *whole* dict back via
        ``model_validate``, so the round-trip is symmetric and total. A field
        added to a payload subclass therefore can never be silently dropped at
        the worker boundary — a hand-listed constructor previously dropped
        ``cross_references`` (and ``svg_available_stems`` / ``inline_images``)
        exactly this way (issue #17), leaving every ``clm:`` link unrewritten.

        Only the file-bound fields are overridden, because at consume time they
        do not come from the payload: the canonical input/output paths are
        columns on the job row, and in Docker source-mount mode the ``content``
        (notebook / diagram source) is read from the mounted filesystem rather
        than carried inline. **Do not "simplify" these overrides away** — both
        direct and Docker modes depend on them.

        A required descriptor field missing from ``payload_data`` raises a
        ``ValidationError`` rather than being silently defaulted: the host
        always sets them, so a gap means a genuinely malformed job that should
        fail loudly.
        """
        return cls.model_validate(
            {
                **dict(payload_data),
                "data": content,
                "input_file": input_file,
                "input_file_name": Path(input_file).name,
                "output_file": output_file,
                "correlation_id": payload_data.get("correlation_id", fallback_correlation_id),
            }
        )

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
