import hashlib
from typing import Literal, Union

from clx_common.messaging.base_classes import Payload, ProcessingError, Result


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
        hash_data = (f"{self.kind}:{self.prog_lang}:{self.language}:{self.format}:"
                     f"{self.data}").encode("utf-8")
        return hashlib.sha256(hash_data).hexdigest()


class NotebookResult(Result):
    result_type: Literal["result"] = "result"
    result: str

    def result_bytes(self) -> bytes:
        return self.result.encode("utf-8")


NotebookResultOrError = Union[NotebookResult, ProcessingError]
