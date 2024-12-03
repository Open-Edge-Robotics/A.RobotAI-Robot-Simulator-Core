from typing import List

from src.settings import BaseSchema


class GlobalResponseModel(BaseSchema):
    status_code: int | str
    data: List | dict | None
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
