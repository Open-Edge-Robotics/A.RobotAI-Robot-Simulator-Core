from datetime import datetime, timezone
import traceback
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
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
    from models.enums import SimulationStatus, PatternType, StepStatus, GroupStatus
    MODELS_AVAILABLE = True
except ImportError as e:
    logger.warning(f"모델 import 실패: {e}")
    MODELS_AVAILABLE = False
    class Simulation: pass
    class SimulationGroup: pass  
    class SimulationStep: pass
    class SimulationStatus: 
        PENDING = "PENDING"
        RUNNING = "RUNNING" 
        COMPLETED = "COMPLETED"
        FAILED = "FAILED"
        STOPPED = "STOPPED"
    class StepStatus:
        PENDING = "PENDING"
        RUNNING = "RUNNING" 
        COMPLETED = "COMPLETED"
        FAILED = "FAILED"
        STOPPED = "STOPPED"
    class GroupStatus: 
        PENDING = "PENDING"
        RUNNING = "RUNNING" 
        COMPLETED = "COMPLETED"
        FAILED = "FAILED"
        STOPPED = "STOPPED"
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
                    (Simulation.status == "PENDING", 3),
                    (Simulation.status == "STOPPED", 4),
                    (Simulation.status == "FAILED", 5),
                    (Simulation.status == "COMPLETED", 6),
                    else_=8
                )
                
                time_priority = case(
                    (Simulation.status == "RUNNING", Simulation.started_at),
                    (Simulation.status == "INITIATING", Simulation.created_at),
                    (Simulation.status == "PENDING", Simulation.created_at),
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

    async def find_summary_list(self) -> List[Tuple[int, str]]:
        """시뮬레이션 ID와 이름만 조회 (드롭다운용)"""
        if not MODELS_AVAILABLE:
            logger.warning("모델이 없어 빈 리스트를 반환합니다.")
            return []

        try:
            async with self.session_factory() as session:
                query = (
                    select(Simulation.id, Simulation.name)
                    .order_by(desc(Simulation.created_at))  # 최신순 정렬
                )
                result = await session.execute(query)
                summary_list = result.all()
                return summary_list

        except Exception as e:
            logger.error(f"시뮬레이션 요약 목록 조회 중 오류: {str(e)}")
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
        
    async def count_simulation_steps(self, simulation_id: int) -> int:
        """특정 시뮬레이션의 스텝 개수 조회"""
        async with self.session_factory() as session:
            result = await session.execute(
                select(func.count(SimulationStep.id))
                .where(SimulationStep.simulation_id == simulation_id)
            )
            return result.scalar() or 0
        
    async def count_simulation_groups(self, simulation_id: int) -> int:
        """특정 시뮬레이션의 그룹 개수 조회"""
        async with self.session_factory() as session:
            result = await session.execute(
                select(func.count(SimulationGroup.id))
                .where(SimulationGroup.simulation_id == simulation_id)
            )
            return result.scalar() or 0

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
            return {'total':0,'pending':0,'running':0,'completed':0,'failed':0}
        try:
            async with self.session_factory() as session:
                stmt = select(
                    func.count(Simulation.id).label('total'),
                    func.sum(case((Simulation.status == SimulationStatus.PENDING, 1), else_=0)).label('pending'),
                    func.sum(case((Simulation.status == SimulationStatus.RUNNING, 1), else_=0)).label('running'), 
                    func.sum(case((Simulation.status == SimulationStatus.COMPLETED, 1), else_=0)).label('completed'), 
                    func.sum(case((Simulation.status == SimulationStatus.FAILED, 1), else_=0)).label('failed')
                )
                result = await session.execute(stmt)
                row = result.first()
                return {
                    'total': row.total or 0,
                    'pending': row.pending or 0,
                    'running': row.running or 0,
                    'completed': row.completed or 0,
                    'failed': row.failed or 0
                }
        except Exception as e:
            logger.error(f"시뮬레이션 개요 조회 중 오류: {str(e)}")
            return {'total':0,'pending':0,'running':0,'completed':0,'failed':0}

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
        
    async def update_simulation_step_status(
        self, 
        step_id: int, 
        status: StepStatus, 
        started_at: datetime = None, 
        completed_at: datetime = None,
        stopped_at: datetime = None,
        failed_at: datetime = None,
        current_repeat: int = None
    ):
        """
        SimulationStep의 상태와 시간 정보를 업데이트
        """
        update_data = {"status": status, "updated_at": datetime.now()}
        
        if started_at is not None:
            update_data["started_at"] = started_at
        if completed_at is not None:
            update_data["completed_at"] = completed_at
        if stopped_at is not None:
            update_data["stopped_at"] = stopped_at
        if failed_at is not None:
            update_data["failed_at"] = failed_at
        if current_repeat is not None:
            update_data["current_repeat"] = current_repeat

        async with self.session_factory() as session:
            await session.execute(
                update(SimulationStep)
                .where(SimulationStep.id == step_id)
                .values(**update_data)
            )
            await session.commit()
            
    async def update_simulation_step_current_repeat(
        self, 
        step_id: int, 
        current_repeat: int
    ):
        """
        SimulationStep의 현재 반복 진행 상황을 업데이트
        """
        async with self.session_factory() as session:
            await session.execute(
                update(SimulationStep)
                .where(SimulationStep.id == step_id)
                .values(
                    current_repeat=current_repeat,
                    updated_at=datetime.now()
                )
            )
            await session.commit()
            
    async def update_simulation_group_status(
        self, 
        group_id: int, 
        status: GroupStatus, 
        started_at: datetime = None, 
        completed_at: datetime = None,
        stopped_at: datetime = None,
        failed_at: datetime = None,
        current_repeat: int = None
    ):
        """
        SimulationGroup의 상태와 시간 정보를 업데이트
        """
        update_data = {"status": status, "updated_at": datetime.now(timezone.utc)}
        
        if started_at is not None:
            update_data["started_at"] = started_at
        if completed_at is not None:
            update_data["completed_at"] = completed_at
        if stopped_at is not None:
            update_data["stopped_at"] = stopped_at
        if failed_at is not None:
            update_data["failed_at"] = failed_at
        if current_repeat is not None:
            update_data["current_repeat"] = current_repeat

        async with self.session_factory() as session:
            await session.execute(
                update(SimulationGroup)
                .where(SimulationGroup.id == group_id)
                .values(**update_data)
            )
            await session.commit()
            
            
    async def update_simulation_group_current_repeat(
        self, 
        group_id: int, 
        current_repeat: int
    ):
        """
        SimulationGroup의 현재 반복 진행 상황을 업데이트
        """
        async with self.session_factory() as session:
            await session.execute(
                update(SimulationGroup)
                .where(SimulationGroup.id == group_id)
                .values(
                    current_repeat=current_repeat,
                    updated_at=datetime.now(timezone.utc)
                )
            )
            await session.commit()
            
    async def get_simulation_step_progress(self, simulation_id: int):
        """
        시뮬레이션의 모든 스텝 진행 상황을 조회 (모니터링용)
        """
        async with self.session_factory() as session:
            result = await session.execute(
                select(SimulationStep)
                .where(SimulationStep.simulation_id == simulation_id)
                .order_by(SimulationStep.step_order)
            )
            steps: List[SimulationStep] = result.scalars().all()
            
            progress_info: List[Dict[str, Any]] = []
            for step in steps:
                # 진행률 계산
                progress_percentage = 0.0
                if step.repeat_count > 0:
                    progress_percentage = (step.current_repeat / step.repeat_count) * 100
                        
                # 안전하게 running_autonomous_agents 할당 (없으면 0)
                autonomous_agents: int = getattr(step, "autonomous_agent_count", 0) or 0
                
                progress_info.append({
                    "step_id": step.id,
                    "step_order": step.step_order,
                    "status": step.status.value,
                    "progress_percentage": progress_percentage,
                    "current_repeat": step.current_repeat,
                    "repeat_count": step.repeat_count,
                    "started_at": step.started_at,
                    "completed_at": step.completed_at,
                    "failed_at": step.failed_at,
                    "autonomous_agents": autonomous_agents
                })
            
            return progress_info

    async def get_simulation_overall_progress(self, simulation_id: int):
        """
        시뮬레이션 전체 진행률 요약 정보 조회
        """
        async with self.session_factory() as session:
            result = await session.execute(
                select(SimulationStep)
                .where(SimulationStep.simulation_id == simulation_id)
            )
            steps = result.scalars().all()
            
            if not steps:
                return {
                    "total_steps": 0,
                    "completed_steps": 0,
                    "running_steps": 0,
                    "failed_steps": 0,
                    "stopped_steps": 0,
                    "overall_progress": 0.0
                }
            
            total_steps = len(steps)
            completed_steps = sum(1 for step in steps if step.status == StepStatus.COMPLETED)
            running_steps = sum(1 for step in steps if step.status == StepStatus.RUNNING)
            failed_steps = sum(1 for step in steps if step.status == StepStatus.FAILED)
            stopped_steps = sum(1 for step in steps if step.status == StepStatus.STOPPED)
            
            overall_progress = (completed_steps / total_steps) * 100 if total_steps > 0 else 0.0
            
            return {
                "total_steps": total_steps,
                "completed_steps": completed_steps,
                "running_steps": running_steps,
                "failed_steps": failed_steps,
                "stopped_steps": stopped_steps,
                "overall_progress": overall_progress
            }
            
    async def get_simulation_group_progress(self, simulation_id: int) -> List[Dict[str, Any]]:
        """
        병렬 패턴 시뮬레이션의 모든 그룹 진행 상황 조회 (모니터링용)
        """
        async with self.session_factory() as session:
            result = await session.execute(
                select(SimulationGroup)
                .where(SimulationGroup.simulation_id == simulation_id)
                .order_by(SimulationGroup.id)
            )
            groups: List[SimulationGroup] = result.scalars().all()

            progress_info: List[Dict[str, Any]] = []
            for group in groups:
                # 그룹 진행률 계산
                # 각 그룹 내부에서 반복 수행 중이면 current_repeat / repeat_count 기준
                progress = getattr(group, "progress", None)
                if progress is None:
                    if group.repeat_count > 0:
                        progress = (group.current_repeat / group.repeat_count) * 100
                    else:
                        progress = 0.0

                # 안전하게 autonomous_agents 할당
                autonomous_agents = getattr(group, "autonomous_agent_count", 0) or 0

                progress_info.append({
                    "group_id": group.id,
                    "status": group.status.value,
                    "progress": progress,
                    "started_at": group.started_at,
                    "completed_at": group.completed_at,
                    "failed_at": group.failed_at,
                    "stopped_at": group.stopped_at,
                    "autonomous_agents": autonomous_agents,
                    "current_repeat": getattr(group, "current_repeat", 0),
                    "total_repeats": getattr(group, "repeat_count", 0),
                    "error": getattr(group, "error", None)
                })

            return progress_info

    async def get_simulation_overall_group_progress(self, simulation_id: int) -> Dict[str, Any]:
        """
        병렬 패턴 시뮬레이션 전체 진행률 요약 정보 조회
        """
        async with self.session_factory() as session:
            result = await session.execute(
                select(SimulationGroup)
                .where(SimulationGroup.simulation_id == simulation_id)
            )
            groups = result.scalars().all()

            if not groups:
                return {
                    "total_groups": 0,
                    "completed_groups": 0,
                    "running_groups": 0,
                    "failed_groups": 0,
                    "stopped_groups": 0,
                    "overall_progress": 0.0
                }

            total_groups = len(groups)
            completed_groups = sum(1 for g in groups if g.status == GroupStatus.COMPLETED)
            running_groups = sum(1 for g in groups if g.status == GroupStatus.RUNNING)
            failed_groups = sum(1 for g in groups if g.status == GroupStatus.FAILED)
            stopped_groups = sum(1 for g in groups if g.status == GroupStatus.STOPPED)

            # overallProgress: 에이전트 수 가중 평균
            total_agents = sum(g.autonomous_agent_count for g in groups)
            print(f"total_agents: {total_agents}")
            if total_agents > 0:
                weighted_sum = sum(
                    g.calculate_progress * g.autonomous_agent_count 
                    for g in groups
                )
                overall_progress = weighted_sum / total_agents
            else:
                overall_progress = 0.0
                
            print(f"overall_progress: {overall_progress}")

            return {
                "total_groups": total_groups,
                "completed_groups": completed_groups,
                "running_groups": running_groups,
                "failed_groups": failed_groups,
                "stopped_groups": stopped_groups,
                "overall_progress": round(overall_progress, 1)
            }



# Repository 생성 팩토리
def create_simulation_repository(session_factory: async_sessionmaker[AsyncSession]) -> SimulationRepository:
    """AsyncSession Factory 기반 안전한 Repository 생성"""
    return SimulationRepository(session_factory)
