from typing import Literal, Union

from clx_common.messaging.base_classes import Payload, ProcessingError, Result


class NotebookPayload(Payload):
    notebook_text: str
    notebook_path: str
    kind: str
    prog_lang: str
    language: str
    format: str
    other_files: dict[str, str]

class NotebookResult(Result):
    result_type: Literal["result"] = "result"
    result: str

NotebookResultOrError = Union[NotebookResult, ProcessingError]
