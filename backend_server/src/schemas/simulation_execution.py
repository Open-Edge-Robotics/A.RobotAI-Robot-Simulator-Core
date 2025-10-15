from typing import Any, List, Optional, Union
from utils.json_utils import remove_none_recursive
from schemas.simulation_detail import ExecutionPlanSequential, ExecutionPlanParallel
from pydantic import Field

from schemas.pagination import PaginationMeta
from settings import BaseSchema
from .simulation_status import CurrentStatus

# ------------------------------
# ExecutionItem DTO
# ------------------------------
class ExecutionItem(BaseSchema):
    execution_id: int = Field(..., alias="executionId", description="실행 ID")
    simulation_id: int = Field(..., alias="simulationId", description="시뮬레이션 ID")
    pattern_type: str = Field(..., alias="patternType", description="패턴 타입 (sequential / parallel)")
    # For detail responses this will be a validated ExecutionPlanSequential or ExecutionPlanParallel.
    execution_plan: Optional[Union[ExecutionPlanSequential, ExecutionPlanParallel, dict]] = Field(
        None, alias="executionPlan", description="Execution 시작 시점의 실행 계획"
    )
    current_status: CurrentStatus = Field(..., alias="currentStatus", description="현재 실행 상태 정보")

# ------------------------------
# ExecutionListData DTO
# ------------------------------
class ExecutionListData(BaseSchema):
    executions: List[ExecutionItem] = Field(..., description="실행 목록")
    pagination: PaginationMeta  = Field(..., description="페이지네이션 정보")

# ------------------------------
# ExecutionListResponse DTO
# ------------------------------
class ExecutionListResponse(BaseSchema):
    status_code: int = Field(default=200, description="HTTP 상태 코드", alias="statusCode")
    message: str = Field(..., description="응답 메시지")
    data: ExecutionListData = Field(..., description="응답 데이터")

    class Config:
        json_schema_extra = {
            "example": {
                "statusCode": 200,
                "message": "실행 히스토리 조회 성공",
                "data": {
                    "executions": [
                        {
                            "executionId": 1,
                            "simulationId": 2,
                            "patternType": "sequential",
                            "currentStatus": {
                                "status": "COMPLETED",
                                "progress": {
                                    "overallProgress": 100,
                                    "currentStep": 2,
                                    "completedSteps": 2,
                                    "totalSteps": 2
                                },
                                "timestamps": {
                                    "createdAt": "2025-09-24T04:56:06.268284Z",
                                    "lastUpdated": "2025-09-24T05:00:14.791491Z",
                                    "startedAt": "2025-09-24T04:56:35.205294Z",
                                    "completedAt": "2025-09-24T05:00:14.791491Z"
                                },
                                "message": "시뮬레이션 완료"
                            }
                        }
                    ],
                    "pagination": {
                        "page": 1,
                        "size": 20,
                        "totalCount": 45,
                        "totalPages": 3,
                        "hasNext": True,
                        "hasPrev": False
                    }
                }
            }
        }

# ------------------------------
# Factory
# ------------------------------

class ExecutionListResponseFactory:
    """Execution 목록 응답 생성 팩토리"""

    @staticmethod
    def create(
        executions: List[ExecutionItem],
        pagination: PaginationMeta,
        message: str = "실행 히스토리 조회 성공",
        status_code: int = 200
    ) -> dict[str, Any]:
        # 모델 -> dict
        data_dict = ExecutionListData(
            executions=executions,
            pagination=pagination
        ).model_dump()  # BaseSchema에서 exclude_none=True 자동 적용됨

        # dict 내부 중첩 None 제거
        cleaned_data = remove_none_recursive(data_dict)

        # 최종 응답 모델 -> dict
        response_dict = ExecutionListResponse(
            status_code=status_code,
            message=message,
            data=cleaned_data
        ).model_dump()

        # 최종 dict 내부도 재귀적으로 None 제거
        return remove_none_recursive(response_dict)

# ------------------------------
# ExecutionDetailData DTO
# ------------------------------
class ExecutionDetailData(BaseSchema):
    execution: ExecutionItem = Field(..., description="단일 실행 상세 정보")

# ------------------------------
# ExecutionDetailResponse DTO
# ------------------------------
class ExecutionDetailResponse(BaseSchema):
    status_code: int = Field(default=200, description="HTTP 상태 코드", alias="statusCode")
    message: str = Field(..., description="응답 메시지")
    data: ExecutionDetailData = Field(..., description="응답 데이터")

    class Config:
        json_schema_extra = {
            "example": {
                "statusCode": 200,
                "message": "실행 상세 조회 성공",
                "data": {
                    "execution": {
                        "executionId": 5,
                        "simulationId": 1,
                        "patternType": "sequential",
                        "currentStatus": {
                            "status": "RUNNING",
                            "progress": {
                                "overallProgress": 0,
                                "currentStep": 0,
                                "completedSteps": 0,
                                "totalSteps": 2
                            },
                            "timestamps": {
                                "createdAt": "2025-09-26T02:32:43.958414Z",
                                "lastUpdated": "2025-09-26T03:28:49.346077Z",
                                "startedAt": "2025-09-26T03:28:49.346090Z",
                                "completedAt": None,
                                "failedAt": None,
                                "stoppedAt": None
                            },
                            "message": "시뮬레이션 시작 - 총 2개 스텝",
                            "stepDetails": [
                                {
                                    "stepId": 1,
                                    "name": "초기화",
                                    "status": "COMPLETED",
                                    "startedAt": "2025-09-26T02:32:44Z",
                                    "completedAt": "2025-09-26T02:32:45Z"
                                },
                                {
                                    "stepId": 2,
                                    "name": "실행",
                                    "status": "RUNNING",
                                    "startedAt": "2025-09-26T02:32:46Z",
                                    "completedAt": None
                                }
                            ],
                            "groupDetails": None
                        }
                    }
                }
            }
        }

# ------------------------------
# Factory
# ------------------------------
class ExecutionDetailResponseFactory:
    """단일 Execution 상세 응답 생성 팩토리"""

    @staticmethod
    def create(
        execution: ExecutionItem,
        message: str = "실행 상세 조회 성공",
        status_code: int = 200
    ) -> dict[str, Any]:
        """
        Execution 상세 응답 생성
        """
        # ExecutionDetailData -> dict
        data_dict = ExecutionDetailData(execution=execution).model_dump()

        # 중첩 None 제거
        cleaned_data = remove_none_recursive(data_dict)

        # 최종 응답 생성
        response_dict = ExecutionDetailResponse(
            status_code=status_code,
            message=message,
            data=cleaned_data
        ).model_dump()

        # 최종 dict 내부도 재귀적 None 제거
        return remove_none_recursive(response_dict)