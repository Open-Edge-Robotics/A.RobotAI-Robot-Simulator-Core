from typing import List, Optional, Union
from datetime import datetime
from pydantic import BaseModel, Field
from .format import BaseSchema, GlobalResponseModel

# ------------------------------
# ExecutionPlan DTOs
# ------------------------------

class SequentialStepPlan(BaseSchema):
    step_order: int = Field(..., alias="stepOrder")
    template_id: int = Field(..., alias="templateId")
    template_name: str = Field(..., alias="templateName")
    agent_count: int = Field(..., alias="agentCount")
    repeat_count: int = Field(..., alias="repeatCount")
    execution_time: int = Field(..., alias="executionTime")
    delay_after_completion: Optional[int] = Field(0, alias="delayAfterCompletion")


class ParallelGroupPlan(BaseSchema):
    group_id: int = Field(..., alias="groupId")
    template_id: int = Field(..., alias="templateId")
    template_name: str = Field(..., alias="templateName")
    agent_count: int = Field(..., alias="agentCount")
    repeat_count: int = Field(..., alias="repeatCount")
    execution_time: int = Field(..., alias="executionTime")


class ExecutionPlan(BaseSchema):
    pattern_type: str = Field(..., alias="patternType")
    total_steps: Optional[int] = Field(None, alias="totalSteps")
    steps: Optional[List[SequentialStepPlan]] = None
    total_groups: Optional[int] = Field(None, alias="totalGroups")
    groups: Optional[List[ParallelGroupPlan]] = None

# ------------------------------
# CurrentStatus DTOs
# ------------------------------

class CurrentTimestamps(BaseSchema):
    created_at: Optional[datetime] = Field(None, alias="createdAt")
    started_at: Optional[datetime] = Field(None, alias="startedAt")
    last_updated: datetime = Field(..., alias="lastUpdated")
    estimated_completion: Optional[datetime] = Field(None, alias="estimatedCompletion")
    completed_at: Optional[datetime] = Field(None, alias="completedAt")
    failed_at: Optional[datetime] = Field(None, alias="failedAt")
    stopped_at: Optional[datetime] = Field(None, alias="stoppedAt")
    total_duration: Optional[int] = Field(None, alias="totalDuration")


# ------------------------------
# Progress DTOs
# ------------------------------

class BaseProgress(BaseSchema):
    overall_progress: float = Field(..., alias="overallProgress")
    failed_at: Optional[int] = Field(None, alias="failedAt")
    stopped_at: Optional[int] = Field(None, alias="stoppedAt")


class SequentialProgress(BaseProgress):
    current_step: Optional[int] = Field(None, alias="currentStep")
    completed_steps: Optional[int] = Field(None, alias="completedSteps")
    total_steps: Optional[int] = Field(None, alias="totalSteps")
    ready_to_start: Optional[bool] = Field(None, alias="readyToStart")


class ParallelProgress(BaseProgress):
    completed_groups: Optional[int] = Field(None, alias="completedGroups")
    running_groups: Optional[int] = Field(None, alias="runningGroups")
    total_groups: Optional[int] = Field(None, alias="totalGroups")


# ------------------------------
# Step / Group Details
# ------------------------------

class StepDetail(BaseSchema):
    step_order: int = Field(..., alias="stepOrder")
    status: str
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
    status: str
    progress: float
    started_at: datetime = Field(..., alias="startedAt")
    active_autonomous_agents: int = Field(..., alias="activeAgents")
    total_autonomous_agents: int = Field(..., alias="totalAgents")
    current_repeat: Optional[int] = Field(None, alias="currentRepeat")
    total_repeats: Optional[int] = Field(None, alias="totalRepeats")


class CompletedResults(BaseSchema):
    total_agents_executed: int = Field(..., alias="totalAgentsExecuted")
    total_requests: int = Field(..., alias="totalRequests")
    success_rate: float = Field(..., alias="successRate")
    avg_response_time: float = Field(..., alias="avgResponseTime")
    total_data_processed: str = Field(..., alias="totalDataProcessed")


class StepSummary(BaseSchema):
    step_order: int = Field(..., alias="stepOrder")
    status: str
    duration: int
    agents_executed: int = Field(..., alias="agentsExecuted")
    requests_processed: int = Field(..., alias="requestsProcessed")
    success_rate: float = Field(..., alias="successRate")


class ErrorDetails(BaseSchema):
    step: int
    failed_agents: int = Field(..., alias="failedAgents")
    reason: str
    recommended_action: str = Field(..., alias="recommendedAction")


class ErrorInfo(BaseSchema):
    error_code: str = Field(..., alias="errorCode")
    error_message: str = Field(..., alias="errorMessage")
    error_details: ErrorDetails = Field(..., alias="errorDetails")


class StopReason(BaseSchema):
    reason: str
    requested_by: str = Field(..., alias="requestedBy")
    message: str
    can_resume: bool = Field(..., alias="canResume")


# ------------------------------
# 최상위 CurrentStatus
# ------------------------------

class CurrentStatus(BaseSchema):
    status: str
    progress: Optional[Union[SequentialProgress, ParallelProgress]] = None
    timestamps: CurrentTimestamps
    message: Optional[str] = None
    step_details: Optional[List[StepDetail]] = Field(None, alias="stepDetails")
    group_details: Optional[List[GroupDetail]] = Field(None, alias="groupDetails")
    results: Optional[CompletedResults] = None
    step_summary: Optional[List[StepSummary]] = Field(None, alias="stepSummary")
    error: Optional[ErrorInfo] = None
    stop_reason: Optional[StopReason] = Field(None, alias="stopReason")


# ------------------------------
# 최종 API Response
# ------------------------------

class SimulationStatusResponse(GlobalResponseModel):
    data: Optional[dict]
