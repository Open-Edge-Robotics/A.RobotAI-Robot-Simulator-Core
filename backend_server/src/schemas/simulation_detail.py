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
    overall_progress: float = Field(
        0.0,
        alias="overallProgress",
        description="시뮬레이션 전체 진행률 (0.0 ~ 1.0)"
    )
    
class ExecutionStatusModel(BaseSchema):
    execution_id: int = Field(
        ...,
        alias="executionId",
        description="실행 식별자"
    )
    status: str = Field(
        ...,
        alias="status",
        description="실행 상태 (예: RUNNING, COMPLETED, FAILED)"
    )
    timestamps: TimestampModel = Field(
        ...,
        alias="timestamps",
        description="실행의 생성 및 갱신 시각 정보"
    )
    progress: Optional[ProgressModel] = Field(
        None,
        alias="progress",
        description="진행률 정보 (실행 중일 때만 존재)"
    )


# INITIATING 상태용
class CurrentStatusInitiating(BaseModel):
    status: str
    timestamps: TimestampModel

# PENDING 상태용
class CurrentStatusPENDING(BaseModel):
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
    group_id: int
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
class SimulationData(BaseSchema):
    simulation_id: int = Field(
        ...,
        alias="simulationId",
        description="시뮬레이션 식별자"
    )
    simulation_name: str = Field(
        ...,
        alias="simulationName",
        description="시뮬레이션 이름"
    )
    simulation_description: str = Field(
        ...,
        alias="simulationDescription",
        description="시뮬레이션 설명"
    )
    pattern_type: str = Field(
        ...,
        alias="patternType",
        description="시뮬레이션 패턴 타입 ('sequential' 또는 'parallel')"
    )
    mec_id: str = Field(
        ...,
        alias="mecId",
        description="MEC 식별자"
    )
    namespace: str = Field(
        ...,
        alias="namespace",
        description="시뮬레이션이 실행되는 네임스페이스"
    )
    created_at: datetime = Field(
        ...,
        alias="createdAt",
        description="시뮬레이션 생성 시각"
    )
    execution_plan: Union[ExecutionPlanSequential, ExecutionPlanParallel] = Field(
        ...,
        alias="executionPlan",
        description="패턴에 따른 실행 계획 정보"
    )
    latest_execution_status: Optional[ExecutionStatusModel] = Field(
        None,
        alias="latestExecutionStatus",
        description="가장 최근 실행의 상태 정보"
    )

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
                    "status": "PENDING",
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
