from typing import Generic, TypeVar, Optional
from pydantic.generics import GenericModel
from settings import BaseSchema

T = TypeVar("T")

class GlobalResponseModel(BaseSchema, GenericModel, Generic[T]):
    status_code: int | str
    data: Optional[T] = None
    message: str | dict

    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": 200,
                "data": {},
                "message": "String"
            }
        }
    }
