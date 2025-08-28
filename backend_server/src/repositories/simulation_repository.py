from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Optional
from sqlalchemy import case, select, func, desc, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
import logging

logger = logging.getLogger(__name__)

# 실제 모델 import (런타임에서 사용)
try:
    from models.simulation import Simulation
    from models.simulation_groups import SimulationGroup
    from models.simulation_steps import SimulationStep
    from models.enums import SimulationStatus, PatternType
    MODELS_AVAILABLE = True
except ImportError as e:
    logger.warning(f"모델 import 실패: {e}")
    MODELS_AVAILABLE = False
    class Simulation: pass
    class SimulationGroup: pass  
    class SimulationStep: pass
    class SimulationStatus: 
        READY = "READY"
        RUNNING = "RUNNING" 
        COMPLETED = "COMPLETED"
        FAILED = "FAILED"
    class PatternType: pass

if TYPE_CHECKING:
    from schemas.pagination import PaginationParams


class SimulationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        """AsyncSession Factory 주입"""
        self.session_factory = session_factory

    async def find_all_with_pagination(
        self, 
        pagination: "PaginationParams",
        pattern_type: Optional[PatternType] = None,
        status: Optional[SimulationStatus] = None
    ) -> List[Simulation]:
        if not MODELS_AVAILABLE:
            return []

        try:
            async with self.session_factory() as session:
                query = select(Simulation)
                if pattern_type:
                    query = query.where(Simulation.pattern_type == pattern_type)
                if status:
                    query = query.where(Simulation.status == status)

                status_priority = case(
                    (Simulation.status == "RUNNING", 1),
                    (Simulation.status == "INITIATING", 2),
                    (Simulation.status == "READY", 3),
                    (Simulation.status == "STOPPED", 4),
                    (Simulation.status == "FAILED", 5),
                    (Simulation.status == "COMPLETED", 6),
                    else_=8
                )
                
                time_priority = case(
                    (Simulation.status == "RUNNING", Simulation.started_at),
                    (Simulation.status == "INITIATING", Simulation.created_at),
                    (Simulation.status == "READY", Simulation.created_at),
                    (Simulation.status == "STOPPED", Simulation.updated_at),
                    (Simulation.status == "FAILED", Simulation.updated_at),
                    (Simulation.status == "COMPLETED", Simulation.completed_at),
                    else_=Simulation.created_at
                )
                
                query = query.order_by(status_priority, desc(time_priority))
                query = query.offset(pagination.offset).limit(pagination.limit)
                result = await session.execute(query)
                return result.scalars().unique().all()

        except Exception as e:
            logger.error(f"페이지네이션된 시뮬레이션 목록 조회 중 오류: {str(e)}")
            raise

    async def count_all(
        self,
        pattern_type: Optional[PatternType] = None,
        status: Optional[SimulationStatus] = None
    ) -> int:
        if not MODELS_AVAILABLE:
            return 0
        try:
            async with self.session_factory() as session:
                query = select(func.count(Simulation.id))
                if pattern_type:
                    query = query.where(Simulation.pattern_type == pattern_type)
                if status:
                    query = query.where(Simulation.status == status)
                result = await session.execute(query)
                return result.scalar_one()
        except Exception as e:
            logger.error(f"전체 시뮬레이션 개수 조회 중 오류: {str(e)}")
            return 0

    async def exists_by_id(self, simulation_id: int) -> bool:
        if not MODELS_AVAILABLE:
            return False
        try:
            async with self.session_factory() as session:
                stmt = select(Simulation).filter(Simulation.id == simulation_id)
                result = await session.execute(stmt)
                return result.scalars().first() is not None
        except Exception as e:
            logger.error(f"시뮬레이션 존재 여부 확인 중 오류: {str(e)}")
            return False

    async def get_overview(self) -> Dict[str, int]:
        if not MODELS_AVAILABLE:
            return {'total':0,'ready':0,'running':0,'completed':0,'failed':0}
        try:
            async with self.session_factory() as session:
                stmt = select(
                    func.count(Simulation.id).label('total'),
                    func.sum(case((Simulation.status == SimulationStatus.READY, 1), else_=0)).label('ready'),
                    func.sum(case((Simulation.status == SimulationStatus.RUNNING, 1), else_=0)).label('running'), 
                    func.sum(case((Simulation.status == SimulationStatus.COMPLETED, 1), else_=0)).label('completed'), 
                    func.sum(case((Simulation.status == SimulationStatus.FAILED, 1), else_=0)).label('failed')
                )
                result = await session.execute(stmt)
                row = result.first()
                return {
                    'total': row.total or 0,
                    'ready': row.ready or 0,
                    'running': row.running or 0,
                    'completed': row.completed or 0,
                    'failed': row.failed or 0
                }
        except Exception as e:
            logger.error(f"시뮬레이션 개요 조회 중 오류: {str(e)}")
            return {'total':0,'ready':0,'running':0,'completed':0,'failed':0}

    async def find_by_id(self, simulation_id: int) -> Optional[Simulation]:
        if not MODELS_AVAILABLE:
            return None
        try:
            async with self.session_factory() as session:
                stmt = select(Simulation).where(Simulation.id == simulation_id)
                result = await session.execute(stmt)
                return result.scalars().first()
        except Exception as e:
            logger.error(f"단일 시뮬레이션 조회 중 오류: {str(e)}")
            return None

    async def find_steps_with_template(self, simulation_id: int) -> List[SimulationStep]:
        if not MODELS_AVAILABLE:
            return []
        try:
            async with self.session_factory() as session:
                stmt = (
                    select(SimulationStep)
                    .options(selectinload(SimulationStep.template))
                    .where(SimulationStep.simulation_id == simulation_id)
                    .order_by(SimulationStep.step_order)
                )
                result = await session.execute(stmt)
                return result.scalars().all()
        except Exception as e:
            logger.error(f"시뮬레이션 스텝 조회 중 오류: {str(e)}")
            return []

    async def find_groups_with_template(self, simulation_id: int) -> List[SimulationGroup]:
        if not MODELS_AVAILABLE:
            return []
        try:
            async with self.session_factory() as session:
                stmt = (
                    select(SimulationGroup)
                    .options(selectinload(SimulationGroup.template))
                    .where(SimulationGroup.simulation_id == simulation_id)
                )
                result = await session.execute(stmt)
                return result.scalars().all()
        except Exception as e:
            logger.error(f"시뮬레이션 그룹 조회 중 오류: {str(e)}")
            return []

    async def find_simulation_steps(self, simulation_id: int) -> List[SimulationStep]:
        try:
            async with self.session_factory() as session:
                stmt = (
                    select(SimulationStep)
                    .where(SimulationStep.simulation_id == simulation_id)
                    .order_by(SimulationStep.step_order)
                )
                result = await session.execute(stmt)
                return result.scalars().all()
        except Exception as e:
            logger.error(f"시뮬레이션 스텝 조회 중 오류: {str(e)}")
            return []

    async def find_simulation_groups(self, simulation_id: int) -> List[SimulationGroup]:
        try:
            async with self.session_factory() as session:
                stmt = (
                    select(SimulationGroup)
                    .where(SimulationGroup.simulation_id == simulation_id)
                    .order_by(SimulationGroup.id)
                )
                result = await session.execute(stmt)
                return result.scalars().all()
        except Exception as e:
            logger.error(f"시뮬레이션 그룹 조회 중 오류: {str(e)}")
            return []

    async def update_simulation_status(
        self, simulation_id: int, status: str, failure_reason: Optional[str] = None
    ) -> bool:
        try:
            current_time = datetime.now(timezone.utc)
            async with self.session_factory() as session:
                update_data = {"status": status}
                if status == "RUNNING":
                    update_data["started_at"] = current_time
                elif status in ["COMPLETED", "FAILED"]:
                    update_data["completed_at"] = current_time
                stmt = update(Simulation).where(Simulation.id == simulation_id).values(**update_data)
                result = await session.execute(stmt)
                if result.rowcount == 0:
                    raise ValueError(f"시뮬레이션 ID {simulation_id}를 찾을 수 없습니다.")
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"시뮬레이션 상태 업데이트 실패: {str(e)}")
            raise


# Repository 생성 팩토리
def create_simulation_repository(session_factory: async_sessionmaker[AsyncSession]) -> SimulationRepository:
    """AsyncSession Factory 기반 안전한 Repository 생성"""
    return SimulationRepository(session_factory)
