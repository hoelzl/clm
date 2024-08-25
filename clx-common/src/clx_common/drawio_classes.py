from typing import Literal, Union

from pydantic import BaseModel

class DrawioPayload(BaseModel):
    data: str
    output_format: str = "png"

class DrawioResult(BaseModel):
    result_type: Literal["result"] = "result"
    image_format: str = "png"
    result: bytes

class DrawioError(BaseModel):
    result_type: Literal["error"] = "error"
    error: str

DrawioResultOrError = Union[DrawioResult, DrawioError]
