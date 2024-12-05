from pydantic import Field

from src.settings import BaseSchema

from src.schemas.format import GlobalResponseModel
from src.utils.my_enum import API


###### 생성 #######
class SimulationCreateRequest(BaseSchema):
    simulation_name: str = Field(examples=["simulation1"])
    simulation_description: str = Field(examples=["시뮬레이션1 입니다~~"])

class SimulationCreateResponse(BaseSchema):
    simulation_id : int
    simulation_name: str
    simulation_description: str
    simulation_namespace: str

class SimulationCreateResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": 201,
                "data": {
                    "simulationId": 1,
                    "simulationName": "simulation1",
                    "simulationDescription": "시뮬레이션1 입니다~~",
                    "simulationNamespace": "simulation-1"
                },
                "message": API.CREATE_SIMULATION.value
            }
        }
    }

    pass


###### 목록 조회 #######
class SimulationListResponse(BaseSchema):
    simulation_id : int
    simulation_name: str
    simulation_description: str
    simulation_namespace: str
    simulation_created_at : str
    simulation_status : str

class SimulationListResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": 200,
                "data": [
                    {
                        "simulationId": 1,
                        "simulationName": "simulation1",
                        "simulationDescription": "시뮬레이션1 입니다~~",
                        "simulationNamespace": "simulation-1",
                        "simulationCreatedAt": "2024-11-18 09:41:31.405853",
                        "simulationStatus": "Active"
                    }
                ],
                "message": API.GET_SIMULATIONS.value
            }
        }
    }

    pass


###### 실행 #######
class SimulationControlRequest(BaseSchema):
    simulation_id : int = Field(examples=[1])
    action: str = Field(examples=["start"])

class SimulationControlResponse(BaseSchema):
    simulation_id: int

class SimulationControlResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": 200,
                "data": {
                    "simulationId": 1,
                },
                "message": API.RUN_SIMULATION.value
            }
        }
    }

    pass


###### 삭제 #######
class SimulationDeleteResponse(BaseSchema):
    simulation_id: int

class SimulationDeleteResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": 200,
                "data": {
                    "simulationId": 1,
                },
                "message": API.DELETE_SIMULATION.value
            }
        }
    }

    pass




