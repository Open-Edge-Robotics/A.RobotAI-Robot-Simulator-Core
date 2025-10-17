from typing import Dict, List, Optional, Union
from datetime import datetime
from pydantic import BaseModel, Field

from models.enums import GroupStatus, SimulationStatus, StepStatus
from .format import BaseSchema, GlobalResponseModel

# ------------------------------
# CurrentStatus DTOs
# ------------------------------

class CurrentTimestamps(BaseSchema):
    created_at: Optional[datetime] = Field(None, alias="createdAt")
    last_updated: datetime = Field(None, alias="lastUpdated")
    started_at: Optional[datetime] = Field(None, alias="startedAt")
    completed_at: Optional[datetime] = Field(None, alias="completedAt")
    failed_at: Optional[datetime] = Field(None, alias="failedAt")
    stopped_at: Optional[datetime] = Field(None, alias="stoppedAt")


# ------------------------------
# Progress DTOs
# ------------------------------

class BaseProgress(BaseSchema):
    overall_progress: float = Field(..., alias="overallProgress")


class SequentialProgress(BaseProgress):
    current_step: Optional[int] = Field(None, alias="currentStep")
    completed_steps: Optional[int] = Field(None, alias="completedSteps")
    total_steps: Optional[int] = Field(None, alias="totalSteps")


class ParallelProgress(BaseProgress):
    completed_groups: Optional[int] = Field(None, alias="completedGroups")
    running_groups: Optional[int] = Field(None, alias="runningGroups")
    total_groups: Optional[int] = Field(None, alias="totalGroups")


# ------------------------------
# Step / Group Details
# ------------------------------

class StepDetail(BaseSchema):
    step_order: int = Field(..., alias="stepOrder", frozen = True)
    status: StepStatus
    progress: float = 0.0
    started_at: Optional[datetime] = Field(None, alias="startedAt")
    completed_at: Optional[datetime] = Field(None, alias="completedAt")
    failed_at: Optional[datetime] = Field(None, alias="failedAt")
    stopped_at: Optional[datetime] = Field(None, alias="stoppedAt")
    autonomous_agents: Optional[int] = Field(0, alias="autonomousAgents")
    current_repeat: Optional[int] = Field(0, alias="currentRepeat")
    total_repeats: Optional[int] = Field(0, alias="totalRepeats")
    error: Optional[str] = None


class GroupDetail(BaseSchema):
    group_id: int = Field(..., alias="groupId")
    status: GroupStatus
    progress: float = 0.0
    started_at: Optional[datetime] = Field(None, alias="startedAt")
    completed_at: Optional[datetime] = Field(None, alias="completedAt")
    failed_at: Optional[datetime] = Field(None, alias="failedAt")
    stopped_at: Optional[datetime] = Field(None, alias="stoppedAt")
    autonomous_agents: Optional[int] = Field(0, alias="autonomousAgents")
    current_repeat: Optional[int] = Field(0, alias="currentRepeat")
    total_repeats: Optional[int] = Field(0, alias="totalRepeats")
    error: Optional[str] = None

# ------------------------------
# 최상위 CurrentStatus
# ------------------------------

class CurrentStatus(BaseSchema):
    status: SimulationStatus
    progress: Optional[Union[SequentialProgress, ParallelProgress]] = None
    timestamps: CurrentTimestamps
    message: Optional[str] = None
    step_details: Optional[List[StepDetail]] = Field(None, alias="stepDetails")
    group_details: Optional[List[GroupDetail]] = Field(None, alias="groupDetails")

# ------------------------------
# 최종 API Response
# ------------------------------

class SimulationStatusResponse(GlobalResponseModel):
    data: Optional[dict]

# ------------------------------
# 시뮬레이션 삭제 상태 Response
# ------------------------------
class SimulationDeletionStatusData(BaseSchema):
    simulation_id: int = Field(..., alias="simulationId")
    status: str  # PENDING, RUNNING, COMPLETED, FAILED
    progress: int  # 0~100
    steps: Dict[str, str]  # namespace, redis, db 단계별 상태
    started_at: Optional[datetime] = Field(None, alias="startedAt")
    completed_at: Optional[datetime] = Field(None, alias="completedAt")
    error_message: Optional[str] = Field(None, alias="errorMessage")
    
class SimulationDeletionStatusResponse(GlobalResponseModel):
    data: Optional[dict]
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": "200",
                "data": {
                    "simulationId": 1,
                    "status": "RUNNING",
                    "progress": 33,
                    "steps": {
                        "namespace": "COMPLETED",
                        "redis": "PENDING",
                        "db": "PENDING"
                    },
                    "startedAt": "2025-09-09T12:00:00Z",
                    "completedAt": None,
                    "errorMessage": None
                },
                "message": "시뮬레이션 삭제 진행 상태 조회 성공"
            }
        }
    }
    