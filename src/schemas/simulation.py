from typing import Dict

from src.models.instance import Instance
from pydantic import BaseModel


class SimulationResponseModel(Instance):
    """
        This class is used to validate the response when getting Simulation objects
    """
    pass


class SimulationCreateModel(BaseModel):
    name: str
    description: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "simulation1",
                "description": "시뮬레이션1 입니다~~"
            }
        }
    }
