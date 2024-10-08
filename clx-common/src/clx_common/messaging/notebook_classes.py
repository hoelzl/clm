import hashlib
from typing import Literal, Union

from clx_common.messaging.base_classes import Payload, ProcessingError, Result


def notebook_metadata(kind, prog_lang, language, output_format) -> str:
    return f"{kind}:{prog_lang}:{language}:{output_format}"


def notebook_metadata_tags(
    kind, prog_lang, language, output_format
) -> tuple[str, str, str, str]:
    return kind, prog_lang, language, output_format


class NotebookPayload(Payload):
    data: str
    kind: str
    prog_lang: str
    language: str
    format: str
    other_files: dict[str, bytes]

    # The backend relies on having a data property
    @property
    def notebook_text(self) -> str:
        return self.data

    def content_hash(self) -> str:
        hash_data = f"{self.output_metadata()}:{self.data}".encode("utf-8")
        return hashlib.sha256(hash_data).hexdigest()

    def output_metadata(self) -> str:
        return notebook_metadata(self.kind, self.prog_lang, self.language, self.format)


class NotebookResult(Result):
    result_type: Literal["result"] = "result"
    result: str
    output_metadata_tags: tuple[str, str, str, str]

    def result_bytes(self) -> bytes:
        return self.result.encode("utf-8")

    def output_metadata(self) -> str:
        return ":".join(self.output_metadata_tags)


NotebookResultOrError = Union[NotebookResult, ProcessingError]
