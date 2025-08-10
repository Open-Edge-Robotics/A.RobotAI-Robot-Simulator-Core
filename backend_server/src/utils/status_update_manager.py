import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from models.instance import Instance
from models.simulation import Simulation
from models.simulation_steps import SimulationStep
from models.simulation_groups import SimulationGroup

class StatusUpdateManager:
    def __init__(self, sessionmaker: async_sessionmaker):
        self.sessionmaker = sessionmaker
        self._session_lock = asyncio.Lock()  # 병렬 처리 시 세션 동기화
    
    @asynccontextmanager
    async def get_session(self):
        """백그라운드 작업용 독립적인 DB 세션 (병렬 처리 안정화)"""
        async with self._session_lock:
            async with self.sessionmaker() as session:
                try:
                    yield session
                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    print(f"[SESSION ERROR] DB 세션 오류: {e}")
                    raise
    
    async def safe_update_with_retry(self, update_func, max_retries=3):
        """안전한 업데이트 with 재시도 메커니즘"""
        for attempt in range(max_retries):
            try:
                return await update_func()
            except SQLAlchemyError as e:
                if attempt == max_retries - 1:
                    print(f"[RETRY FAILED] {max_retries}번 재시도 후 실패: {e}")
                    raise
                else:
                    print(f"[RETRY {attempt + 1}] DB 업데이트 재시도: {e}")
                    await asyncio.sleep(0.1 * (attempt + 1))  # 지수 백오프
            except Exception as e:
                print(f"[UPDATE ERROR] 업데이트 실패: {e}")
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
        """시뮬레이션 상태 원자적 업데이트 (안정화)"""
        
        async def _update():
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
        
        await self.safe_update_with_retry(_update)
    
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
        """단계별 상태 원자적 업데이트 (안정화)"""
        
        async def _update():
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
        
        await self.safe_update_with_retry(_update)
            
    async def update_group_status(
        self,
        simulation_id: int,
        group_id: int,
        status: str = None,
        current_repeat: int = None,
        successful_agents: int = None,
        failed_agents: int = None,
        created_pods_count: int = None,
        total_expected_agents: int = None,
        execution_started_at: datetime = None,
        execution_completed_at: datetime = None
    ):
        """그룹별 상태 원자적 업데이트 (안정화)"""
        
        async def _update():
            async with self.get_session() as session:
                # SimulationGroup을 ID로 조회하도록 수정
                result = await session.execute(
                    select(SimulationGroup)
                    .where(SimulationGroup.id == group_id)
                    .where(SimulationGroup.simulation_id == simulation_id)
                )
                group = result.scalar_one_or_none()
                
                if not group:
                    raise ValueError(f"Group {group_id} not found in simulation {simulation_id}")
                
                # 상태 업데이트
                if status:
                    group.status = status
                if current_repeat is not None:
                    group.current_repeat = current_repeat
                if successful_agents is not None:
                    group.successful_agents = successful_agents
                if failed_agents is not None:
                    group.failed_agents = failed_agents
                if created_pods_count is not None:
                    group.created_pods_count = created_pods_count
                if total_expected_agents is not None:
                    group.total_expected_agents = total_expected_agents
                if execution_started_at is not None:
                    group.execution_started_at = execution_started_at
                if execution_completed_at is not None:
                    group.execution_completed_at = execution_completed_at
                
                # 상태별 시간 자동 기록
                if status == "CREATING":
                    group.execution_started_at = datetime.now()
                elif status in ["COMPLETED", "FAILED", "PARTIAL_SUCCESS"]:
                    group.execution_completed_at = datetime.now()
                
                group.updated_at = datetime.now()
                session.add(group)
                
                print(f"[GROUP UPDATE] Simulation {simulation_id} Group {group_id}: {status}")
        
        await self.safe_update_with_retry(_update)
    
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
        """인스턴스 상태 원자적 업데이트 (안정화)"""
        
        async def _update():
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
        
        await self.safe_update_with_retry(_update)

    async def get_simulation_progress(self, simulation_id: int) -> dict:
        """시뮬레이션 진행 상황 조회 (추가 기능)"""
        async with self.get_session() as session:
            simulation = await session.get(Simulation, simulation_id)
            if not simulation:
                raise ValueError(f"Simulation {simulation_id} not found")
            
            # 인스턴스 상태 조회
            result = await session.execute(
                select(Instance)
                .where(Instance.simulation_id == simulation_id)
            )
            instances = result.scalars().all()
            
            # 상태별 집계
            status_counts = {}
            for instance in instances:
                status = instance.status
                status_counts[status] = status_counts.get(status, 0) + 1
            
            return {
                'simulation_id': simulation_id,
                'simulation_status': simulation.status,
                'pod_creation_status': simulation.pod_creation_status,
                'total_instances': len(instances),
                'status_breakdown': status_counts,
                'progress_percentage': self._calculate_progress_percentage(simulation, instances)
            }
    
    def _calculate_progress_percentage(self, simulation, instances) -> float:
        """진행률 계산 (private method)"""
        if not instances:
            return 0.0
        
        completed_instances = sum(1 for inst in instances if inst.status in ["RUNNING", "COMPLETED"])
        return (completed_instances / len(instances)) * 100.0

    async def cleanup_simulation_resources(self, simulation_id: int):
        """시뮬레이션 리소스 정리 (추가 기능)"""
        async with self.get_session() as session:
            # 실행 중인 인스턴스들을 CANCELLED 상태로 변경
            result = await session.execute(
                select(Instance)
                .where(Instance.simulation_id == simulation_id)
                .where(Instance.status.in_(["PENDING", "CREATING", "RUNNING"]))
            )
            running_instances = result.scalars().all()
            
            cancelled_count = 0
            for instance in running_instances:
                instance.status = "CANCELLED"
                instance.updated_at = datetime.now()
                session.add(instance)
                cancelled_count += 1
            
            print(f"[CLEANUP] {cancelled_count}개 인스턴스를 CANCELLED 상태로 변경")
            return cancelled_count

    async def bulk_update_instances(self, instance_updates: list[dict]):
        """인스턴스 대량 업데이트 (성능 최적화)"""
        async with self.get_session() as session:
            updated_count = 0
            
            for update_data in instance_updates:
                instance_id = update_data.get('instance_id')
                if not instance_id:
                    continue
                
                instance = await session.get(Instance, instance_id)
                if not instance:
                    continue
                
                # 업데이트 적용
                for field, value in update_data.items():
                    if field != 'instance_id' and hasattr(instance, field):
                        setattr(instance, field, value)
                
                instance.updated_at = datetime.now()
                session.add(instance)
                updated_count += 1
            
            print(f"[BULK UPDATE] {updated_count}개 인스턴스 대량 업데이트 완료")
            return updated_count

    async def get_group_progress(self, simulation_id: int) -> list[dict]:
        """그룹별 진행 상황 조회"""
        async with self.get_session() as session:
            # 그룹 정보 조회
            result = await session.execute(
                select(SimulationGroup)
                .where(SimulationGroup.simulation_id == simulation_id)
            )
            groups = result.scalars().all()
            
            group_progress_list = []
            
            for group in groups:
                # 그룹별 인스턴스 조회
                instance_result = await session.execute(
                    select(Instance)
                    .where(Instance.simulation_id == simulation_id)
                    .where(Instance.group_id == group.id)
                )
                instances = instance_result.scalars().all()
                
                # 상태별 집계
                status_counts = {}
                for instance in instances:
                    status = instance.status
                    status_counts[status] = status_counts.get(status, 0) + 1
                
                group_progress = {
                    'group_id': group.id,
                    'group_name': group.group_name,
                    'template_id': group.template_id,
                    'status': group.status,
                    'expected_pods': group.expected_pods_count,
                    'actual_instances': len(instances),
                    'successful_agents': group.successful_agents or 0,
                    'failed_agents': group.failed_agents or 0,
                    'status_breakdown': status_counts,
                    'progress_percentage': self._calculate_progress_percentage(group, instances)
                }
                group_progress_list.append(group_progress)
            
            return group_progress_list

# 전역 인스턴스
status_manager = None

def init_status_manager(sessionmaker):
    """초기화 함수"""
    global status_manager
    status_manager = StatusUpdateManager(sessionmaker)
    print("StatusUpdateManager 초기화 완료 (병렬 처리 안정화)")

def get_status_manager():
    """안전한 접근을 위한 getter 함수"""
    if status_manager is None:
        raise RuntimeError("StatusUpdateManager가 초기화되지 않았습니다. init_status_manager()를 먼저 호출하세요.")
    return status_manager