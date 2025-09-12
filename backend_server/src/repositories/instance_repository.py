import contextlib
from datetime import datetime
from typing import Annotated, List, Optional, Union
from fastapi import Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
import logging

from database.db_conn import get_async_sessionmaker
from schemas.context import SimulationContext, StepContext
from models.simulation import Simulation
from models.simulation_steps import SimulationStep
from models.instance import Instance

logger = logging.getLogger(__name__)

class InstanceRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        """AsyncSession Factory 주입"""
        self.session_factory = session_factory
    
    async def count_all(self) -> int:
        """전체 인스턴스 개수 조회"""
        try:
            async with self.session_factory() as session:
                result = await session.execute(select(func.count(Instance.id)))
                return result.scalar() or 0
        except Exception as e:
            logger.error(f"전체 인스턴스 개수 조회 중 오류: {str(e)}")
            raise
        
    # -----------------------------
    # 단일 인스턴스 생성
    # -----------------------------
    async def create_instance(
        self,
        simulation: Union[SimulationContext, Simulation],
        step: Union[StepContext, SimulationStep],
        agent_index: int,
        session: Optional[AsyncSession] = None,
    ) -> Instance:
        """
        단일 Instance 생성 및 DB에 추가.
        
        Args:
            simulation: SimulationContext DTO (id, name, namespace 포함)
            template: 템플릿 ORM 객체
            step_order: 단계 순서 (Optional)
            agent_index: 에이전트 번호
            description: 인스턴스 설명
            session: Optional, 기존 AsyncSession 재사용 가능

        Returns:
            생성된 Instance 객체 (ID 포함, commit은 하지 않음)
        """
        manage_session = False
        if session is None:
            session = self.session_factory()
            manage_session = True

        async with session if manage_session else contextlib.nullcontext(session):
            name = (
                f"{simulation.name}_step{step.step_order}_agent_{agent_index}"
                if step.step_order is not None
                else f"{simulation.name}_agent_{agent_index}"
            )
            desc = f"Step {step.step_order} - Agent {agent_index}" if step.step_order is not None else f"Agent {agent_index}"

            instance = Instance(
                name=name,
                description=desc,
                pod_namespace=getattr(simulation, "namespace", None),
                simulation_id=simulation.id,
                template_id=step.template_id,
                step_order=step.step_order,
            )
            session.add(instance)
            await session.flush()  # ID 생성
            return instance

    # -----------------------------
    # 여러 인스턴스 batch 생성
    # -----------------------------
    async def create_instances_batch(
        self,
        simulation: Union[SimulationContext, Simulation],
        step: Union[StepContext, SimulationStep],
        session: Optional[AsyncSession] = None,
        start_index: int = 0,
    ) -> List[Instance]:
        """
        여러 Instance 생성 (batch insert). name/description 자동 생성
        외부에서 session을 주입하면 단일 트랜잭션으로 묶을 수 있음.
        """
        manage_session = False
        if session is None:
            session = self.session_factory()
            manage_session = True
            
        instances = []
        async with session if manage_session else contextlib.nullcontext(session):
            for i in range(step.autonomous_agent_count):
                agent_index = start_index + i
                inst = await self.create_instance(
                    simulation=simulation,
                    step=step,
                    agent_index=agent_index,
                    session=session,
                )
                instances.append(inst)

            if manage_session:
                await session.commit()  # 외부 세션 없을 때만 commit
                
        return instances
    
    async def delete(self, session, instance_id: int) -> None:
        instance = await session.get(Instance, instance_id)
        if instance:
            await session.delete(instance)

# 의존성 주입을 위한 팩토리 함수
def create_instance_repository(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_async_sessionmaker)]
) -> InstanceRepository:
    """인스턴스 레포지토리 생성 팩토리"""
    return InstanceRepository(session_factory)