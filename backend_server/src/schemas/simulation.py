from datetime import date, datetime
from typing import List, Optional, Union, Dict, Any
from pydantic import BaseModel, Field, field_validator, validator

from models.enums import PatternType, SimulationStatus
from .pagination import PaginationMeta, PaginationParams
from .format import GlobalResponseModel
from settings import BaseSchema
from utils.my_enum import API


###### 생성 #######
# 1) Sequential 패턴용 Step DTO
class SequentialStep(BaseModel):
    step_order: int = Field(..., alias="stepOrder", ge=1, description="단계 순서 (1부터 시작)")
    template_id: int = Field(..., alias="templateId", description="템플릿 ID")
    autonomous_agent_count: int = Field(..., alias="autonomousAgentCount", ge=1, description="자율행동체 수")
    execution_time: int = Field(..., alias="executionTime", ge=1, description="단계 실행 시간(초)")
    delay_after_completion: int = Field(0, alias="delayAfterCompletion", ge=0, description="단계 완료 후 지연 시간(초)")
    repeat_count: int = Field(..., alias="repeatCount", ge=1, description="동작 실행 반복 횟수")

    class Config:
        populate_by_name = True

# 2) Parallel 패턴용 Agent DTO
class ParallelAgent(BaseModel):
    template_id: int = Field(..., alias="templateId", description="템플릿 ID")
    autonomous_agent_count: int = Field(..., alias="autonomousAgentCount", ge=1, description="자율행동체 수")
    execution_time: int = Field(..., alias="executionTime", ge=1, description="실행 시간(초)")
    repeat_count: int = Field(..., alias="repeatCount", ge=1, description="동작 실행 반복 횟수")

    class Config:
        populate_by_name = True


# 4) 패턴 타입 별 패턴 데이터 DTO
class SequentialPattern(BaseModel):
    steps: List[SequentialStep] = Field(..., description="순차 실행 단계 리스트")

    class Config:
        populate_by_name = True

class ParallelPattern(BaseModel):
    groups: List[ParallelAgent] = Field(..., description="병렬 실행 에이전트 리스트")

    class Config:
        populate_by_name = True

# 5) 상위 SimulationCreateRequest DTO
class SimulationCreateRequest(BaseModel):
    simulation_name: str = Field(..., alias="simulationName", description="시뮬레이션 이름")
    simulation_description: str = Field(..., alias="simulationDescription", description="시뮬레이션 설명")
    pattern_type: str = Field(..., alias="patternType", description="시뮬레이션 패턴 타입 ('sequential' 또는 'parallel')")
    mec_id: str = Field(..., alias="mecId", description="MEC 식별자")
    pattern: Union[SequentialPattern, ParallelPattern] = Field(..., description="패턴별 상세 실행 계획")

    class Config:
        populate_by_name = True

    @field_validator('pattern')
    @classmethod
    def validate_pattern(cls, v, info):
        values = info.data
        pattern_type = values.get('pattern_type') or values.get('patternType')
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

class SimulationCreateResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": 201,
                "data": {
                    "simulationId": 8,
                    "simulationName": "병렬 실행 시뮬레이션 테스트",
                    "simulationDescription": "2개 그룹 병렬적으로 실행하는 시뮬레이션",
                    "patternType": "parallel",
                    "status": "CREATED",
                    "simulationNamespace": "simulation-8",
                    "mecId": "mec-01",
                    "createdAt": "2025-08-08 04:38:51.022791",
                    "totalExpectedPods": 4
                },
                "message": API.CREATE_SIMULATION.value
            }
        }
    }

    pass


###### 목록 조회 #######
class SimulationFilterRequest(PaginationParams):
    """
    시뮬레이션 필터 요청 DTO
    - 패턴 타입과 상태값 기준 필터링 가능
    - PaginationParams를 상속받아 페이징 지원
    - start_date, end_date로 기간 필터링 가능
    """
    pattern_type: Optional[PatternType] = Field(
        None, description="필터링할 시뮬레이션 패턴 타입 (선택)"
    )
    status: Optional[SimulationStatus] = Field(
        None, description="필터링할 시뮬레이션 상태값 (선택)"
    )
    start_date: Optional[date] = Field(
        None, description="조회 시작일 (YYYY-MM-DD, 선택)"
    )
    end_date: Optional[date] = Field(
        None, description="조회 종료일 (YYYY-MM-DD, 선택)"
    )

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
                        "agentCount": 5,
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

class SimulationListItem(BaseModel):
    """시뮬레이션 목록 아이템"""
    simulation_id: int = Field(alias="simulationId")
    simulation_name: str = Field(alias="simulationName")
    pattern_type: str = Field(alias="patternType")
    status: str
    mec_id: Optional[str] = Field(None, alias="mecId")
    created_at: datetime = Field(alias="createdAt", description="생성 시간 (항상 존재)")
    updated_at: datetime = Field(alias="updatedAt", description="마지막 업데이트 시간 (항상 존재)")

    class Config:
        populate_by_name = True
        
