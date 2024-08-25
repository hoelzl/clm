from typing import Literal, Union

from pydantic import BaseModel

class PlantUmlPayload(BaseModel):
    data: str
    output_format: str = "png"

class PlantUmlResult(BaseModel):
    result_type: Literal["result"] = "result"
    image_format: str = "png"
    result: bytes

class PlantUmlError(BaseModel):
    result_type: Literal["error"] = "error"
    error: str

PlantUmlResultOrError = Union[PlantUmlResult, PlantUmlError]