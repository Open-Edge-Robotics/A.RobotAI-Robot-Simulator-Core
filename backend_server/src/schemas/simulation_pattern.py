from typing import Optional, Union
from pydantic import BaseModel, Field, model_validator

from schemas.format import BaseSchema

# -----------------------------
# Step 공통 필드
# -----------------------------
class StepBaseDTO(BaseModel):
    step_order: int = Field(..., ge=1, alias="stepOrder", description="단계 순서")

class StepTimingMixin(BaseModel):
    template_id: int = Field(..., alias="templateId", description="템플릿 ID")
    autonomous_agent_count: int = Field(..., ge=1, le=50, alias="autonomousAgentCount")
    execution_time: Optional[int] = Field(None, ge=60, le=86400, alias="executionTime")
    delay_after_completion: Optional[int] = Field(None, ge=0, alias="delayAfterCompletion")
    repeat_count: Optional[int] = Field(None, ge=1, le=100, alias="repeatCount")

# -----------------------------
# Group 공통 필드
# -----------------------------
class GroupBaseDTO(BaseModel):
    group_id: int = Field(..., ge=1, alias="groupId", description="그룹 ID")

class GroupTimingMixin(BaseModel):
    template_id: int = Field(..., alias="templateId", description="템플릿 ID")
    autonomous_agent_count: int = Field(..., ge=1, le=50, alias="autonomousAgentCount")
    execution_time: Optional[int] = Field(None, ge=60, le=86400, alias="executionTime")
    repeat_count: Optional[int] = Field(None, ge=1, le=100, alias="repeatCount")

# -----------------------------
# Step DTO
# -----------------------------
class StepCreateDTO(StepBaseDTO, StepTimingMixin):
    """Step 생성용 DTO"""
    pass

class StepUpdateDTO(StepBaseDTO):
    """Step 수정용 DTO"""
    template_id: Optional[int] = Field(None, alias="templateId")
    autonomous_agent_count: Optional[int] = Field(None, alias="autonomousAgentCount")
    execution_time: Optional[int] = Field(None, alias="executionTime")
    delay_after_completion: Optional[int] = Field(None, alias="delayAfterCompletion")
    repeat_count: Optional[int] = Field(None, alias="repeatCount")

class StepDeleteDTO(StepBaseDTO):
    """Step 삭제용 DTO"""
    pass

# -----------------------------
# Group DTO
# -----------------------------
class GroupCreateDTO(GroupTimingMixin):
    """Group 생성용 DTO"""
    pass

class GroupUpdateDTO(GroupBaseDTO):
    """Group 수정용 DTO"""
    template_id: Optional[int] = Field(None, alias="templateId")
    autonomous_agent_count: Optional[int] = Field(None, alias="autonomousAgentCount")
    execution_time: Optional[int] = Field(None, alias="executionTime")
    repeat_count: Optional[int] = Field(None, alias="repeatCount")

class GroupDeleteDTO(GroupBaseDTO):
    """Group 삭제용 DTO"""
    pass

# -----------------------------
# Pattern Request DTO
# -----------------------------
class PatternCreateRequestDTO(BaseModel):
    """패턴 생성 요청 DTO"""
    step: Optional[StepCreateDTO] = None
    group: Optional[GroupCreateDTO] = None

    @model_validator(mode="before")
    def validate_step_or_group(cls, values):
        step, group = values.get("step"), values.get("group")
        if step and group:
            raise ValueError("❌ Step과 Group을 동시에 요청할 수 없습니다")
        if not step and not group:
            raise ValueError("❌ Step 또는 Group 중 하나는 필수입니다")
        return values


class PatternUpdateRequestDTO(BaseModel):
    """패턴 수정 요청 DTO"""
    step: Optional[StepUpdateDTO] = None
    group: Optional[GroupUpdateDTO] = None

    @model_validator(mode="before")
    def validate_step_or_group(cls, values):
        step, group = values.get("step"), values.get("group")
        if step and group:
            raise ValueError("❌ Step과 Group을 동시에 요청할 수 없습니다")
        if not step and not group:
            raise ValueError("❌ Step 또는 Group 중 하나는 필수입니다")
        return values


class PatternDeleteRequestDTO(BaseModel):
    """패턴 삭제 요청 DTO"""
    step: Optional[StepDeleteDTO] = None
    group: Optional[GroupDeleteDTO] = None

    @model_validator(mode="before")
    def validate_step_or_group(cls, values):
        step, group = values.get("step"), values.get("group")
        if step and group:
            raise ValueError("❌ Step과 Group을 동시에 요청할 수 없습니다")
        if not step and not group:
            raise ValueError("❌ Step 또는 Group 중 하나는 필수입니다")
        return values

# -----------------------------
# Pattern Response DTO
# -----------------------------
class StepResponseData(BaseSchema):
    step_order: int
    id: Optional[int] = None
    template_id: Optional[int] = None
    template_name: Optional[str] = None
    template_type: Optional[str] = None
    autonomous_agent_count: Optional[int] = None
    execution_time: Optional[int] = None
    delay_after_completion: Optional[int] = None
    repeat_count: Optional[int] = None


class GroupResponseData(BaseSchema):
    group_id: int
    group_name: Optional[str] = None
    template_id: Optional[int] = None
    template_name: Optional[str] = None
    template_type: Optional[str] = None
    autonomous_agent_count: Optional[int] = None
    execution_time: Optional[int] = None
    repeat_count: Optional[int] = None
    assigned_area: Optional[str] = None
    
class PatternResponseDTO(BaseModel):
    status_code: int = Field(..., alias="statusCode")
    data: Optional[dict] = Field(None, alias="data") 
    message: str
