from src.settings import BaseSchema

from src.schemas.format import GlobalResponseModel

###### 생성 #######
class SimulationCreateRequest(BaseSchema):
    simulation_name: str
    simulation_description: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "simulationName": "simulation1",
                "simulationDescription": "시뮬레이션1 입니다~~"
            }
        }
    }

class SimulationCreateResponse(BaseSchema):
    simulation_id : int
    simulation_name: str
    simulation_description: str

class SimulationCreateResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": 201,
                "data": {
                    "simulationId": 1,
                    "simulationName": "simulation1",
                    "simulationDescription": "시뮬레이션1 입니다~~"
                },
                "message": "시뮬레이션 생성 성공"
            }
        }
    }

    pass


###### 목록 조회 #######
class SimulationListResponse(BaseSchema):
    simulation_id : int
    simulation_name: str
    simulation_description: str
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
                    "simulationCreatedAt": "2024-11-18 09:41:31.405853",
                    "simulationStatus": "RUNNING" #TODO: 수정
                    }
                ],
                "message": "시뮬레이션 목록 조회 성공"
            }
        }
    }

    pass


###### 실행 #######
class SimulationControlRequest(BaseSchema):
    simulation_id : int
    action: str

    model_config = {
        "json_schema_extra": {
            "example":
                {
                    "simulationId": 1,
                    "action": "start"
                }
        }
    }

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
                "message": "시뮬레이션 {action} 성공"
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
                "message": "시뮬레이션 삭제 성공"
            }
        }
    }

    pass




