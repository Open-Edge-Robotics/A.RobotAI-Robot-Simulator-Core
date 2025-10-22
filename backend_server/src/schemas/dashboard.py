from pydantic import Field
from typing import Dict, Optional

from settings import BaseSchema
from schemas.format import GlobalResponseModel

# -----------------------------
# 리소스 사용률 모델
# -----------------------------
class ResourceUsage(BaseSchema):
    usage_percent: float = Field(..., alias="usagePercent")
    status: str  # "normal", "warning", "critical"
    
    message: Optional[str] = None  # 사용자에게 보여줄 메시지

class ResourceUsageData(BaseSchema):
    cpu: ResourceUsage
    memory: ResourceUsage
    disk: ResourceUsage

# -----------------------------
# Pod 상태 모델
# -----------------------------
class StatusBreakdown(BaseSchema):
    count: int
    percentage: float

class PodStatusData(BaseSchema):
    total_count: int = Field(..., alias="totalCount")
    overall_health_percent: float = Field(..., alias="overallHealthPercent")
    status_breakdown: Dict[str, StatusBreakdown] = Field(..., alias="statusBreakdown")

# -----------------------------
# Dashboard 메인 데이터
# -----------------------------
class DashboardData(BaseSchema):
    simulation_id: int = Field(..., alias="simulationId")
    simulation_name: str = Field(..., alias="simulationName")
    latest_execution_status: str = Field(..., alias="latestExecutionStatus")
    pattern_type: str = Field(..., alias="patternType")
    total_execution_time: int = Field(..., alias="totalExecutionTime")
    autonomous_agent_count: int = Field(..., alias="autonomousAgentCount")
    resource_usage: ResourceUsageData = Field(..., alias="resourceUsage")
    pod_status: PodStatusData = Field(..., alias="podStatus")

class SimulationDashboardResponseModel(GlobalResponseModel):
    """대시보드 API 응답 모델"""

    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": "200",
                "data": {
                    "simulationId": 1,
                    "simulationName": "순차 시뮬레이션",
                    "status": "RUNNING",
                    "simulationStatus": "정상",
                    "patternType": "sequential",
                    "totalExecutionTime": 200,
                    "autonomousAgentCount": 2,
                    "resourceUsage": {
                        "cpu": {"usagePercent": 85.6, "status": "normal"},
                        "memory": {"usagePercent": 12.8, "status": "normal"},
                        "disk": {"usagePercent": 100.0, "status": "critical"}
                    },
                    "podStatus": {
                        "totalCount": 24,
                        "overallHealthPercent": 75.0,
                        "statusBreakdown": {
                            "success": {"count": 18, "percentage": 75.00},
                            "pending": {"count": 4, "percentage": 16.67},
                            "failed": {"count": 2, "percentage": 8.33}
                        }
                    }
                },
                "message": "시뮬레이션 대시보드 정보 조회 성공"
            }
        }
    }
    
    pass