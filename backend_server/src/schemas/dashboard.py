from pydantic import BaseModel, Field
from typing import Dict

from schemas.format import GlobalResponseModel

# -----------------------------
# 리소스 사용률 모델
# -----------------------------
class ResourceUsage(BaseModel):
    usage_percent: float = Field(..., alias="usagePercent")
    status: str  # "normal", "warning", "critical"
    
    class Config:
        populate_by_name = True

class ResourceUsageData(BaseModel):
    cpu: ResourceUsage
    memory: ResourceUsage
    disk: ResourceUsage
    
    class Config:
        populate_by_name = True

# -----------------------------
# Pod 상태 모델
# -----------------------------
class StatusBreakdown(BaseModel):
    count: int
    percentage: float
    
    class Config:
        populate_by_name = True

class PodStatusData(BaseModel):
    total_count: int = Field(..., alias="totalCount")
    overall_health_percent: float = Field(..., alias="overallHealthPercent")
    status_breakdown: Dict[str, StatusBreakdown] = Field(..., alias="statusBreakdown")

    class Config:
        populate_by_name = True

# -----------------------------
# Dashboard 메인 데이터
# -----------------------------
class DashboardData(BaseModel):
    simulation_id: int = Field(..., alias="simulationId")
    simulation_name: str = Field(..., alias="simulationName")
    status: str
    simulation_status: str = Field(..., alias="simulationStatus")
    pattern_type: str = Field(..., alias="patternType")
    total_execution_time: int = Field(..., alias="totalExecutionTime")
    autonomous_agent_count: int = Field(..., alias="autonomousAgentCount")
    resource_usage: ResourceUsageData = Field(..., alias="resourceUsage")
    pod_status: PodStatusData = Field(..., alias="podStatus")

    class Config:
        allow_population_by_field_name = True  # Python 이름으로도 population 가능
        alias_generator = None
        # json 출력 시 alias 사용
        populate_by_name = True

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
                            "waiting": {"count": 4, "percentage": 16.67},
                            "failed": {"count": 2, "percentage": 8.33}
                        }
                    }
                },
                "message": "시뮬레이션 대시보드 정보 조회 성공"
            }
        }
    }
    
    pass