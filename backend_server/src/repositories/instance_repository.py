import contextlib
from datetime import datetime
from typing import Annotated, List, Optional, Union
from fastapi import Depends
from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
import logging

from utils.simulation_utils import generate_instance_name
from database.db_conn import get_async_sessionmaker
from schemas.context import SimulationContext, StepContext
from models.simulation import Simulation, SimulationGroup
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
        step: Optional[Union[StepContext, SimulationStep]] = None,
        group: Optional[SimulationGroup] = None,
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
            step_order = getattr(step, "step_order", None)
            template_id = None
            if step is not None:
                template_id = getattr(step, "template_id", None)
            elif group is not None:
                template_id = getattr(group, "template_id", None)
 
            group_id = getattr(group, "id", None)
            
            # 1. UUID8 기반 임시 이름 생성
            name = generate_instance_name(simulation.id, step_order, group_id)

            instance = Instance(
                name=name,
                description=f"Step {step_order}" if step_order is not None else f"Group {group_id}" if group_id is not None else None,
                pod_namespace=getattr(simulation, "namespace", None),
                simulation_id=simulation.id,
                template_id=template_id,
                step_order=step_order,
                group_id=group_id
            )
            session.add(instance)
            await session.flush()  # ID 생성
            
            # 3. 최종 이름 업데이트 (instance_id 반영)
            if step_order is not None:
                instance.name = f"sim-{simulation.id}-step-{step_order}-instance-{instance.id}"
            elif group_id is not None:
                instance.name = f"sim-{simulation.id}-group-{group_id}-instance-{instance.id}"
            else:
                instance.name = f"sim-{simulation.id}-instance-{instance.id}"
                
            if manage_session:
                await session.commit()
                
            return instance

    # -----------------------------
    # 여러 인스턴스 batch 생성
    # -----------------------------
    async def create_instances_batch(
        self,
        simulation: Union[SimulationContext, Simulation],
        step: Optional[Union["StepContext", "SimulationStep"]] = None,
        group: Optional["SimulationGroup"] = None,
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
        
        # 생성할 instance 개수 결정
        if step is not None:
            count = step.autonomous_agent_count
        elif group is not None:
            count = group.autonomous_agent_count
        else:
            raise ValueError("Either step or group must be provided to determine instance count")
            
        instances = []
        async with session if manage_session else contextlib.nullcontext(session):
            for _ in range(count):
                inst = await self.create_instance(
                    simulation=simulation,
                    step=step,
                    group=group,
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
            
    async def delete_by_step(
        self,
        step_order: int,
        simulation_id: int,
        session: Optional[AsyncSession] = None,
    ) -> int:
        """
        step_order 기준으로 Instance 삭제
        Args:
            step_order: 삭제할 Step 순서 (필수)
            simulation_id: Simulation ID (필수)
            session: Optional, 기존 AsyncSession 재사용 가능
        Returns:
            삭제된 레코드 수
        """
        if step_order is None or simulation_id is None:
            raise Exception("step_order와 simulation_id는 필수입니다.")

        manage_session = False
        if session is None:
            session = self.session_factory()
            manage_session = True

        async with session if manage_session else contextlib.nullcontext(session):
            stmt = delete(Instance).where(
                Instance.step_order == step_order,
                Instance.simulation_id == simulation_id
            ).returning(Instance.id)
            result = await session.execute(stmt)
            deleted_ids = result.scalars().all()
            return len(deleted_ids)


    async def delete_by_group(
        self,
        group_id: int,
        session: Optional[AsyncSession] = None,
    ) -> int:
        """
        group_id 기준으로 Instance 삭제
        Args:
            group_id: 삭제할 그룹 ID (필수)
            session: Optional, 기존 AsyncSession 재사용 가능
        Returns:
            삭제된 레코드 수
        """
        if group_id is None:
            raise Exception("group_id는 필수입니다.")

        manage_session = False
        if session is None:
            session = self.session_factory()
            manage_session = True

        async with session if manage_session else contextlib.nullcontext(session):
            stmt = delete(Instance).where(
                Instance.group_id == group_id
            ).returning(Instance.id)      
            result = await session.execute(stmt)
            deleted_ids = result.scalars().all()
            return len(deleted_ids)

# 의존성 주입을 위한 팩토리 함수
def create_instance_repository(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_async_sessionmaker)]
) -> InstanceRepository:
    """인스턴스 레포지토리 생성 팩토리"""
    return InstanceRepository(session_factory)