class SimulationOverview(BaseModel):
    """시뮬레이션 전체 개요 응답 DTO"""
    total: int = Field(..., description="전체 시뮬레이션 개수", ge=0)
    pending: int = Field(..., description="실행 대기 중인 시뮬레이션 개수", ge=0)
    running: int = Field(..., description="실행 중인 시뮬레이션 개수", ge=0)
    completed: int = Field(..., description="완료된 시뮬레이션 개수", ge=0)
    failed: int = Field(..., description="실패한 시뮬레이션 개수", ge=0)
    
    class Config:
        """Pydantic 설정"""
        json_schema_extra = {
            "example": {
                "total": 100,
                "pending": 0,
                "running": 25,
                "completed": 60,
                "failed": 15
            }
        }
    
    @classmethod
    def from_dict(cls, data) -> "SimulationOverview":
        """딕셔너리에서 DTO 객체 생성"""
        # 이미 SimulationOverview 인스턴스인 경우 그대로 반환
        if isinstance(data, cls):
            return data
        
        # 딕셔너리가 아닌 경우 에러
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict, got {type(data)}")
            
        return cls(**data)
    
    def to_dict(self) -> Dict[str, int]:
        """DTO 객체를 딕셔너리로 변환"""
        return {
            "total": self.total,
            "pending": self.pending,
            "running": self.running,
            "completed": self.completed,
            "failed": self.failed
        }

class SimulationListData(BaseModel):
    """시뮬레이션 목록 데이터"""
    overview: SimulationOverview = Field(..., description="시뮬레이션 전체 개요")
    simulations: List[SimulationListItem] = Field(..., description="시뮬레이션 목록")
    pagination: PaginationMeta = Field(..., description="페이지네이션 정보")
        
class SimulationListResponse(BaseModel):
    """시뮬레이션 목록 응답 DTO"""
    status: str = Field(default="success", description="응답 상태")
    message: str = Field(..., description="응답 메시지")
    data: SimulationListData = Field(..., description="응답 데이터")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "message": "시뮬레이션 목록 조회 성공",
                "data": {
                    "overview": {
                        "total": 2,
                        "pending": 2,
                        "running": 0,
                        "completed": 0,
                        "failed": 0
                    },
                    "simulations": [
                        {
                            "simulationId": 4,
                            "simulationName": "병렬 실행 반복 시뮬레이션 테스트",
                            "patternType": "parallel",
                            "status": "PENDING",
                            "mecId": "mec-02",
                            "createdAt": "2025-08-18T03:50:54.868749",
                            "updatedAt": "2025-08-18T03:50:55.371514"
                        },
                        {
                            "simulationId": 2,
                            "simulationName": "순차 실행 반복 시뮬레이션 테스트",
                            "patternType": "sequential",
                            "status": "PENDING",
                            "mecId": "mec-01",
                            "createdAt": "2025-08-18T03:49:01.113675",
                            "updatedAt": "2025-08-18T03:49:02.000375"
                        }
                    ],
                    "pagination": {
                        "currentPage": 1,
                        "pageSize": 2,
                        "totalItems": 2,
                        "totalPages": 1,
                        "hasNext": False,
                        "hasPrevious": False
                    }
                }
            }
        }


class SimulationListResponseFactory:
    """시뮬레이션 목록 응답 생성 팩토리"""
    
    @staticmethod
    def create(
        simulations: List[SimulationListItem],
        overview_data: SimulationOverview,
        pagination_meta: PaginationMeta,
        message: str = "시뮬레이션 목록 조회 성공"
    ) -> SimulationListResponse:
        """시뮬레이션 목록 응답 생성"""
        return SimulationListResponse(
            message=message,
            data=SimulationListData(
                overview=overview_data,
                simulations=simulations,
                pagination=pagination_meta
            )
        )

###### 시뮬레이션 요약 목록 #######        
class SimulationSummaryItem(BaseSchema):
    """시뮬레이션 목록 아이템"""
    simulation_id: int = Field(alias="simulationId")
    simulation_name: str = Field(alias="simulationName")

class SimulationSummaryResponse(GlobalResponseModel):
    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "message": "시뮬레이션 목록 조회 성공",
                "data": {
                    "simulations": [
                        {
                            "simulationId": 4,
                            "simulationName": "병렬 실행 반복 시뮬레이션 테스트"
                        },
                        {
                            "simulationId": 2,
                            "simulationName": "순차 실행 반복 시뮬레이션 테스트"
                        }
                    ]
                }
            }
        }

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
                "statusCode": 202,
                "data": {
                    "simulationId": 1,
                },
                "message": "시뮬레이션 삭제 요청 접수됨."
            }
        }
    }

    pass