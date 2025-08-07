from datetime import datetime
from typing import List, Optional, Union
from pydantic import BaseModel, Field, field_validator, validator

from .format import GlobalResponseModel
from settings import BaseSchema
from utils.my_enum import API


###### 생성 #######
# 1) Sequential 패턴용 Step DTO
class SequentialStep(BaseModel):
    step_order: int = Field(..., ge=1, description="단계 순서 (1부터 시작)", examples=[1])
    template_id: int = Field(..., description="템플릿 ID", examples=[1])
    autonomous_agent_count: int = Field(..., ge=1, description="해당 단계 자율행동체 수", examples=[5])
    execution_time: int = Field(..., ge=1, description="단계 실행 시간(초)", examples=[1800])
    delay_after_completion: int = Field(0, ge=0, description="단계 완료 후 지연 시간(초)", examples=[300])
    repeat_count: int = Field(..., ge=1, description="동작 실행 반복 횟수", examples=[1])

# 2) Parallel 패턴용 Agent DTO
class ParallelAgent(BaseModel):
    template_id: int = Field(..., description="템플릿 ID", examples=[1])
    autonomous_agent_count: int = Field(..., ge=1, description="자율행동체 수", examples=[6])
    execution_time: int = Field(..., ge=1, description="실행 시간(초)", examples=[7200])
    repeat_count: int = Field(..., ge=1, description="동작 실행 반복 횟수", examples=[1])

# 4) 패턴 타입 별 패턴 데이터 DTO
class SequentialPattern(BaseModel):
    steps: List[SequentialStep] = Field(
        ..., 
        description="순차 실행 단계 리스트",
        examples=[[{
            "step_order": 1,
            "template_id": 1,
            "autonomous_agent_count": 5,
            "execution_time": 1800,
            "delay_after_completion": 300
        }]]
    )

class ParallelPattern(BaseModel):
    agents: List[ParallelAgent] = Field(
        ..., 
        description="병렬 실행 에이전트 리스트",
        examples=[[{
            "template_id": 1,
            "autonomous_agent_count": 6,
            "execution_time": 7200
        }]]
    )

# 5) 상위 SimulationCreateRequest DTO
class SimulationCreateRequest(BaseModel):
    simulation_name: str = Field(..., description="시뮬레이션 이름", examples=["순차 실행 시뮬레이션 테스트"])
    simulation_description: str = Field(..., description="시뮬레이션 설명", examples=["3단계로 나누어 순차적으로 실행하는 시뮬레이션"])
    pattern_type: str = Field(..., description="시뮬레이션 패턴 타입 ('sequential' 또는 'parallel')", examples=["sequential"])
    mec_id: str = Field(..., description="MEC 식별자", examples=["mec-01"])
    
    pattern: Union[SequentialPattern, ParallelPattern] = Field(..., description="패턴별 상세 실행 계획")

    @field_validator('pattern')
    @classmethod
    def validate_pattern(cls, v, info):
        values = info.data
        pattern_type = values.get('pattern_type')
        if pattern_type == 'sequential' and not isinstance(v, SequentialPattern):
            raise ValueError('pattern_type이 sequential일 때 pattern은 SequentialPattern이어야 합니다.')
        if pattern_type == 'parallel' and not isinstance(v, ParallelPattern):
            raise ValueError('pattern_type이 parallel일 때 pattern은 ParallelPattern이어야 합니다.')
        return v


class SimulationCreateResponse(BaseSchema):
    simulation_id: int
    simulation_name: str
    simulation_description: str
    pattern_type: str 
    status: str 
    simulation_namespace: str
    mec_id: Optional[str]
    created_at: str
    total_expected_pods: int
    pod_creation_status: str 

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