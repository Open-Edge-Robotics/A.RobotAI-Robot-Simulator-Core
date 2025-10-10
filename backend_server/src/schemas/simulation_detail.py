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

    @classmethod
    def from_entity(cls, execution) -> Optional["ExecutionStatusModel"]:
        """SimulationExecution 엔티티로부터 DTO 생성"""
        if not execution:
            return None

        from models.enums import SimulationExecutionStatus

        progress = None
        if execution.status == SimulationExecutionStatus.RUNNING:
            overall_progress = execution.result_summary.get("overall_progress") if execution.result_summary else None
            # overall_progress가 None인 경우 기본값 0.0 사용
            progress = ProgressModel(overall_progress=overall_progress if overall_progress is not None else 0.0)

        return cls(
            execution_id=execution.id,
            status=execution.status,
            timestamps=TimestampModel(
                created_at=execution.created_at,
                last_updated=execution.updated_at,
                started_at=execution.started_at,
                completed_at=execution.completed_at
            ),
            progress=progress
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

    @classmethod
    def from_entity(cls, step) -> "StepModel":
        """Step 엔티티로부터 DTO 생성"""
        return cls(
            step_order=step.step_order,
            template_id=step.template.template_id,
            template_type=step.template.type,
            autonomous_agent_count=step.autonomous_agent_count,
            repeat_count=step.repeat_count,
            execution_time=step.execution_time,
            delay_after_completion=step.delay_after_completion
        )

class GroupModel(BaseSchema):
    group_id: int
    template_id: int
    template_type: str
    autonomous_agent_count: int
    repeat_count: int
    execution_time: int

    @classmethod
    def from_entity(cls, group) -> "GroupModel":
        """Group 엔티티로부터 DTO 생성"""
        return cls(
            group_id=group.id,
            template_id=group.template.template_id,
            template_type=group.template.type,
            autonomous_agent_count=group.autonomous_agent_count,
            repeat_count=group.repeat_count,
            execution_time=group.execution_time
        )

class ExecutionPlanSequential(BaseSchema):
    steps: List[StepModel]

    @classmethod
    def from_entities(cls, steps: list) -> "ExecutionPlanSequential":
        """Step 엔티티 리스트로부터 DTO 생성"""
        return cls(steps=[StepModel.from_entity(step) for step in steps])

class ExecutionPlanParallel(BaseSchema):
    groups: List[GroupModel]

    @classmethod
    def from_entities(cls, groups: list) -> "ExecutionPlanParallel":
        """Group 엔티티 리스트로부터 DTO 생성"""
        return cls(groups=[GroupModel.from_entity(group) for group in groups])

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

    @classmethod
    def from_entity(
        cls,
        simulation,
        execution_plan: Union[ExecutionPlanSequential, ExecutionPlanParallel],
        latest_execution = None
    ) -> "SimulationData":
        """Simulation 엔티티로부터 DTO 생성"""
        return cls(
            simulation_id=simulation.id,
            simulation_name=simulation.name,
            simulation_description=simulation.description,
            pattern_type=simulation.pattern_type,
            mec_id=simulation.mec_id,
            namespace=simulation.namespace,
            created_at=simulation.created_at,
            execution_plan=execution_plan,
            latest_execution_status=ExecutionStatusModel.from_entity(latest_execution)
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
