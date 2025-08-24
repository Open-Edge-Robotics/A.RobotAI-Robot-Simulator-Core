from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Optional
from sqlalchemy import case, select, func, desc, update
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
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
    # 임시 더미 클래스 (모델이 없을 때)
    class Simulation: pass
    class SimulationGroup: pass  
    class SimulationStep: pass
    class SimulationStatus: 
        READY = "READY"
        RUNNING = "RUNNING" 
        COMPLETED = "COMPLETED"
        FAILED = "FAILED"
    class PatternType: pass

# TYPE_CHECKING 블록은 타입 힌트용으로만 사용
if TYPE_CHECKING:
    from schemas.pagination import PaginationParams


class SimulationRepository:
    def __init__(self, db_session: Optional[AsyncSession] = None):
        self.db_session = db_session

    async def find_all_with_pagination(
        self, 
        pagination: "PaginationParams",
        pattern_type: Optional[PatternType] = None,
        status: Optional[SimulationStatus] = None
    ) -> List[Simulation]:
        """페이지네이션된 시뮬레이션 목록 조회"""
        if not self.db_session or not MODELS_AVAILABLE:
            logger.warning("DB 세션이 없거나 모델이 없어 빈 리스트를 반환합니다.")
            return []
            
        try:
            query = select(Simulation)
            
            # 필터 적용
            if pattern_type:
                query = query.where(Simulation.pattern_type == pattern_type)
            if status:
                query = query.where(Simulation.status == status)

            # 상태 우선순위
            status_priority = case(
                (Simulation.status == "RUNNING", 1),
                (Simulation.status == "INITIATING", 2),
                (Simulation.status == "READY", 3),
                (Simulation.status == "PAUSED", 4),
                (Simulation.status == "FAILED", 5),
                (Simulation.status == "CANCELLED", 6),
                (Simulation.status == "COMPLETED", 7),
                else_=8
            )
            
            # 상태별 시간 기준
            time_priority = case(
                (Simulation.status == "RUNNING", Simulation.started_at),
                (Simulation.status == "INITIATING", Simulation.created_at),
                (Simulation.status == "READY", Simulation.created_at),
                (Simulation.status == "PAUSED", Simulation.updated_at),
                (Simulation.status == "FAILED", Simulation.updated_at),
                (Simulation.status == "CANCELLED", Simulation.updated_at),
                (Simulation.status == "COMPLETED", Simulation.completed_at),
                else_=Simulation.created_at
            )
            
            # 복합 우선순위 정렬
            query = query.order_by(status_priority, desc(time_priority))

            # 페이징 적용
            query = query.offset(pagination.offset).limit(pagination.limit)
            result = await self.db_session.execute(query)
            simulations = result.scalars().unique().all()

            return simulations
            
        except Exception as e:
            logger.error(f"페이지네이션된 시뮬레이션 목록 조회 중 오류: {str(e)}")
            raise

    async def count_all(
        self,
        pattern_type: Optional[PatternType] = None,
        status: Optional[SimulationStatus] = None
    ) -> int:
        """전체 시뮬레이션 개수 조회"""
        if not self.db_session or not MODELS_AVAILABLE:
            logger.warning("DB 세션이 없거나 모델이 없어 0을 반환합니다.")
            return 0
            
        try:
            query = select(func.count(Simulation.id))
            if pattern_type:
                query = query.where(Simulation.pattern_type == pattern_type)
            if status:
                query = query.where(Simulation.status == status)
            result = await self.db_session.execute(query)
            return result.scalar_one()
            
        except Exception as e:
            logger.error(f"전체 시뮬레이션 개수 조회 중 오류: {str(e)}")
            return 0

    async def exists_by_id(self, simulation_id: int) -> bool:
        """시뮬레이션 존재 여부 확인"""
        if not self.db_session or not MODELS_AVAILABLE:
            logger.warning("DB 세션이 없거나 모델이 없어 False를 반환합니다.")
            return False
            
        try:
            stmt = select(Simulation).filter(Simulation.id == simulation_id)
            result = await self.db_session.execute(stmt)
            return result.scalars().first() is not None
            
        except Exception as e:
            logger.error(f"시뮬레이션 존재 여부 확인 중 오류: {str(e)}")
            return False
    
    async def get_overview(self) -> Dict[str, int]:
        """시뮬레이션 전체 개요 조회 - 전체/상태별 개수"""
        if not self.db_session or not MODELS_AVAILABLE:
            logger.warning("DB 세션이 없거나 모델이 없어 기본값을 반환합니다.")
            return {
                'total': 0,
                'ready': 0,
                'running': 0,
                'completed': 0,
                'failed': 0
            }
            
        try:
            stmt = select(
                func.count(Simulation.id).label('total'),
                func.sum(
                    case((Simulation.status == SimulationStatus.READY, 1), else_=0)
                ).label('ready'),
                func.sum(
                    case((Simulation.status == SimulationStatus.RUNNING, 1), else_=0)
                ).label('running'), 
                func.sum(
                    case((Simulation.status == SimulationStatus.COMPLETED, 1), else_=0)
                ).label('completed'), 
                func.sum(
                    case((Simulation.status == SimulationStatus.FAILED, 1), else_=0)
                ).label('failed')
            )
            
            result = await self.db_session.execute(stmt)
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
            return {
                'total': 0,
                'ready': 0,
                'running': 0,
                'completed': 0,
                'failed': 0
            }
    
    async def find_by_id(self, simulation_id: int) -> Optional[Simulation]:
        """단일 시뮬레이션 조회"""
        if not self.db_session or not MODELS_AVAILABLE:
            logger.warning("DB 세션이 없거나 모델이 없어 None을 반환합니다.")
            return None
            
        try:
            stmt = select(Simulation).where(Simulation.id == simulation_id)
            result = await self.db_session.execute(stmt)
            simulation = result.scalars().first()
            return simulation
            
        except Exception as e:
            logger.error(f"단일 시뮬레이션 조회 중 오류: {str(e)}")
            return None
    
    async def find_steps_with_template(self, simulation_id: int) -> List[SimulationStep]:
        """Sequential 패턴용 Step 조회 + Template join"""
        if not self.db_session or not MODELS_AVAILABLE:
            logger.warning("DB 세션이 없거나 모델이 없어 빈 리스트를 반환합니다.")
            return []
            
        try:
            stmt = (
                select(SimulationStep)
                .options(selectinload(SimulationStep.template)) 
                .where(SimulationStep.simulation_id == simulation_id)
                .order_by(SimulationStep.step_order)
            )
            result = await self.db_session.execute(stmt)
            return result.scalars().all()
            
        except Exception as e:
            logger.error(f"시뮬레이션 스텝 조회 중 오류: {str(e)}")
            return []
    
    async def find_groups_with_template(self, simulation_id: int) -> List[SimulationGroup]:
        """Parallel 패턴용 Group 조회 + Template join"""
        if not self.db_session or not MODELS_AVAILABLE:
            logger.warning("DB 세션이 없거나 모델이 없어 빈 리스트를 반환합니다.")
            return []
            
        try:
            stmt = (
                select(SimulationGroup)
                .options(selectinload(SimulationGroup.template)) 
                .where(SimulationGroup.simulation_id == simulation_id)
            )
            result = await self.db_session.execute(stmt)
            return result.scalars().all()
            
        except Exception as e:
            logger.error(f"시뮬레이션 그룹 조회 중 오류: {str(e)}")
            return []

    async def find_simulation_steps(self, simulation_id: int) -> List[SimulationStep]:
        """simulation_id로 SimulationStep들을 step_order 순으로 조회"""
        stmt = (
            select(SimulationStep)
            .where(SimulationStep.simulation_id == simulation_id)
            .order_by(SimulationStep.step_order)
        )
        result = await self.db_session.execute(stmt)
        return result.scalars().all()
    
    async def find_simulation_groups(self, simulation_id: int) -> List[SimulationGroup]:
        """simulation_id로 SimulationGroup들을 id 기준 오름차순으로 조회"""
        stmt = (
            select(SimulationGroup)
            .where(SimulationGroup.simulation_id == simulation_id)
            .order_by(SimulationGroup.id)
        )
        result = await self.db_session.execute(stmt)
        return result.scalars().all()
    
    async def update_simulation_status(
        self, simulation_id: int, status: str, failure_reason: Optional[str] = None
    ) -> bool:
        """시뮬레이션 상태 업데이트"""
        try:
            current_time = datetime.now(timezone.utc)
            update_data = {"status": status}
            
            if status == "RUNNING":
                update_data["started_at"] = current_time
            elif status in ["COMPLETED", "FAILED"]:
                update_data["completed_at"] = current_time
            
            stmt = (
                update(Simulation)
                .where(Simulation.id == simulation_id)
                .values(**update_data)
            )
            result = await self.db_session.execute(stmt)
            if result.rowcount == 0:
                raise ValueError(f"시뮬레이션 ID {simulation_id}를 찾을 수 없습니다.")
            await self.db_session.commit()
            return True
            
        except Exception as e:
            await self.db_session.rollback()
            logger.error(f"시뮬레이션 상태 업데이트 실패: {str(e)}")
            raise


# 의존성 주입을 위한 팩토리 함수
def create_simulation_repository(db_session: Optional[AsyncSession] = None) -> SimulationRepository:
    """시뮬레이션 레포지토리 생성 팩토리"""
    return SimulationRepository(db_session)