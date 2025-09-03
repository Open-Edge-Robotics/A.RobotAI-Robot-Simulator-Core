from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Union
from datetime import datetime

from .format import GlobalResponseModel
from settings import BaseSchema

# 공통 모델
class TimestampModel(BaseSchema):
    created_at: datetime
    last_updated: datetime

class ProgressModel(BaseSchema):
    overall_progress: Optional[float] = 0.0
    ready_to_start: Optional[bool] = False

# INITIATING 상태용
class CurrentStatusInitiating(BaseModel):
    status: str
    timestamps: TimestampModel

# READY 상태용
class CurrentStatusReady(BaseModel):
    status: str
    progress: ProgressModel
    timestamps: TimestampModel

# Execution Plan
class StepModel(BaseSchema):
    step_order: int
    template_id: int
    template_type: str
    autonomous_agent_count: int
    repeat_count: int
    execution_time: int
    delay_after_completion: Optional[int] = 0

class GroupModel(BaseSchema):
    template_id: int
    template_type: str
    autonomous_agent_count: int
    repeat_count: int
    execution_time: int

class ExecutionPlanSequential(BaseSchema):
    steps: List[StepModel]

class ExecutionPlanParallel(BaseSchema):
    groups: List[GroupModel]

# 최종 Simulation Response
class SimulationData(BaseModel):
    simulation_id: int = Field(..., alias="simulationId")
    simulation_name: str = Field(..., alias="simulationName")
    simulation_description: str = Field(..., alias="simulationDescription")
    pattern_type: str = Field(..., alias="patternType")
    mec_id: str = Field(..., alias="mecId")
    namespace: str
    created_at: datetime = Field(..., alias="createdAt")
    execution_plan: Union[ExecutionPlanSequential, ExecutionPlanParallel] = Field(
        ..., alias="executionPlan"
    )  # Sequential/Parallel
    current_status: Union[CurrentStatusInitiating, CurrentStatusReady] = Field(
        ..., alias="currentStatus"
    )  # INITIATING / READY

    class Config:
        allow_population_by_field_name = True  # Python 이름(snake_case)으로도 채울 수 있음
        alias_generator = None                 # 자동 alias 변환 없음
        populate_by_name = True                # dict(by_alias=True)로 직렬화 가능
  # INITIATING / READY

class SimulationResponseModel(GlobalResponseModel):    
    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": "200",
                "data": {
                    "simulationId": 1,
                    "simulationName": "순차 시뮬레이션",
                    "simulationDescription": "2단계로 구성된 순차 시뮬레이션",
                    "patternType": "sequential",
                    "mecId": "mec-01",
                    "namespace": "simulation-1",
                    "createdAt": "2025-08-19T04:49:05.647402",
                    "executionPlan": {
                    "steps": [
                        {
                        "stepOrder": 1,
                        "templateId": 1,
                        "templateType": "robot-arm",
                        "agentCount": 1,
                        "repeatCount": 1,
                        "executionTime": 100,
                        "delayAfterCompletion": 0
                        },
                        {
                        "stepOrder": 2,
                        "templateId": 1,
                        "templateType": "robot-arm",
                        "agentCount": 1,
                        "repeatCount": 1,
                        "executionTime": 100,
                        "delayAfterCompletion": 0
                        }
                    ]
                    },
                    "currentStatus": {
                    "status": "READY",
                    "progress": {
                        "overallProgress": 0,
                        "readyToStart": True
                    },
                    "timestamps": {
                        "createdAt": "2025-08-19T04:49:05.647402",
                        "lastUpdated": "2025-08-19T04:49:06.564660"
                    }
                    }
                },
                "message": "1번 시뮬레이션 상세정보 조회 성공"
            }
        }
    }

    pass
