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

class ExecutionPlanSequential(BaseModel):
    steps: List[StepModel]

class ExecutionPlanParallel(BaseModel):
    groups: List[GroupModel]

# 최종 Simulation Response
class SimulationData(BaseSchema):
    simulation_id: int
    simulation_name: str
    simulation_description: str
    pattern_type: str
    mec_id: str
    namespace: str
    created_at: datetime
    execution_plan: Union[ExecutionPlanSequential, ExecutionPlanParallel]  # Sequential/Parallel
    current_status: Union[CurrentStatusInitiating, CurrentStatusReady]  # INITIATING / READY

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
