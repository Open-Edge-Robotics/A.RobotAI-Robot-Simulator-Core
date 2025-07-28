from datetime import datetime
from typing import Optional
from pydantic import Field, validator

from .format import GlobalResponseModel
from settings import BaseSchema
from utils.my_enum import API


###### 생성 #######
class SimulationCreateRequest(BaseSchema):
    # 기본 정보
    simulation_name: str = Field(examples=["simulation1"])
    simulation_description: str = Field(examples=["시뮬레이션1 입니다~~"])

    # 패턴 설정
    template_id: int = Field(examples=[1], description="템플릿 ID")
    autonomous_agent_count: int = Field(examples=[5], description="자율행동체 개수", ge=1)
    execution_time: int = Field(examples=[300], description="실행 시간(초)", ge=1)
    delay_time: Optional[int] = Field(None, examples=[10], description="지연 시간(초)", ge=0)
    repeat_count: Optional[int] = Field(None, examples=[3], description="반복 횟수 (None이면 무한 반복)", ge=1)

    # 시작/종료 시간 설정
    scheduled_start_time: Optional[datetime] = Field(None, description="시작 시간")
    scheduled_end_time: Optional[datetime] = Field(None, description="종료 시간")

    # MEC 선택
    mec_id: Optional[str] = Field(None, examples=["mec-01"], description="MEC 식별자")

    @validator('scheduled_end_time')
    def validate_end_time(cls, v, values):
        if v and 'scheduled_start_time' in values and values['scheduled_start_time']:
            if v <= values['scheduled_start_time']:
                raise ValueError('종료 시간은 시작 시간보다 늦어야 합니다')
        return v


class SimulationCreateResponse(BaseSchema):
    simulation_id: int
    simulation_name: str
    simulation_description: str
    simulation_namespace: str
    template_id: int
    autonomous_agent_count: int
    execution_time: int
    delay_time: Optional[int]
    repeat_count: Optional[int]
    scheduled_start_time: Optional[str]
    scheduled_end_time: Optional[str]
    mec_id: Optional[str]


class SimulationCreateResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": 201,
                "data": {
                    "simulationId": 1,
                    "simulationName": "simulation1",
                    "simulationDescription": "시뮬레이션1 입니다~~",
                    "simulationNamespace": "simulation-1",
                    "templateId": 1,
                    "autonomousAgentCount": 5,
                    "executionTime": 300,
                    "delayTime": 10,
                    "repeatCount": 3,
                    "scheduledStartTime": "2024-12-01T09:00:00",
                    "scheduledEndTime": "2024-12-01T18:00:00",
                    "mecId": "mec-01"
                },
                "message": API.CREATE_SIMULATION.value
            }
        }
    }

    pass


###### 목록 조회 #######
class SimulationListResponse(BaseSchema):
    simulation_id: int
    simulation_name: str
    simulation_description: str
    simulation_namespace: str
    simulation_created_at: str
    simulation_status: str
    template_id: Optional[int]
    autonomous_agent_count: Optional[int]
    execution_time: Optional[int]
    delay_time: Optional[int]
    repeat_count: Optional[int]
    scheduled_start_time: Optional[str]
    scheduled_end_time: Optional[str]
    mec_id: Optional[str]


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
                        "simulationStatus": "Active",
                        "templateId": 1,
                        "autonomousAgentCount": 5,
                        "executionTime": 300,
                        "delayTime": 10,
                        "repeatCount": 3,
                        "scheduledStartTime": "2024-12-01T09:00:00",
                        "scheduledEndTime": "2024-12-01T18:00:00",
                        "mecId": "mec-01"
                    }
                ],
                "message": API.GET_SIMULATIONS.value
            }
        }
    }

    pass


###### 패턴 설정 업데이트 #######
class SimulationPatternUpdateRequest(BaseSchema):
    template_id: Optional[int] = Field(None, description="템플릿 ID")
    autonomous_agent_count: Optional[int] = Field(None, description="자율행동체 개수", ge=1)
    execution_time: Optional[int] = Field(None, description="실행 시간(초)", ge=1)
    delay_time: Optional[int] = Field(None, description="지연 시간(초)", ge=0)
    repeat_count: Optional[int] = Field(None, description="반복 횟수 (None이면 무한 반복)", ge=1)
    scheduled_start_time: Optional[datetime] = Field(None, description="시작 시간")
    scheduled_end_time: Optional[datetime] = Field(None, description="종료 시간")
    mec_id: Optional[str] = Field(None, description="MEC 식별자")

    @validator('scheduled_end_time')
    def validate_end_time(cls, v, values):
        if v and 'scheduled_start_time' in values and values['scheduled_start_time']:
            if v <= values['scheduled_start_time']:
                raise ValueError('종료 시간은 시작 시간보다 늦어야 합니다')
        return v


class SimulationPatternUpdateResponse(BaseSchema):
    simulation_id: int
    message: str


class SimulationPatternUpdateResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": 200,
                "data": {
                    "simulationId": 1,
                    "message": "패턴 설정이 성공적으로 업데이트되었습니다"
                },
                "message": "UPDATE_SIMULATION_PATTERN"
            }
        }
    }

    pass


###### 실행 #######
class SimulationStatusResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": 200,
                "data": [
                    {
                        "simulationId": 12,
                        "runningStatus": "Running",
                        "currentLoop": 2,
                        "maxLoops": 3,
                        "elapsedTime": 150.5,
                        "remainingTime": 149.5,
                    },
                ],
                "message": API.CHECK_SIMULATION.value
            }
        }
    }


class SimulationControlRequest(BaseSchema):
    simulation_id: int = Field(examples=[1])
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