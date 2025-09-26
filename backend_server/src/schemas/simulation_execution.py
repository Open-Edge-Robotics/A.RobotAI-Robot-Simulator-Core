from typing import List
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
    ) -> ExecutionListResponse:
        """
        Execution 목록 응답 생성
        Returns:
            dict: camelCase로 변환된 JSON 응답
        """
        return ExecutionListResponse(
            status_code=status_code,
            message=message,
            data=ExecutionListData(
                executions=executions,
                pagination=pagination
            )
        ).model_dump()

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
    ) -> ExecutionDetailResponse:
        """
        Execution 상세 응답 생성
        """
        return ExecutionDetailResponse(
            status_code=status_code,
            message=message,
            data=ExecutionDetailData(execution=execution)
        ).model_dump()