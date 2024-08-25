from typing import Literal, Union

from pydantic import BaseModel

class NotebookPayload(BaseModel):
    data: str
    output_type: str
    prog_lang: str
    language: str
    notebook_format: str

class NotebookResult(BaseModel):
    result_type: Literal["result"] = "result"
    result: str

class NotebookError(BaseModel):
    result_type: Literal["error"] = "error"
    error: str

NotebookResultOrError = Union[NotebookResult, NotebookError]
