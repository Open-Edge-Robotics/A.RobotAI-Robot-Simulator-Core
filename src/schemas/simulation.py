from datetime import datetime
from typing import Dict

from src.models.instance import Instance
from pydantic import BaseModel


class SimulationResponseModel(Instance):
    """
        This class is used to validate the response when getting Simulation objects
    """
    pass


class SimulationCreateModel(BaseModel):
    simulationName: str
    simulationDescription: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "simulationName": "simulation1",
                "simulationDescription": "시뮬레이션1 입니다~~"
            }
        }
    }

class SimulationListModel(BaseModel):
    simulationId : str
    simulationName: str
    simulationDescription: str
    simulationCreatedAt : str
    simulationStatus : str

    model_config = {
        "json_schema_extra": {
            "example": {
                "simulationId": "1",
                "simulationName": "simulation1",
                "simulationDescription": "시뮬레이션1 입니다~~",
                "simulationCreatedAt": "2024-11-18 09:41:31.405853",
                "simulationStatus": "RUNNING"
            }
        }
    }