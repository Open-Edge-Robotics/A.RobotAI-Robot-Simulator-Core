from typing import Dict

from src.models.instance import Instance
from pydantic import BaseModel


class InstanceResponseModel(Instance):
    """
        This class is used to validate the response when getting Instance objects
    """
    pass


class InstanceCreateModel(BaseModel):
    name: str
    description: str
    templateSet: Dict[int, int] # 템플릿 이름, 개수

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "instance1",
                "description": "instance1 입니다~~",
                "templateSet": {1: 20, 2: 30, 3: 10}
            }
        }
    }

class InstanceGetModel(BaseModel):
    id: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "1"
            }
        }
    }
