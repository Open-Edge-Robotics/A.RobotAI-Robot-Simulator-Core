from typing import List, Optional
from sqlalchemy import func, select, desc, case
from database.db_conn import AsyncSession, async_sessionmaker
from models.simulation_execution import SimulationExecution
from schemas.pagination import PaginationParams

class SimulationExecutionRepository:
    """SimulationExecution 전용 CRUD"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def find_all_with_pagination(
        self,
        simulation_id: int,
        pagination: PaginationParams
    ) -> List[SimulationExecution]:
        """
        simulation_id 기준 실행 히스토리 조회
        최신 생성일(created_at) 기준 정렬 + pagination 적용
        """
        async with self.session_factory() as session:
            query = (
                select(SimulationExecution)
                .where(SimulationExecution.simulation_id == simulation_id)
                .order_by(desc(SimulationExecution.created_at))
                .offset(pagination.offset)
                .limit(pagination.limit)
            )

            result = await session.execute(query)
            return result.scalars().unique().all()

    async def count_by_simulation_id(self, simulation_id: int) -> int:
        """총 실행 히스토리 개수"""
        async with self.session_factory() as session:
            stmt = select(func.count(SimulationExecution.id)).where(SimulationExecution.simulation_id == simulation_id)
            result = await session.execute(stmt)
            return result.scalar_one()
        
    async def find_by_id(
        self,
        simulation_id: int,
        execution_id: int
    ) -> Optional[SimulationExecution]:
        """
        simulation_id + execution_id 기준으로 단일 Execution 조회
        존재하지 않으면 None 반환
        """
        async with self.session_factory() as session:
            stmt = select(SimulationExecution).where(
                SimulationExecution.simulation_id == simulation_id,
                SimulationExecution.id == execution_id
            )
            result = await session.execute(stmt)
            execution = result.scalars().first()  # 없으면 None
            return execution
