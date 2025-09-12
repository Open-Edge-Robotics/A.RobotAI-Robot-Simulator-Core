from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

# -----------------------------
# Step / Group 생성 DTO
# -----------------------------
class StepCreateDTO(BaseModel):
    step_order: int = Field(..., description="단계 순서", ge=1, alias="stepOrder", example=1)
    template_id: int = Field(..., description="템플릿 ID", alias="templateId", example=101)
    autonomous_agent_count: int = Field(..., description="자율행동체 개수", ge=1, le=50,
                                        alias="autonomousAgentCount", example=3)
    execution_time: Optional[int] = Field(None, description="실행 시간(초)", ge=60, le=86400,
                                          alias="executionTime", example=3600)
    delay_after_completion: Optional[int] = Field(None, description="완료 후 지연 시간(초)", ge=0,
                                                  alias="delayAfterCompletion", example=10)
    repeat_count: Optional[int] = Field(None, description="반복 횟수", ge=1, le=100,
                                        alias="repeatCount", example=2)

    class Config:
        populate_by_name = True


class GroupCreateDTO(BaseModel):
    template_id: int = Field(..., description="템플릿 ID", alias="templateId", example=102)
    autonomous_agent_count: int = Field(..., description="자율행동체 개수", ge=1, le=50,
                                        alias="autonomousAgentCount", example=5)
    execution_time: Optional[int] = Field(None, description="실행 시간(초)", ge=60, le=86400,
                                          alias="executionTime", example=1800)
    repeat_count: Optional[int] = Field(None, description="반복 횟수", ge=1, le=100,
                                        alias="repeatCount", example=3)

    class Config:
        populate_by_name = True


class PatternCreateRequestDTO(BaseModel):
    step: Optional[StepCreateDTO] = Field(None, description="순차 패턴 단계 생성", alias="step")
    group: Optional[GroupCreateDTO] = Field(None, description="병렬 패턴 그룹 생성", alias="group")

    @model_validator(mode="before")
    @classmethod
    def check_step_or_group(cls, values):
        step = values.get("step")
        group = values.get("group")
        if step and group:
            raise ValueError("step과 group은 동시에 사용할 수 없습니다")
        if not step and not group:
            raise ValueError("step 또는 group 중 하나는 필수입니다")
        return values

    class Config:
        populate_by_name = True


# -----------------------------
# Pattern 생성 응답 DTO
# -----------------------------
class PatternCreateResponseDTO(BaseModel):
    status_code: int = Field(..., alias="statusCode")
    data: dict = Field(..., alias="data")
    message: str


# -----------------------------
# Step / Group 수정 DTO
# -----------------------------
class StepUpdateDTO(BaseModel):
    step_id: int = Field(..., description="단계 ID", ge=1, alias="stepId", example=1)
    execution_time: Optional[int] = Field(None, description="실행 시간(초)", ge=60, le=86400,
                                          alias="executionTime", example=3600)
    delay_after_completion: Optional[int] = Field(None, description="완료 후 지연 시간(초)", ge=0,
                                                  alias="delayAfterCompletion", example=5)
    repeat_count: Optional[int] = Field(None, description="반복 횟수", ge=1, le=100,
                                        alias="repeatCount", example=2)

    @field_validator("execution_time")
    @classmethod
    def validate_execution_time(cls, v):
        if v is not None and (v < 60 or v > 86400):
            raise ValueError("실행 시간은 60초 이상 86400초 이하여야 합니다")
        return v

    class Config:
        populate_by_name = True


class GroupUpdateDTO(BaseModel):
    group_id: int = Field(..., description="그룹 ID", ge=1, alias="groupId", example=1)
    execution_time: Optional[int] = Field(None, description="실행 시간(초)", ge=60, le=86400,
                                          alias="executionTime", example=1800)
    repeat_count: Optional[int] = Field(None, description="반복 횟수", ge=1, le=100,
                                        alias="repeatCount", example=3)

    @field_validator("execution_time")
    @classmethod
    def validate_execution_time(cls, v):
        if v is not None and (v < 60 or v > 86400):
            raise ValueError("실행 시간은 60초 이상 86400초 이하여야 합니다")
        return v

    class Config:
        populate_by_name = True


class PatternUpdateRequestDTO(BaseModel):
    step_update: Optional[StepUpdateDTO] = Field(None, description="순차 패턴 단계 업데이트", alias="stepUpdate")
    group_update: Optional[GroupUpdateDTO] = Field(None, description="병렬 패턴 그룹 업데이트", alias="groupUpdate")

    @model_validator(mode="before")
    @classmethod
    def validate_step_or_group(cls, values):
        step = values.get("step_update")
        group = values.get("group_update")
        if step and group:
            raise ValueError("stepUpdate와 groupUpdate는 동시에 사용할 수 없습니다")
        if not step and not group:
            raise ValueError("stepUpdate 또는 groupUpdate 중 하나는 필수입니다")
        return values

    class Config:
        populate_by_name = True


class PatternUpdateResponseDTO(BaseModel):
    status_code: int = Field(..., alias="statusCode")
    data: dict = Field(..., alias="data")
    message: str


# -----------------------------
# Step / Group 삭제 DTO
# -----------------------------
class PatternDeleteRequestDTO(BaseModel):
    step_id: Optional[int] = Field(None, description="삭제할 단계 ID", alias="stepId")
    group_id: Optional[int] = Field(None, description="삭제할 그룹 ID", alias="groupId")

    @model_validator(mode="before")
    @classmethod
    def validate_step_or_group(cls, values):
        step = values.get("step_id")
        group = values.get("group_id")
        if step and group:
            raise ValueError("stepId와 groupId는 동시에 사용할 수 없습니다")
        if not step and not group:
            raise ValueError("stepId 또는 groupId 중 하나는 필수입니다")
        return values

    class Config:
        populate_by_name = True


class PatternDeleteResponseDTO(BaseModel):
    status_code: int = Field(..., alias="statusCode")
    data: dict = Field(..., alias="data")
    message: str
