from contextlib import asynccontextmanager
import asyncio
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import select

from models.instance import Instance
from models.simulation import Simulation
from models.simulation_steps import SimulationStep

class StatusUpdateManager:
    def __init__(self, sessionmaker: async_sessionmaker):
        self.sessionmaker = sessionmaker
    
    @asynccontextmanager
    async def get_session(self):
        """백그라운드 작업용 독립적인 DB 세션"""
        async with self.sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise
    
    async def update_simulation_status(
        self, 
        simulation_id: int, 
        pod_creation_status: str = None,
        current_step_order: int = None,
        current_step_repeat: int = None,
        total_created_pods: int = None,
        total_successful_pods: int = None,
        total_failed_pods: int = None,
        user_action_required: bool = None,
        partial_failure_step_order: int = None
    ):
        """시뮬레이션 상태 원자적 업데이트"""
        async with self.get_session() as session:
            simulation = await session.get(Simulation, simulation_id)
            if not simulation:
                raise ValueError(f"Simulation {simulation_id} not found")
            
            # 상태 업데이트
            if pod_creation_status:
                simulation.pod_creation_status = pod_creation_status
            if current_step_order is not None:
                simulation.current_step_order = current_step_order
            if current_step_repeat is not None:
                simulation.current_step_repeat = current_step_repeat
            if total_created_pods is not None:
                simulation.total_created_pods = total_created_pods
            if total_successful_pods is not None:
                simulation.total_successful_pods = total_successful_pods
            if total_failed_pods is not None:
                simulation.total_failed_pods = total_failed_pods
            if user_action_required is not None:
                simulation.user_action_required = user_action_required
            if partial_failure_step_order is not None:
                simulation.partial_failure_step_order = partial_failure_step_order
            
            simulation.updated_at = datetime.now()
            session.add(simulation)
            
            print(f"[STATUS UPDATE] Simulation {simulation_id}: {pod_creation_status}")
    
    async def update_step_status(
        self,
        simulation_id: int,
        step_order: int,
        status: str = None,
        current_repeat: int = None,
        successful_agents: int = None,
        failed_agents: int = None,
        created_pods_count: int = None
    ):
        """단계별 상태 원자적 업데이트"""
        async with self.get_session() as session:
            result = await session.execute(
                select(SimulationStep)
                .where(SimulationStep.simulation_id == simulation_id)
                .where(SimulationStep.step_order == step_order)
            )
            step = result.scalar_one_or_none()
            
            if not step:
                raise ValueError(f"Step {step_order} not found in simulation {simulation_id}")
            
            # 상태 업데이트
            if status:
                step.status = status
            if current_repeat is not None:
                step.current_repeat = current_repeat
            if successful_agents is not None:
                step.successful_agents = successful_agents
            if failed_agents is not None:
                step.failed_agents = failed_agents
            if created_pods_count is not None:
                step.created_pods_count = created_pods_count
            
            step.updated_at = datetime.now()
            session.add(step)
            
            print(f"[STEP UPDATE] Simulation {simulation_id} Step {step_order}: {status}")
    
    async def update_instance_status(
        self,
        instance_id: int,
        status: str = None,
        pod_name: str = None,
        error_message: str = None,
        error_code: str = None,
        retry_count: int = None,
        step_order: int = None,
        group_id: int = None
    ):
        """인스턴스 상태 원자적 업데이트"""
        async with self.get_session() as session:
            instance = await session.get(Instance, instance_id)
            if not instance:
                raise ValueError(f"Instance {instance_id} not found")
            
            # 상태 업데이트
            if status:
                instance.status = status
            if pod_name:
                instance.pod_name = pod_name
            if error_message:
                instance.error_message = error_message
            if error_code:
                instance.error_code = error_code
            if retry_count is not None:
                instance.retry_count = retry_count
            if step_order is not None:
                instance.step_order = step_order
            if group_id is not None:
                instance.group_id = group_id
            
            # 시간 기록
            if status == "CREATING":
                instance.pod_creation_started_at = datetime.now()
            elif status in ["RUNNING", "COMPLETED"]:
                instance.pod_creation_completed_at = datetime.now()
            
            instance.updated_at = datetime.now()
            session.add(instance)
            
            print(f"[INSTANCE UPDATE] Instance {instance_id}: {status}")

# 전역 인스턴스
status_manager = None

def init_status_manager(sessionmaker):
    """초기화 함수"""
    global status_manager
    status_manager = StatusUpdateManager(sessionmaker)
    print("StatusUpdateManager 초기화 완료")

def get_status_manager():
    """안전한 접근을 위한 getter 함수 (권장)"""
    if status_manager is None:
        raise RuntimeError("StatusUpdateManager가 초기화되지 않았습니다. init_status_manager()를 먼저 호출하세요.")
    return status_manager