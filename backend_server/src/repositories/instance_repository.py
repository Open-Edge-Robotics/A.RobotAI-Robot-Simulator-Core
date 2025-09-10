import contextlib
from datetime import datetime
from typing import List, Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
import logging

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
        simulation: "Simulation",
        template: "Template",
        step_order: Optional[int],
        agent_index: int,
        description: Optional[str] = None,
        session: Optional[AsyncSession] = None,
    ) -> Instance:
        """
        단일 Instance 생성 및 DB에 추가.
        
        Args:
            simulation: 시뮬레이션 객체
            template: 템플릿 객체
            step_order: 단계 순서 (Optional)
            agent_index: 에이전트 번호
            pod_namespace: Pod 네임스페이스
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
                f"{simulation.name}_step{step_order}_agent_{agent_index}"
                if step_order is not None
                else f"{simulation.name}_agent_{agent_index}"
            )
            desc = description or (
                f"Step {step_order} - Agent {agent_index}" if step_order is not None else f"Agent {agent_index}"
            )

            instance = Instance(
                name=name,
                description=desc,
                pod_namespace=getattr(simulation, "namespace", None),
                simulation_id=simulation.id,
                template_id=template.template_id,
                step_order=step_order,
            )
            session.add(instance)
            await session.flush()  # ID 생성
            return instance

    # -----------------------------
    # 여러 인스턴스 batch 생성
    # -----------------------------
    async def create_instances_batch(
        self,
        simulation: "Simulation",
        template: "Template",
        step_order: Optional[int],
        agent_count: int,
    ) -> List[Instance]:
        """
        지정된 수만큼 인스턴스를 생성 (batch insert)
        """
        instances = []
        async with self.session_factory() as session:
            for i in range(agent_count):
                inst = await self.create_instance(
                    simulation=simulation,
                    template=template,
                    step_order=step_order,
                    agent_index=i,
                    pod_namespace=simulation.namespace,
                    session=session,
                )
                instances.append(inst)
            await session.commit()  # 한 번만 commit
        return instances

# 의존성 주입을 위한 팩토리 함수
def create_instance_repository(session_factory: async_sessionmaker[AsyncSession]) -> InstanceRepository:
    """인스턴스 레포지토리 생성 팩토리"""
    return InstanceRepository(session_factory)