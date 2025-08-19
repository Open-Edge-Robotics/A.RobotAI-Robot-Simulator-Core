from typing import Dict, List, Optional, Tuple
from sqlalchemy import case, select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc

from models.simulation_groups import SimulationGroup
from models.simulation_steps import SimulationStep
from models.simulation import Simulation
from models.enums import PatternType, SimulationStatus
from schemas.pagination import PaginationParams


class SimulationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_all_with_pagination(
        self, 
        pagination: PaginationParams,
        pattern_type: Optional[PatternType] = None,
        status: Optional[SimulationStatus] = None
    ) -> List[Simulation]:
        """페이지네이션된 시뮬레이션 목록 조회"""
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
        result = await self.db.execute(query)
        simulations = result.scalars().unique().all()

        return simulations

    async def count_all(
        self,
        pattern_type: Optional[PatternType] = None,
        status: Optional[SimulationStatus] = None
    ) -> int:
        """전체 시뮬레이션 개수 조회"""
        query = select(func.count(Simulation.id))
        if pattern_type:
            query = query.where(Simulation.pattern_type == pattern_type)
        if status:
            query = query.where(Simulation.status == status)
        result = await self.db.execute(query)
        return result.scalar_one()

    async def exists_by_id(self, simulation_id: int) -> bool:
        """시뮬레이션 존재 여부 확인"""
        stmt = select(Simulation).filter(Simulation.id == simulation_id)
        result = await self.db.execute(stmt)
        return result.scalars().first() is not None
    
    async def get_overview(self) -> Dict[int, int]:
        """시뮬레이션 전체 개요 조회 - 전체/상태별 개수"""
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
        
        result = await self.db.execute(stmt)
        row = result.first()
        
        return {
            'total': row.total,
            'ready': row.ready or 0,
            'running': row.running or 0,
            'completed': row.completed or 0,
            'failed': row.failed or 0
        }
    
    async def find_by_id(self, simulation_id: int) -> Simulation:
        """단일 시뮬레이션 조회"""
        stmt = select(Simulation).where(Simulation.id == simulation_id)
        result = await self.db.execute(stmt)
        simulation = result.scalars().first()
        return simulation
    
    async def find_steps_with_template(self, simulation_id: int) -> list[SimulationStep]:
        """
        Sequential 패턴용 Step 조회 + Template join
        """
        stmt = (
            select(SimulationStep)
            .options(selectinload(SimulationStep.template)) 
            .where(SimulationStep.simulation_id == simulation_id)
            .order_by(SimulationStep.step_order)
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()
    
    async def find_groups_with_template(self, simulation_id: int) -> list[SimulationGroup]:
        """
        Parallel 패턴용 Group 조회 + Template join
        """
        stmt = (
            select(SimulationGroup)
            .options(selectinload(SimulationGroup.template)) 
            .where(SimulationGroup.simulation_id == simulation_id)
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()