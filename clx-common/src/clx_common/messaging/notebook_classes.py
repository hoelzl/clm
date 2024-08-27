from pydantic import Field
from typing import Literal, Union

from clx_common.messaging.base_classes import Payload, ProcessingError, Result


class NotebookPayload(Payload):
    data: str
    kind: str
    prog_lang: str
    language: str
    format: str
    other_files: dict[str, str]

    # The backend relies on having a data property
    @property
    def notebook_text(self) -> str:
        return self.data


class NotebookResult(Result):
    result_type: Literal["result"] = "result"
    result: str

NotebookResultOrError = Union[NotebookResult, ProcessingError]
