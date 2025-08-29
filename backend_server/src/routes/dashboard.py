from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
import logging

from schemas.format import GlobalResponseModel
from repositories.simulation_repository import create_simulation_repository
from repositories.mec_repository import create_mec_repository
from repositories.instance_repository import create_instance_repository
from crud.dashboard import DashboardService, create_dashboard_service
from crud.simulation import AsyncSession
from database.db_conn import get_db, async_session

# 로거 설정
logger = logging.getLogger(__name__)

# 라우터 생성
router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# 응답 모델 (필요에 따라 별도 models.py로 분리 가능)
from pydantic import BaseModel

class SystemOverviewData(BaseModel):
    totalSimulations: int
    runningSimulations: int
    totalMec: int
    totalInstances: int
    timestamp: str

class SystemOverviewResponse(BaseModel):
    statusCode: int
    data: Optional[SystemOverviewData]
    message: str
    
async def get_dashboard_service(
    db_session: Optional[AsyncSession] = Depends(get_db)
) -> DashboardService:
    """대시보드 서비스 의존성 주입"""
    simulation_repo = create_simulation_repository(async_session)
    mec_repo = create_mec_repository(db_session)
    instance_repo = create_instance_repository(db_session)
    
    return create_dashboard_service(
        simulation_repo=simulation_repo,
        mec_repo=mec_repo,
        instance_repo=instance_repo
    )


@router.get(
    "/system-overview", 
    response_model=SystemOverviewResponse,
    summary="대시보드 시스템 현황 조회",
    description="대시보드 상단에 표시되는 전체 시스템 현황 정보를 제공합니다.",
    response_description="시스템 현황 조회 결과",
    responses={
        200: {
            "description": "시스템 현황 조회 성공",
            "model": SystemOverviewResponse,
            "content": {
                "application/json": {
                    "example": {
                        "statusCode": 200,
                        "data": {
                            "totalSimulations": 15,
                            "runningSimulations": 3,
                            "totalMec": 8,
                            "totalInstances": 24,
                            "timestamp": "2024-08-22T10:30:00Z"
                        },
                        "message": "시스템 현황 조회 성공"
                    }
                }
            }
        },
        500: {
            "description": "서버 내부 오류",
            "model": GlobalResponseModel,
            "content": {
                "application/json": {
                    "example": {
                        "statusCode": 500,
                        "data": None,
                        "message": "시스템 현황 조회 중 오류가 발생했습니다."
                    }
                }
            }
        }
    },
    tags=["dashboard"]
)
async def get_system_overview(
    dashboard_service: DashboardService = Depends(get_dashboard_service)
):
    """
    대시보드 시스템 현황 조회 API
    
    Args
        dashboard_service: 주입된 대시보드 서비스
    
    Returns:
        SystemOverviewResponse: 시스템 현황 정보
    """
    try:
        logger.info("시스템 현황 조회 요청 시작")
        
        # 서비스 레이어를 통해 데이터 조회
        overview = await dashboard_service.get_system_overview()
        
        # 응답 데이터 구성
        overview_data = SystemOverviewData(
            totalSimulations=overview.total_simulations,
            runningSimulations=overview.running_simulations,
            totalMec=overview.total_mec,
            totalInstances=overview.total_instances,
            timestamp=overview.timestamp
        )
        
        logger.info(f"시스템 현황 조회 성공: {overview_data}")
        
        return SystemOverviewResponse(
            statusCode=200,
            data=overview_data,
            message="시스템 현황 조회 성공"
        )
        
    except Exception as e:
        logger.error(f"시스템 현황 조회 중 오류 발생: {str(e)}")
        
        return SystemOverviewResponse(
            statusCode=500,
            data=None,
            message="시스템 현황 조회 중 오류가 발생했습니다."
        )