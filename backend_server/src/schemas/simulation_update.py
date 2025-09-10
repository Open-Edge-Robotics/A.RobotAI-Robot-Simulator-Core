from typing import Optional, List, Union
from pydantic import BaseModel, Field, field_validator

from schemas.format import BaseSchema

class StepUpdateRequest(BaseSchema):
    """순차 패턴 단계 수정 요청"""
    step_order: int = Field(..., description="단계 순서", ge=1, alis="stepOrder")
    template_id: Optional[int] = Field(None, description="템플릿 ID", alias="templateId")
    autonomous_agent_count: Optional[int] = Field(None, description="자율행동체 개수", ge=1, le=50, alias="autonomousAgentCount")
    execution_time: Optional[int] = Field(None, description="실행 시간(초)", ge=1, alias="executionTime")
    delay_after_completion: Optional[int] = Field(None, description="완료 후 지연 시간(초)", ge=0, alias="delayAfterCompletion")
    repeat_count: Optional[int] = Field(None, description="반복 횟수", ge=1, le=100, alias="repeatCount")
    
    @field_validator('execution_time')
    @classmethod
    def validate_execution_time(cls, v):
        if v and (v < 60 or v > 86400):  # 1분 ~ 24시간
            raise ValueError('실행 시간은 60초 이상 86400초 이하여야 합니다')
        return v

class GroupUpdateRequest(BaseSchema):
    """병렬 패턴 그룹 수정 요청"""
    group_id: int = Field(..., description="그룹 ID (1부터 시작)", ge=1)
    template_id: Optional[int] = Field(None, description="템플릿 ID", alias="templateId")
    autonomous_agent_count: Optional[int] = Field(None, description="자율행동체 개수", ge=1, le=50, alias="autonomousAgentCount")
    execution_time: Optional[int] = Field(None, description="실행 시간(초)", ge=1, alias="executionTime")
    repeat_count: Optional[int] = Field(None, description="반복 횟수", ge=1, le=100, alias="repeatCount")
    
    @field_validator('execution_time')
    @classmethod
    def validate_execution_time(cls, v):
        if v and (v < 60 or v > 86400):  # 1분 ~ 24시간
            raise ValueError('실행 시간은 60초 이상 86400초 이하여야 합니다')
        return v

class SimulationUpdateRequest(BaseSchema):
    """시뮬레이션 수정 요청"""
    description: Optional[str] = Field(None, description="시뮬레이션 설명")
    pattern_update: Optional['PatternUpdateRequest'] = Field(None, description="패턴 업데이트 정보", alias="patternUpdate")
    
    @field_validator('pattern_update')
    @classmethod
    def validate_modification_content(cls, v, values):
        simulation_description = values.get('simulation_description')
        if not v and not simulation_description:
            raise ValueError('pattern_update 또는 simulation_description 중 하나는 필수입니다')
        return v

class PatternUpdateRequest(BaseSchema):
    """패턴 업데이트 요청 (순차/병렬 공통)"""
    steps_update: Optional[List['StepUpdateRequest']] = Field(None, description="순차 패턴 단계 업데이트", alias="stepsUpdate")
    groups_update: Optional[List['GroupUpdateRequest']] = Field(None, description="병렬 패턴 그룹 업데이트", alias="groupsUpdate")
    
    @field_validator('groups_update')
    @classmethod
    def validate_pattern_type(cls, v, values):
        steps = values.get('steps_update')
        if steps and v:
            raise ValueError('steps_update와 groups_update는 동시에 사용할 수 없습니다')
        if not steps and not v:
            raise ValueError('steps_update 또는 groups_update 중 하나는 필수입니다')
        return v