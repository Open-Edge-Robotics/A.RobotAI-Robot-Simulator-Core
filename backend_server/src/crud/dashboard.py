from datetime import datetime, timezone
import traceback
from typing import NamedTuple
from schemas.simulation import SimulationStatus
from repositories.base import InstanceRepositoryInterface, MecRepositoryInterface, SimulationRepositoryInterface
import logging

logger = logging.getLogger(__name__)

class SystemOverview(NamedTuple):
    """시스템 현황 데이터 구조"""
    total_simulations: int
    running_simulations: int
    total_mec: int
    total_instances: int
    timestamp: str

class DashboardService:
    def __init__(
        self,
        simulation_repo: SimulationRepositoryInterface,
        mec_repo: MecRepositoryInterface,
        instance_repo: InstanceRepositoryInterface
    ):
        self.simulation_repo = simulation_repo
        self.mec_repo = mec_repo
        self.instance_repo = instance_repo
        
    async def get_system_overview(self) -> SystemOverview:
        """
        시스템 전체 현황 조회
        
        Returns:
            SystemOverview: 시스템 현황 정보
            
        Raises:
            Exception: 데이터 조회 중 오류 발생 시
        """
        try:
            logger.info("시스템 현황 데이터 조회 시작")
            
            # 병렬로 모든 데이터 조회 (성능 최적화)
            import asyncio
            
            total_simulations_task = self.simulation_repo.count_all()
            running_simulations_task = self.simulation_repo.count_all(status=SimulationStatus.RUNNING)
            total_mec_task = self.mec_repo.count_all()
            total_instances_task = self.instance_repo.count_all()
            
            # 모든 비동기 작업을 병렬로 실행
            total_simulations, running_simulations, total_mec, total_instances = await asyncio.gather(
                total_simulations_task,
                running_simulations_task,
                total_mec_task,
                total_instances_task
            )
            
            # 현재 시간을 ISO 8601 형식으로 생성
            timestamp = datetime.now(timezone.utc).isoformat() + "Z"
            
            # 비즈니스 로직: 데이터 검증
            if running_simulations > total_simulations:
                logger.warning(
                    f"실행 중인 시뮬레이션 수({running_simulations})가 "
                    f"전체 시뮬레이션 수({total_simulations})보다 큽니다."
                )
            
            system_overview = SystemOverview(
                total_simulations=total_simulations,
                running_simulations=running_simulations,
                total_mec=total_mec,
                total_instances=total_instances,
                timestamp=timestamp
            )
            
            logger.info(f"시스템 현황 조회 완료: {system_overview}")
            return system_overview
            
        except Exception as e:
            traceback.print_stack()
            logger.error(f"시스템 현황 조회 중 오류 발생: {str(e)}")
            raise
        
# 의존성 주입을 위한 팩토리 함수
def create_dashboard_service(
    simulation_repo: SimulationRepositoryInterface,
    mec_repo: MecRepositoryInterface,
    instance_repo: InstanceRepositoryInterface
) -> DashboardService:
    """대시보드 서비스 생성 팩토리"""
    return DashboardService(
        simulation_repo=simulation_repo,
        mec_repo=mec_repo,
        instance_repo=instance_repo
    )