from pydantic import BaseModel
from typing import List, Union

class GlobalResponseModel(BaseModel):
    statusCode: int
    data: Union[List, dict, None]
    message: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": "String",
                "data": {},
                "message": "String"
            }
        }
    }