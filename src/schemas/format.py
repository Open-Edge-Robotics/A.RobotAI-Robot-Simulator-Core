from typing import List

from src.settings import BaseSchema


class GlobalResponseModel(BaseSchema):
    statusCode: int
    data: List | dict | None
    message: str | dict


    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": "String",
                "data": {},
                "message": "String"
            }
        }
    }