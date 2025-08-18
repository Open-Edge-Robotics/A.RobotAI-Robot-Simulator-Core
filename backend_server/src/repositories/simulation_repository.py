from typing import Dict, List, Optional, Tuple
from sqlalchemy import case, select, func
from sqlalchemy.ext.asyncio import AsyncSession

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
        if pattern_type:
            query = query.where(Simulation.pattern_type == pattern_type)
        if status:
            query = query.where(Simulation.status == status)

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

    async def exists_by_id(self, simulation_id: str) -> bool:
        """시뮬레이션 존재 여부 확인"""
        stmt = select(Simulation).filter(Simulation.id == simulation_id)
        result = await self.db.execute(stmt)
        return result.scalars().first() is not None
    
    async def get_overview(self) -> Dict[str, int]:
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