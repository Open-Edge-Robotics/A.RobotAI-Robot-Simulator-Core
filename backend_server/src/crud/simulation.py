import asyncio
from datetime import datetime, timezone
import traceback
from typing import Any, Dict, Tuple, List, Optional
from fastapi import HTTPException, status
from sqlalchemy import select, exists, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload
from starlette.status import HTTP_409_CONFLICT

from repositories.instance_repository import InstanceRepository
from repositories.template_repository import TemplateRepository
from utils.status_update_manager import get_status_manager
from schemas.simulation_update import PatternUpdateRequest
from database.redis_simulation_client import RedisSimulationClient
from schemas.simulation_status import CurrentStatus, CurrentTimestamps, GroupDetail, ParallelProgress, SequentialProgress, StepDetail
from crud.metrics_collector import MetricsCollector
from schemas.dashboard import DashboardData
from utils.simulation_utils import extract_simulation_dashboard_data
from state import SimulationState
from utils.debug_print import debug_print
from utils.rosbag_executor import RosbagExecutor
from schemas.simulation_detail import CurrentStatusInitiating, CurrentStatusPENDING, ExecutionPlanParallel, ExecutionPlanSequential, GroupModel, ProgressModel, SimulationData, StepModel, TimestampModel
from schemas.pod import GroupIdFilter, StepOrderFilter
from repositories.simulation_repository import SimulationRepository
from schemas.pagination import PaginationMeta, PaginationParams
from models.enums import ExecutionStatus, GroupStatus, PatternType, SimulationStatus, StepStatus
from utils.simulation_background import (
    handle_parallel_pattern_background,
    handle_sequential_pattern_background,
    process_single_step,
)
from .template import TemplateService

from .pod import PodService
from .rosbag import RosService
from models.instance import Instance
from models.simulation import Simulation
from models.simulation_groups import SimulationGroup
from models.simulation_steps import SimulationStep
from schemas.simulation import (
    SimulationCreateRequest,
    SimulationListItem,
    SimulationListResponse,
    SimulationCreateResponse,
    SimulationDeleteResponse,
    SimulationControlResponse,
    SimulationPatternUpdateRequest,
    SimulationPatternUpdateResponse,
    SimulationOverview,
    SimulationSummaryItem
)
from utils.my_enum import PodStatus, API
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import async_sessionmaker

class SimulationService:
    def __init__(self, session: AsyncSession, sessionmaker: async_sessionmaker, repository: SimulationRepository, template_repository: TemplateRepository, instance_repository: InstanceRepository, state: SimulationState):
        self.session = session
        self.sessionmaker = sessionmaker
        self.repository = repository
        self.template_repository = template_repository
        self.instance_repository = instance_repository
        self.state = state
        self.pod_service = PodService()
        self.templates_service = TemplateService(session)
        
        # RosbagExecutor 초기화 (pod_service와 ros_service 의존성 주입)
        self.rosbag_executor = RosbagExecutor(self.pod_service)
        self.collector = MetricsCollector()   

    async def create_simulation(
        self,
        simulation_create_data: SimulationCreateRequest,
        background_tasks: BackgroundTasks
    ):
        print("--- create_simulation 메서드 시작 ---")
        print(f"받은 요청 데이터: {simulation_create_data.model_dump_json()}")
        
        api = API.CREATE_INSTANCE.value
        
        # 생성된 리소스 추적 (실패 시 정리용)
        simulation_id = None
        created_namespace = None

        try:
            # [단계 1] 템플릿 존재 여부 검증
            print("\n[단계 1] 템플릿 존재 여부 검증 시작")
            await self._validate_template_existence(simulation_create_data, api)
            print("모든 템플릿 존재 여부 검증 완료")
            
            # [단계 2] 예상 Pod 수 계산
            print("\n[단계 2] 예상 Pod 수 계산 시작")
            total_expected_pods = self._calculate_expected_pods(simulation_create_data)
            print(f"총 예상 Pod 수: {total_expected_pods}")

            # [단계 3] 트랜잭션으로 시뮬레이션 생성
            print("\n[단계 3] 시뮬레이션 생성 및 네임스페이스 생성")
            response_data = await self._create_simulation(
                simulation_create_data, 
                total_expected_pods
            )
            
            simulation_id = response_data['simulation_id']
            created_namespace = response_data['namespace']
            
            print(f"시뮬레이션 생성 완료: ID={simulation_id}, namespace={created_namespace}")
            
            print("\n[단계 4] 상태 관리자 초기화")
            from utils.status_update_manager import init_status_manager
            init_status_manager(self.sessionmaker)

            # [단계 5] 백그라운드 작업 시작
            print("\n[단계 5] 패턴 생성 (백그라운드) 처리 시작")
            await self._start_background_pattern_creation(
                background_tasks, 
                simulation_create_data, 
                simulation_id, 
                api
            )
            
            return SimulationCreateResponse(
                simulation_id=response_data['simulation_id'],
                simulation_name=response_data['simulation_name'],
                simulation_description=response_data['simulation_description'],
                pattern_type=response_data['pattern_type'],
                status=response_data['status'],
                simulation_namespace=response_data['namespace'],
                mec_id=response_data['mec_id'],
                created_at=str(response_data['created_at']),
                total_expected_pods=response_data['total_expected_pods']
            )
            
        except HTTPException:
            # HTTPException은 그대로 재발생
            raise
        except Exception as e:
            print(f"예상치 못한 오류 발생: {e}")
            print(f"스택 트레이스: {traceback.format_exc()}")
            
            # 생성된 리소스 정리
            await self._safe_cleanup_resources(simulation_id, created_namespace)
            
            raise HTTPException(
                status_code=500,
                detail=f"시뮬레이션 생성 중 오류 발생: {str(e)}"
            )

    def _extract_template_ids(self, simulation_create_data: SimulationCreateRequest) -> List[int]:
        """시뮬레이션 요청에서 모든 templateId 추출"""
        template_ids = []
        
        if simulation_create_data.pattern_type == PatternType.SEQUENTIAL:
            # 순차 패턴: pattern.steps[].templateId
            if hasattr(simulation_create_data.pattern, 'steps'):
                for step in simulation_create_data.pattern.steps:
                    template_ids.append(step.template_id)
        elif simulation_create_data.pattern_type == PatternType.PARALLEL:
            # 병렬 패턴: pattern.groups[].templateId  
            if hasattr(simulation_create_data.pattern, 'groups'):
                for group in simulation_create_data.pattern.groups:
                    template_ids.append(group.template_id)
        
        # 중복 제거
        return list(set(template_ids))
    
    async def _validate_template_existence(
        self, 
        simulation_create_data: SimulationCreateRequest, 
        api: str
    ):
        """템플릿 존재 여부 검증"""
        
        # 1. 모든 templateId 추출
        template_ids = self._extract_template_ids(simulation_create_data)
        
        if not template_ids:
            raise HTTPException(
                status_code=400,
                detail="시뮬레이션 패턴에 템플릿 ID가 지정되지 않았습니다."
            )
        
        print(f"검증할 템플릿 ID 목록: {template_ids}")
        
        # 2. 각 템플릿 존재 여부 확인
        missing_template_ids = []
        existing_template_ids = []
        
        async with self.sessionmaker() as session:
            templates_service = TemplateService(session)
            
            for template_id in template_ids:
                try:
                    template = await templates_service.find_template_by_id(template_id, api)
                    existing_template_ids.append(template.template_id)
                    print(f"  ✅ 템플릿 ID {template_id}: 존재함 (타입: {template.type})")
                    
                except Exception as e:
                    print(f"  ❌ 템플릿 ID {template_id}: 존재하지 않음 ({str(e)})")
                    missing_template_ids.append(template_id)
        
        # 3. 누락된 템플릿이 있으면 예외 발생
        if missing_template_ids:
            missing_str = ", ".join(map(str, missing_template_ids))
            suggestions = (
                "템플릿 ID가 올바른지 확인해주세요. "
                "템플릿이 삭제되었거나 비활성화되었을 수 있습니다. "
                "템플릿 목록을 다시 조회해서 유효한 ID를 사용해주세요."
            )
            message = (
                f"다음 템플릿 ID를 찾을 수 없습니다: {missing_str}. "
                f"{suggestions}"
            )

            raise HTTPException(
                status_code=400,
                detail=message
            )
        
        # 4. 검증 완료 로그
        print(f"✅ 모든 템플릿 검증 완료:")

    def _calculate_expected_pods(self, simulation_create_data: SimulationCreateRequest) -> int:
        """예상 Pod 수 계산"""
        total_expected_pods = 0
        
        if simulation_create_data.pattern_type == PatternType.SEQUENTIAL:
            for step in simulation_create_data.pattern.steps:
                total_expected_pods += step.autonomous_agent_count
                print(f"Step {step.step_order}: {step.autonomous_agent_count}개 Pod")
        else:  # PARALLEL
            for group in simulation_create_data.pattern.groups:
                total_expected_pods += group.autonomous_agent_count
                print(f"Agent {group.template_id}: {group.autonomous_agent_count}개 Pod")
                
        return total_expected_pods

    async def _create_simulation(
        self, 
        simulation_create_data: SimulationCreateRequest, 
        total_expected_pods: int
    ) -> dict:
        """시뮬레이션 생성"""
        
        simulation_id = None
        created_namespace = None
        
        try:
            # [1단계] DB에 시뮬레이션 저장 (트랜잭션)
            async with self.sessionmaker() as db_session:
                async with db_session.begin():
                    # 중복 검사 (DB 제약조건과 함께 이중 보호)
                    statement = select(exists().where(
                        Simulation.name == simulation_create_data.simulation_name
                    ))
                    is_existed = await db_session.scalar(statement)
                    
                    if is_existed:
                        print(f"ERROR: 시뮬레이션 이름 '{simulation_create_data.simulation_name}'이 이미 존재")
                        raise HTTPException(
                            status_code=HTTP_409_CONFLICT,
                            detail=f"시뮬레이션 이름이 이미 존재합니다."
                        )
                    
                    new_simulation = Simulation(
                        name=simulation_create_data.simulation_name,
                        description=simulation_create_data.simulation_description,
                        pattern_type=simulation_create_data.pattern_type,
                        mec_id=simulation_create_data.mec_id,
                        status=SimulationStatus.INITIATING,
                        total_expected_pods=total_expected_pods,
                        total_pods=0,
                        namespace=None,
                    )
                    db_session.add(new_simulation)
                    await db_session.flush()  # ID 생성
                    simulation_id = new_simulation.id
                    # 트랜잭션 커밋됨
            
            # [2단계] 네임스페이스 생성 (트랜잭션 외부에서)
            print(f"네임스페이스 생성 시작: simulation-{simulation_id}")
            try:
                created_namespace = await self.pod_service.create_namespace(simulation_id)
                print(f"네임스페이스 생성 완료: {created_namespace}")
                
                # 검증
                expected_namespace = f"simulation-{simulation_id}"
                if created_namespace != expected_namespace:
                    print(f"WARNING: 예상 네임스페이스명({expected_namespace})과 실제 생성된 네임스페이스명({created_namespace})이 다름")
                
            except Exception as ns_error:
                print(f"네임스페이스 생성 실패: {ns_error}")
                # DB 레코드 정리
                await self._cleanup_simulation_record(simulation_id)
                raise HTTPException(
                    status_code=500,
                    detail=f"네임스페이스 생성 실패: {str(ns_error)}"
                )
            
            # [3단계] 네임스페이스 정보 업데이트
            async with self.sessionmaker() as db_session:
                async with db_session.begin():
                    # 다시 조회해서 업데이트
                    simulation = await db_session.get(Simulation, simulation_id)
                    if not simulation:
                        raise Exception(f"시뮬레이션 ID {simulation_id}를 찾을 수 없습니다")
                    simulation.namespace = created_namespace
                    
                    print(f"시뮬레이션 상태 업데이트 완료: ID={simulation_id}, namespace={created_namespace}")
                    
                    # 응답 데이터 준비
                    response_data = {
                        'simulation_id': simulation.id,
                        'simulation_name': simulation.name,
                        'simulation_description': simulation.description,
                        'pattern_type': simulation.pattern_type,
                        'status': simulation.status,
                        'namespace': simulation.namespace,
                        'mec_id': simulation.mec_id,
                        'created_at': simulation.created_at,
                        'total_expected_pods': simulation.total_expected_pods
                    }
                    # 트랜잭션 커밋됨
                    
            return response_data
            
        except HTTPException:
            # HTTPException은 그대로 재발생
            raise
        except Exception as e:
            print(f"시뮬레이션 생성 중 예상치 못한 오류: {e}")
            # 생성된 리소스 정리
            if simulation_id and created_namespace:
                await self._safe_cleanup_resources(simulation_id, created_namespace)
            elif simulation_id:
                await self._cleanup_simulation_record(simulation_id)
            raise HTTPException(
                status_code=500,
                detail=f"시뮬레이션 생성 실패: {str(e)}"
            )

    async def _start_background_pattern_creation(
        self, 
        background_tasks: BackgroundTasks, 
        simulation_create_data: SimulationCreateRequest, 
        simulation_id: int, 
        api: str
    ):
        """백그라운드 패턴 생성 작업 시작"""
        
        if simulation_create_data.pattern_type == PatternType.SEQUENTIAL:
            print("패턴 타입: sequential. 백그라운드 작업 추가 중...")
            background_tasks.add_task(
                handle_sequential_pattern_background,
                sessionmaker=self.sessionmaker,
                simulation_id=simulation_id,
                steps_data=simulation_create_data.pattern.steps,
                api=api
            )
        elif simulation_create_data.pattern_type == PatternType.PARALLEL:
            print("패턴 타입: parallel. 백그라운드 작업 추가 중...")
            background_tasks.add_task(
                handle_parallel_pattern_background,
                sessionmaker=self.sessionmaker,
                simulation_id=simulation_id,
                groups_data=simulation_create_data.pattern.groups,
                api=api,
            )
        else:
            print(f"ERROR: 지원하지 않는 패턴 타입. pattern_type={simulation_create_data.pattern_type}")
            raise HTTPException(
                status_code=400, 
                detail="지원하지 않는 패턴 타입입니다."
            )

    async def _cleanup_simulation_record(self, simulation_id: int):
        """시뮬레이션 레코드만 정리"""
        if not simulation_id:
            return
            
        try:
            async with self.sessionmaker() as session:
                async with session.begin():
                    simulation = await session.get(Simulation, simulation_id)
                    if simulation:
                        await session.delete(simulation)
                        print(f"시뮬레이션 레코드 정리 완료: {simulation_id}")
        except Exception as e:
            print(f"시뮬레이션 레코드 정리 실패: {e}")
            raise
            
    async def _cleanup_namespace(self, simulation_id: int):
        """네임스페이스만 정리"""
        if not simulation_id:
            return
            
        try:
            await self.pod_service.delete_namespace(simulation_id)
            print(f"네임스페이스 정리 완료: simulation-{simulation_id}")
        except Exception as e:
            print(f"네임스페이스 정리 실패: {e}")

    async def _safe_cleanup_resources(self, simulation_id: int = None, namespace: str = None):
        """안전한 리소스 정리 (실패해도 다른 정리 작업 계속 진행)"""
        if not simulation_id:
            return
            
        print(f"리소스 정리 시작: simulation_id={simulation_id}, namespace={namespace}")
        cleanup_errors = []
        
        # 네임스페이스 정리 (실패해도 계속 진행)
        try:
            await self._cleanup_namespace(simulation_id)
        except Exception as e:
            cleanup_errors.append(f"네임스페이스 정리 실패: {e}")
        
        # DB 정리 (실패해도 계속 진행)
        try:
            await self._cleanup_simulation_record(simulation_id)
        except Exception as e:
            cleanup_errors.append(f"DB 정리 실패: {e}")
        
        if cleanup_errors:
            print(f"정리 과정에서 발생한 오류들: {cleanup_errors}")
            # 정리 오류는 로깅만 하고 예외는 발생시키지 않음
                
    async def _cleanup_simulation_record(self, simulation_id: int):
        """시뮬레이션 레코드만 정리"""
        try:
            async with self.sessionmaker() as session:
                async with session.begin():
                    simulation = await session.get(Simulation, simulation_id)
                    if simulation:
                        await session.delete(simulation)
                        print(f"시뮬레이션 레코드 정리 완료: {simulation_id}")
        except Exception as e:
            print(f"시뮬레이션 레코드 정리 실패: {e}")
            raise         
        
    async def get_simulations_with_pagination(
        self, 
        pagination: PaginationParams,
        pattern_type: Optional[PatternType] = None,
        status: Optional[SimulationStatus] = None
    ) -> Tuple[List[SimulationListItem], PaginationMeta]:
        """페이지네이션된 시뮬레이션 목록 조회 (선택적 필터링 지원"""
        # 1. 전체 데이터 개수 조회 (페이지 범위 검증용)
        total_count = await self.repository.count_all(pattern_type=pattern_type, status=status)
        
        # 2. 페이지 범위 검증
        self._validate_pagination_range(pagination, total_count)
        
        # 3. 실제 데이터 조회 (필터 + 페이지 적용)
        simulations = await self.repository.find_all_with_pagination(
            pagination,
            pattern_type=pattern_type,
            status=status
        )
        
        # 4. 비즈니스 로직: 응답 데이터 변환
        simulation_items = self._convert_to_list_items(simulations)
        
        # 5. 페이지네이션 메타데이터 생성
        pagination_meta = PaginationMeta.create(
            page=pagination.page,
            size=len(simulation_items) if simulation_items else 0,
            total_items=total_count
        )
        
        return simulation_items, pagination_meta
    
    async def get_simulation_overview(self) -> SimulationOverview:
        overview_data = await self.repository.get_overview()
        print(overview_data)
        return SimulationOverview.from_dict(overview_data)
    
    async def get_simulation(self, simulation_id: int) -> SimulationData:
        sim = await self.repository.find_by_id(simulation_id)
        if not sim:
            raise HTTPException(status_code=404, detail=f"Simulation {simulation_id} not found")
        
        # 패턴별 ExecutionPlan 조회
        if sim.pattern_type == PatternType.SEQUENTIAL:
            execution_plan = await self.get_execution_plan_sequential(sim.id)
        elif sim.pattern_type == PatternType.PARALLEL:  # parallel
            execution_plan = await self.get_execution_plan_parallel(sim.id)
            
        # 상태별 CurrentStatus DTO 생성
        if sim.status == SimulationStatus.INITIATING:
            current_status = CurrentStatusInitiating(
                status=sim.status,
                timestamps=TimestampModel(
                    created_at=sim.created_at,
                    last_updated=sim.updated_at
                )
            )
        elif sim.status == SimulationStatus.PENDING:
            current_status = CurrentStatusPENDING(
                status=sim.status,
                progress=ProgressModel(
                    overall_progress=0.0,
                    ready_to_start=True
                ),
                timestamps=TimestampModel(
                    created_at=sim.created_at,
                    last_updated=sim.updated_at
                )
            )
        else:
            # 예상하지 못한 상태에 대한 기본 처리
            current_status = CurrentStatusInitiating(
                status=sim.status,
                timestamps=TimestampModel(
                    created_at=sim.created_at,
                    last_updated=sim.updated_at
                )
            )
            
        return SimulationData(
            simulation_id=sim.id,
            simulation_name=sim.name,
            simulation_description=sim.description,
            pattern_type=sim.pattern_type,
            mec_id=sim.mec_id,
            namespace=sim.namespace,
            created_at=sim.created_at,
            execution_plan=execution_plan,
            current_status=current_status
        )
        
    async def get_execution_plan_sequential(self, simulation_id: int) -> ExecutionPlanSequential:
        steps = await self.repository.find_steps_with_template(simulation_id)

        dto_steps = [
            StepModel(
                step_order=s.step_order,
                template_id=s.template.template_id,
                template_type=s.template.type,  # join으로 가져온 Template.name
                autonomous_agent_count=s.autonomous_agent_count,
                repeat_count=s.repeat_count,
                execution_time=s.execution_time,
                delay_after_completion=s.delay_after_completion
            )
            for s in steps
        ]
        return ExecutionPlanSequential(steps=dto_steps)

    async def get_execution_plan_parallel(self, simulation_id: int) -> ExecutionPlanParallel:
        groups = await self.repository.find_groups_with_template(simulation_id)

        dto_groups = [
            GroupModel(
                template_id=g.template.template_id,
                template_type=g.template.type,  # join으로 가져온 Template.name
                autonomous_agent_count=g.autonomous_agent_count,
                repeat_count=g.repeat_count,
                execution_time=g.execution_time
            )
            for g in groups
        ]
        return ExecutionPlanParallel(groups=dto_groups)
                    
    async def get_all_simulations(self):
        statement = (
            select(Simulation)
            .options(selectinload(Simulation.instances))
            .order_by(Simulation.id.desc())
        )
        results = await self.session.execute(statement)
        simulations = results.scalars().all()

        simulation_list = []

        for simulation in simulations:
            simulation_status = await self.get_simulation_status(simulation)

            response = SimulationListResponse(
                simulation_id=simulation.id,
                simulation_name=simulation.name,
                simulation_description=simulation.description,
                simulation_namespace=simulation.namespace,
                simulation_created_at=str(simulation.created_at),
                simulation_status=simulation_status,
                template_id=simulation.template_id,
                autonomous_agent_count=simulation.autonomous_agent_count,
                execution_time=simulation.execution_time,
                delay_time=simulation.delay_time,
                repeat_count=simulation.repeat_count,
                scheduled_start_time=(
                    str(simulation.scheduled_start_time)
                    if simulation.scheduled_start_time
                    else None
                ),
                scheduled_end_time=(
                    str(simulation.scheduled_end_time)
                    if simulation.scheduled_end_time
                    else None
                ),
                mec_id=simulation.mec_id,
            )
            simulation_list.append(response)

        return simulation_list

    async def update_simulation_pattern(
        self, simulation_id: int, pattern_data: SimulationPatternUpdateRequest
    ):
        """시뮬레이션 패턴 설정 업데이트"""
        simulation = await self.find_simulation_by_id(
            simulation_id, "update simulation pattern"
        )

        # 시뮬레이션이 실행 중인지 확인
        current_status = await self.get_simulation_status(simulation)
        if current_status == SimulationStatus.ACTIVE.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="실행 중인 시뮬레이션의 패턴은 수정할 수 없습니다.",
            )

        # 스케줄 시간 검증
        if (
            pattern_data.scheduled_start_time
            and pattern_data.scheduled_end_time
            and pattern_data.scheduled_start_time >= pattern_data.scheduled_end_time
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="종료 시간은 시작 시간보다 늦어야 합니다.",
            )

        # 업데이트할 필드들 준비
        update_data = {}
        for field, value in pattern_data.model_dump(exclude_unset=True).items():
            update_data[field] = value

        if update_data:
            update_data["updated_at"] = datetime.now()

            statement = (
                update(Simulation)
                .where(Simulation.id == simulation_id)
                .values(**update_data)
            )
            await self.session.execute(statement)
            await self.session.commit()

        return SimulationPatternUpdateResponse(
            simulation_id=simulation_id,
            message="패턴 설정이 성공적으로 업데이트되었습니다",
        ).model_dump()

    async def start_simulation_async(self, simulation_id: int):
        """
        API 호출용 메서드
        시뮬레이션 시작 요청을 받고, 패턴 타입에 따라 분기 처리 후 메타데이터만 즉시 리턴
        """
        debug_print("🚀 시뮬레이션 시작 메서드 진입", simulation_id=simulation_id)
            
        try:
            debug_print("📋 시뮬레이션 조회 시작", simulation_id=simulation_id)
            simulation = await self.find_simulation_by_id(simulation_id, "start simulation")
            
            # 이미 실행 중이면 409 Conflict
            if simulation.status == SimulationStatus.RUNNING:
                raise HTTPException(
                    status_code=409,
                    detail=f"이미 실행 중인 시뮬레이션입니다 (ID: {simulation_id})"
                )

            simulation_data = {
                "id": simulation.id,
                "name": simulation.name,
                "pattern_type": simulation.pattern_type
            }
            
            debug_print("✅ 시뮬레이션 조회 완료", 
                    simulation_id=simulation_data["id"], 
                    name=simulation_data["name"], 
                    pattern_type=simulation_data["pattern_type"])
            
            # 중지 이벤트 생성
            stop_event = asyncio.Event()

            # 패턴 타입에 따른 분기 처리 (simulation_data 사용)
            if simulation_data["pattern_type"] == "sequential":
                pattern_name = "순차"
                background_task = self._run_sequential_simulation_with_progress(simulation_id, stop_event)
                debug_print("🔄 순차 패턴 선택", simulation_id=simulation_id)
            elif simulation_data["pattern_type"] == "parallel":
                pattern_name = "병렬"
                background_task = self._run_parallel_simulation_with_progress(simulation_id, stop_event)
                debug_print("🔄 병렬 패턴 선택", simulation_id=simulation_id)
            else:
                debug_print("❌ 지원하지 않는 패턴 타입", pattern_type=simulation_data["pattern_type"])
                raise ValueError(f"지원하지 않는 패턴 타입: {simulation_data['pattern_type']}")

            debug_print("📝 시뮬레이션 상태 업데이트 시작 (RUNNING)", simulation_id=simulation_id)
            await self._update_simulation_status_and_log(
                simulation_id, SimulationStatus.RUNNING, f"{pattern_name} 시뮬레이션 시작"
            )
            debug_print("✅ 시뮬레이션 상태 업데이트 완료", simulation_id=simulation_id, status="RUNNING")

            debug_print("🎯 백그라운드 태스크 생성 시작", simulation_id=simulation_id)
            task = asyncio.create_task(background_task)
            task.set_name(f"simulation_{simulation_id}_{pattern_name}")
            debug_print("✅ 백그라운드 태스크 생성 완료", 
                    simulation_id=simulation_id, 
                    task_name=task.get_name(),
                    task_id=id(task))
            
            # 실행 중 시뮬레이션 등록
            self.state.running_simulations[simulation_id] = {
                "task": task,
                "stop_event": stop_event,
                "pattern_type": simulation_data["pattern_type"],
                "stop_handler": None,  # 중지 처리 담당자
                "is_stopping": False   # 중지 진행 중 플래그
            }
            debug_print(f"{self.state.running_simulations[simulation_id]}")

            debug_print("📤 API 응답 반환", simulation_id=simulation_id)
            return {
                "simulationId": simulation_id,
                "status": "RUNNING",
                "patternType": simulation_data["pattern_type"],
                "startedAt": datetime.now(timezone.utc)
            }
            
        except HTTPException:
            raise
        except Exception as e:
            failure_reason = f"시뮬레이션 시작 중 예상치 못한 오류: {str(e)}"
            print(f"❌ {failure_reason}")
            raise HTTPException(
                status_code=500,
                detail="시뮬레이션 시작 중 내부 오류가 발생했습니다"
            )

    def _cleanup_simulation(self, simulation_id: int):
        """시뮬레이션 완료/취소 후 정리"""
        if simulation_id in self.state.running_simulations:
            print(f"시뮬레이션 {simulation_id} 정리 완료")
            del self.state.running_simulations[simulation_id]
   
    async def _run_sequential_simulation(self, simulation_id: int, stop_event: asyncio.Event):
        """
        순차 패턴 시뮬레이션 실행
        - 각 스텝을 순차적으로 처리
        - 1초 단위 Pod 진행상황 모니터링
        """
        redis_client = RedisSimulationClient()
        await redis_client.connect()
        
        try:
            debug_print(f"백그라운드에서 시뮬레이션 실행 시작: {simulation_id}")

            # 1️⃣ 스텝 조회
            simulation = await self.find_simulation_by_id(simulation_id, "background run")
            steps = await self.repository.find_simulation_steps(simulation_id)
            debug_print(f"📊 스텝 조회 완료: {len(steps)}개")
            
            # Redis 초기 상태 설정
            current_time = datetime.now(timezone.utc)
            initial_status = {
                "status": "RUNNING",
                "progress": {
                    "overallProgress": 0.0,
                    "currentStep": None,
                    "completedSteps": 0,
                    "totalSteps": len(steps)
                },
                "timestamps": {
                    "createdAt": simulation.created_at if simulation.created_at else None,
                    "lastUpdated": current_time,
                    "startedAt": current_time,
                    "completedAt": None,
                    "failedAt": None,
                    "stoppedAt": None
                },
                "message": f"시뮬레이션 시작 - 총 {len(steps)}개 스텝",
                "stepDetails": [
                    {
                        "stepOrder": step.step_order,
                        "status": "PENDING",
                        "progress": 0.0,
                        "startedAt": None,
                        "completedAt": None,
                        "failedAt": None,
                        "stoppedAt": None,
                        "autonomousAgents": 0,
                        "currentRepeat": 0,
                        "totalRepeats": step.repeat_count or 1,
                        "error": None
                    } for step in steps
                ]
            }
            await redis_client.set_simulation_status(simulation_id, initial_status)

            total_execution_summary = {
                "total_steps": len(steps),
                "completed_steps": 0,
                "failed_steps": 0,
                "total_pods_executed": 0,
                "total_success_pods": 0,
                "total_failed_pods": 0,
                "step_results": [],
                "simulation_status": "RUNNING",
                "failure_reason": None
            }

            # 2️⃣ 각 스텝 처리
            for i, step in enumerate(steps, 1):
                debug_print(f"\n🔄 스텝 {i}/{len(steps)} 처리 시작 - Step ID: {step.id}")
                step_start_time = datetime.now(timezone.utc)
                
                # 🔄 Step 상태를 RUNNING으로 변경하고 시작 시간 기록
                await self.repository.update_simulation_step_status(
                    step_id=step.id,
                    status=StepStatus.RUNNING,
                    started_at=step_start_time
                )
                debug_print(f"📝 Step {step.step_order} 상태 업데이트: RUNNING")
                
                # Redis 스텝 시작 상태 업데이트
                current_status = await redis_client.get_simulation_status(simulation_id)
                if current_status:
                    # 현재 스텝 정보 업데이트
                    current_status["progress"]["currentStep"] = step.step_order
                    current_status["message"] = f"스텝 {step.step_order}/{len(steps)} 실행 중"
                    current_status["timestamps"]["lastUpdated"] = step_start_time
                    
                    # 스텝 디테일 업데이트
                    for step_detail in current_status["stepDetails"]:
                        if step_detail["stepOrder"] == step.step_order:
                            step_detail.update({
                                "status": "RUNNING",
                                "startedAt": step_start_time,
                                "progress": 0.0
                            })
                            break
                            
                    await redis_client.set_simulation_status(simulation_id, current_status)

                # Pod 조회
                pod_list = self.pod_service.get_pods_by_filter(
                    namespace=simulation.namespace,
                    filter_params=StepOrderFilter(step_order=step.step_order)
                )

                if not pod_list:
                    failure_reason = f"스텝 {step.step_order}에서 Pod를 찾을 수 없음"
                    debug_print(f"❌ {failure_reason}")
                    
                    # 🔄 Step 상태를 FAILED로 변경
                    failed_time = datetime.now(timezone.utc)
                    await self.repository.update_simulation_step_status(
                        step_id=step.id,
                        status=StepStatus.FAILED,
                        failed_at=failed_time
                    )
                    
                    # Redis 실패 상태 업데이트
                    current_status = await redis_client.get_simulation_status(simulation_id)
                    if current_status:
                        current_status["status"] = "FAILED"
                        current_status["message"] = failure_reason
                        current_status["timestamps"].update({
                            "lastUpdated": failed_time,
                            "failedAt": failed_time
                        })
                        
                        # 스텝 디테일 업데이트
                        for step_detail in current_status["stepDetails"]:
                            if step_detail["stepOrder"] == step.step_order:
                                step_detail.update({
                                    "status": "FAILED",
                                    "failedAt": failed_time,
                                    "error": failure_reason
                                })
                                break
                        
                        await redis_client.set_simulation_status(simulation_id, current_status)
                    
                    
                    
                    total_execution_summary.update({
                        "failed_steps": total_execution_summary["failed_steps"] + 1,
                        "simulation_status": "FAILED",
                        "failure_reason": failure_reason
                    })
                    await self._update_simulation_status_and_log(simulation_id, "FAILED", failure_reason)
                    return total_execution_summary

                # Pod Task 생성
                pod_tasks = {
                    asyncio.create_task(self.rosbag_executor.execute_single_pod(pod, simulation, step=step)): pod.metadata.name
                    for pod in pod_list
                }

                completed_pods = set()
                poll_interval = 1  # 1초 단위 진행상황
                
                debug_print(f"📋 Step {step.step_order} Pod Task 생성 완료: {len(pod_tasks)}개 Pod 병렬 실행 시작")

                # 3️⃣ Pod 진행상황 루프
                last_recorded_repeat = 0  # 메모리 기반 반복 횟수 관리
                
                while len(completed_pods) < len(pod_list):
                    done_tasks = [t for t in pod_tasks if t.done()]

                    # 완료된 Pod 처리
                    for task in done_tasks:
                        pod_name = pod_tasks.pop(task)
                        try:
                            result = task.result()
                            debug_print(f"✅ Pod 완료: {result.pod_name} ({len(completed_pods)}/{len(pod_list)})")
                        except asyncio.CancelledError:
                            debug_print(f"🛑 Pod CancelledError 감지: {pod_name}")
                        except Exception as e:
                            debug_print(f"💥 Pod 실행 실패: {pod_name}: {e}")

                    # 진행 중 Pod 상태 확인 및 로그
                    total_progress = 0.0
                    running_info = []
                    
                    debug_print(f"🔍 Pod 상태 체크 시작 - completed_pods: {completed_pods}")

                    status_tasks = {
                        pod.metadata.name: asyncio.create_task(self.rosbag_executor._check_pod_rosbag_status(pod))
                        for pod in pod_list
                    }

                    pod_statuses = await asyncio.gather(*status_tasks.values(), return_exceptions=True)
                    
                    current_total_loops = 0
                    max_total_loops = 0
                    
                    # 🔍 각 Pod별 상세 디버깅
                    debug_print(f"📊 === Pod별 진행률 상세 분석 (Step {step.step_order}) ===")

                    loops = []  # (current_loop, max_loops) 집계용

                    for pod_name, status in zip(status_tasks.keys(), pod_statuses):
                        debug_print(f"🔍 === Pod [{pod_name}] 상태 체크 시작 ===")
                        # 이미 완료된 Pod는 바로 100% 처리
                        if pod_name in completed_pods:
                            pod_progress = 1.0
                            running_info.append(f"{pod_name}(완료)")
                        elif isinstance(status, dict):           
                            is_playing = status.get("is_playing", False)
                            current_loop = status.get("current_loop", 0)
                            max_loops = max(status.get("max_loops") or 1, 1)
                            
                            # 집계용 리스트에 기록
                            loops.append((current_loop, max_loops)) 
                            
                            debug_print(f"  🎮 {pod_name}: is_playing = {is_playing} (기본값: False)")
                            debug_print(f"  🔄 {pod_name}: current_loop = {current_loop} (기본값: 0)")
                            debug_print(f"  🎯 {pod_name}: max_loops = {max_loops} (기본값: 1, 원본값: {status.get('max_loops')})")
                            
                            # 전체 진행률 계산을 위한 루프 수 집계
                            current_total_loops += current_loop
                            max_total_loops += max_loops
                            
                            pod_progress = min(current_loop / max_loops, 1.0)
                            
                            if current_loop >= max_loops and not is_playing:
                                # 실제로 완료된 경우
                                completed_pods.add(pod_name)
                                pod_progress = 1.0
                                running_info.append(f"{pod_name}(완료)")
                                debug_print(f"  ✅ {pod_name}: 상태체크로 완료 감지 -> completed_pods에 추가")
                            elif is_playing:
                                running_info.append(f"{pod_name}({current_loop}/{max_loops})")
                                debug_print(f"  ⏳ {pod_name}: 실행중 -> 진행률 {pod_progress:.1%}")
                            else:
                                # is_playing이 False이지만 아직 완료되지 않은 경우
                                # 무조건 1.0이 아닌 실제 진행률 사용
                                running_info.append(f"{pod_name}({current_loop}/{max_loops}-중지됨)")
                                debug_print(f"  ⏸️ {pod_name}: 중지됨 -> 진행률 {pod_progress:.1%}")
                        else:
                            pod_progress = 0.0
                            running_info.append(f"{pod_name}(상태체크실패)")
                            debug_print(f"  ❌ {pod_name}: 상태체크 실패 -> 0%")

                        total_progress += pod_progress
                        debug_print(f"  📊 {pod_name}: pod_progress={pod_progress:.2f}, 누적 total_progress={total_progress:.2f}")

                    # 그룹 반복 갱신 (min(current_loop) 기준)
                    if loops:
                        group_current_loop = min(cl for cl, _ in loops)
                        group_max_loops = min(ml for _, ml in loops)
                        target_cap = step.repeat_count or group_max_loops
                        new_repeat = min(group_current_loop, target_cap)

                        if new_repeat > last_recorded_repeat and step.status != StepStatus.PENDING:
                            await self.repository.update_simulation_step_current_repeat(step_id=step.id, current_repeat=new_repeat)
                            step.current_repeat = new_repeat
                            last_recorded_repeat = new_repeat
                            debug_print(f"🔁 Step {step.step_order} 반복 갱신: {new_repeat}/{target_cap}")

                    group_progress = (total_progress / len(pod_list)) * 100
                    debug_print(f"⏳ Step {step.step_order} 진행률: {group_progress:.1f}% ({len(completed_pods)}/{len(pod_list)}) | 진행중: {', '.join(running_info)}")

                    # stop_event 감지
                    if stop_event.is_set():
                        debug_print(f"⏹️ 중지 이벤트 감지 - 스텝 {step.step_order} 즉시 종료")
                        
                        # 🔄 Step 상태를 STOPPED로 변경
                        await self.repository.update_simulation_step_status(
                            step_id=step.id,
                            status=StepStatus.STOPPED,
                            completed_at=datetime.now(timezone.utc)
                        )
                        
                        for t in pod_tasks.keys():
                            t.cancel()
                        await asyncio.gather(*pod_tasks.keys(), return_exceptions=True)
                        total_execution_summary["simulation_status"] = "STOPPED"
                        return total_execution_summary

                    await asyncio.sleep(poll_interval)

                # 스텝 완료 처리
                step_end_time = datetime.now(timezone.utc)
                step_execution_time = (step_end_time - step_start_time).total_seconds()
                
                # 실행 결과 요약 생성
                execution_summary = self.rosbag_executor.get_execution_summary([
                    task.result() for task in done_tasks if not isinstance(task.result(), Exception)
                ])
                
                # 🔄 Step 상태를 COMPLETED로 변경
                await self.repository.update_simulation_step_status(
                    step_id=step.id,
                    status=StepStatus.COMPLETED,
                    completed_at=step_end_time,
                    current_repeat=step.repeat_count  # 완료 시 최대값으로 설정
                )
                
                # Redis 스텝 완료 상태 업데이트
                current_status = await redis_client.get_simulation_status(simulation_id)
                if current_status:
                    current_status["progress"]["completedSteps"] += 1
                    overall_progress = current_status["progress"]["completedSteps"] / len(steps)
                    current_status["progress"]["overallProgress"] = overall_progress
                    current_status["message"] = f"스텝 {step.step_order} 완료 ({current_status['progress']['completedSteps']}/{len(steps)})"
                    current_status["timestamps"]["lastUpdated"] = step_end_time
                    
                    # 스텝 디테일 업데이트
                    for step_detail in current_status["stepDetails"]:
                        if step_detail["stepOrder"] == step.step_order:
                            step_detail.update({
                                "status": "COMPLETED",
                                "progress": 1.0,
                                "completedAt": step_end_time.isoformat(),
                                "currentRepeat": step.repeat_count or 1
                            })
                            break
                    
                    await redis_client.set_simulation_status(simulation_id, current_status)
                
                debug_print(f"✅ Step {step.step_order} 완료 (실행시간: {step_execution_time:.1f}초)")
                
                # 전체 실행 요약 업데이트
                total_execution_summary.update({
                    "completed_steps": total_execution_summary["completed_steps"] + 1,
                    "total_pods_executed": total_execution_summary["total_pods_executed"] + execution_summary['total_pods'],
                    "total_success_pods": total_execution_summary["total_success_pods"] + execution_summary['success_count']
                })
                
                total_execution_summary["step_results"].append({
                    "step_id": step.id,
                    "step_order": step.step_order,
                    "status": "success",
                    "execution_summary": execution_summary,
                    "execution_time": step_execution_time,
                    "pod_count": len(pod_list)
                })

                # 스텝 간 지연
                if i < len(steps) and step.delay_after_completion:
                    await asyncio.sleep(step.delay_after_completion)

            # 모든 스텝 성공 - Redis 최종 완료 상태 업데이트
            completed_time = datetime.now(timezone.utc)
            current_status = await redis_client.get_simulation_status(simulation_id)
            if current_status:
                current_status["status"] = "COMPLETED"
                current_status["progress"]["overallProgress"] = 1.0
                current_status["message"] = f"시뮬레이션 완료 - 모든 {len(steps)}개 스텝 성공"
                current_status["timestamps"].update({
                    "lastUpdated": completed_time,
                    "completedAt": completed_time
                })
            await redis_client.set_simulation_status(simulation_id, current_status)
            
            total_execution_summary["simulation_status"] = "COMPLETED"
            await self._update_simulation_status_and_log(simulation_id, "COMPLETED", "모든 스텝 성공")
            debug_print(f"🎉 시뮬레이션 {simulation_id} 완료")
            return total_execution_summary

        except asyncio.CancelledError:
            debug_print(f"🛑 시뮬레이션 {simulation_id} 태스크 취소됨")
            
            cancelled_time = datetime.now(timezone.utc)
            
            # 🔄 진행 중인 모든 스텝을 STOPPED 상태로 변경
            for step in steps:
                if step.status == StepStatus.RUNNING:
                    await self.repository.update_simulation_step_status(
                        step_id=step.id,
                        status=StepStatus.STOPPED,
                        completed_at=datetime.now(timezone.utc)
                    )
                    
            # Redis 취소 상태 업데이트
            current_status = await redis_client.get_simulation_status(simulation_id)
            if current_status:
                current_status["status"] = "STOPPED"
                current_status["message"] = "시뮬레이션 태스크가 취소되었습니다"
                current_status["timestamps"].update({
                    "lastUpdated": cancelled_time,
                    "stoppedAt": cancelled_time
                })
                await redis_client.set_simulation_status(simulation_id, current_status)

            raise
        except Exception as e:
            debug_print(f"❌ 시뮬레이션 {simulation_id} 실행 중 예외: {e}")
            
            error_time = datetime.now(timezone.utc)
            
            # 🔄 진행 중인 모든 스텝을 FAILED 상태로 변경
            for step in steps:
                if step.status == StepStatus.RUNNING:
                    await self.repository.update_simulation_step_status(
                        step_id=step.id,
                        status=StepStatus.FAILED,
                        failed_at=datetime.now(timezone.utc)
                    )
                    
            # Redis 실패 상태 업데이트
            current_status = await redis_client.get_simulation_status(simulation_id)
            if current_status:
                current_status["status"] = "FAILED"
                current_status["message"] = f"시뮬레이션 실행 중 오류 발생: {str(e)}"
                current_status["timestamps"].update({
                    "lastUpdated": error_time,
                    "failedAt": error_time
                })
                await redis_client.set_simulation_status(simulation_id, current_status)
            
            
            await self._update_simulation_status_and_log(simulation_id, "FAILED", str(e))
            raise
        finally:
            # Redis 정리는 TTL에 맡기고, 연결만 정리
            if redis_client.client:
                await redis_client.client.close()
            self._cleanup_simulation(simulation_id)
       
    async def _run_sequential_simulation_with_progress(self, simulation_id: int, stop_event: asyncio.Event):
        """
        순차 패턴 시뮬레이션 실행
        - 각 스텝을 순차적으로 처리
        - 1초 단위 Pod 진행상황 모니터링
        - Redis를 통한 실시간 진행상황 업데이트 (DB 업데이트 최소화)
        - 실패/중단 시 해당 스텝만 정확히 DB에 기록
        """
        redis_client = RedisSimulationClient()
        await redis_client.connect()
        
        # 현재 실행 중인 스텝 정보 추적용
        current_step = None
        current_step_progress = 0.0
        current_step_repeat = 0
        current_step_start_time = None
    
        try:
            debug_print(f"백그라운드에서 시뮬레이션 실행 시작: {simulation_id}")

            # 1️⃣ 스텝 조회
            simulation = await self.find_simulation_by_id(simulation_id, "background run")
            steps = await self.repository.find_simulation_steps(simulation_id)
            debug_print(f"📊 스텝 조회 완료: {len(steps)}개")

            # ✅ DB 업데이트 - 시뮬레이션 시작 시에만
            await self._update_simulation_status_and_log(simulation_id, "RUNNING", "시뮬레이션 실행 시작")

            # Redis 초기 상태 설정
            current_time = datetime.now(timezone.utc)
            initial_status = {
                "status": "RUNNING",
                "progress": {
                    "overallProgress": 0.0,
                    "currentStep": None,
                    "completedSteps": 0,
                    "totalSteps": len(steps)
                },
                "timestamps": {
                    "createdAt": simulation.created_at.isoformat() if simulation.created_at else None,
                    "lastUpdated": current_time.isoformat(),
                    "startedAt": current_time.isoformat(),
                    "completedAt": None,
                    "failedAt": None,
                    "stoppedAt": None
                },
                "message": f"시뮬레이션 시작 - 총 {len(steps)}개 스텝",
                "stepDetails": [
                    {
                        "stepOrder": step.step_order,
                        "status": "PENDING",
                        "progress": 0.0,
                        "startedAt": None,
                        "completedAt": None,
                        "failedAt": None,
                        "stoppedAt": None,
                        "autonomousAgents": step.autonomous_agent_count,
                        "currentRepeat": 0,
                        "totalRepeats": step.repeat_count or 1,
                        "error": None
                    } for step in steps
                ]
            }
            await redis_client.set_simulation_status(simulation_id, initial_status)

            total_execution_summary = {
                "total_steps": len(steps),
                "completed_steps": 0,
                "failed_steps": 0,
                "total_pods_executed": 0,
                "total_success_pods": 0,
                "total_failed_pods": 0,
                "step_results": [],
                "simulation_status": "RUNNING",
                "failure_reason": None
            }

            # 2️⃣ 각 스텝 처리
            for i, step in enumerate(steps, 1):
                debug_print(f"\n🔄 스텝 {i}/{len(steps)} 처리 시작 - Step ID: {step.id}")
                step_start_time = datetime.now(timezone.utc)
                
                # 현재 실행 중인 스텝 추적 정보 업데이트
                current_step = step
                current_step_progress = 0.0
                current_step_repeat = 0
                current_step_start_time = step_start_time
                
                debug_print(f"📝 Step {step.step_order} 실행 시작 - Redis에만 상태 업데이트")

                # ⚡ Redis Only - 스텝 시작 상태 업데이트
                current_status = await redis_client.get_simulation_status(simulation_id)
                if current_status:
                    # 현재 스텝 정보 업데이트
                    current_status["progress"]["currentStep"] = step.step_order
                    current_status["message"] = f"스텝 {step.step_order}/{len(steps)} 실행 중"
                    current_status["timestamps"]["lastUpdated"] = step_start_time.isoformat()
                    
                    # 스텝 디테일 업데이트
                    for step_detail in current_status["stepDetails"]:
                        if step_detail["stepOrder"] == step.step_order:
                            step_detail.update({
                                "status": "RUNNING",
                                "startedAt": step_start_time.isoformat(),
                                "progress": 0.0
                            })
                            break
                            
                    await redis_client.set_simulation_status(simulation_id, current_status)

                # Pod 조회
                pod_list = self.pod_service.get_pods_by_filter(
                    namespace=simulation.namespace,
                    filter_params=StepOrderFilter(step_order=step.step_order)
                )

                if not pod_list:
                    failure_reason = f"스텝 {step.step_order}에서 Pod를 찾을 수 없음"
                    debug_print(f"❌ {failure_reason}")
                    
                    failed_time = datetime.now(timezone.utc)
                    
                    # ✅ DB 업데이트 - 실패한 스텝만 정확히 기록
                    await self.repository.update_simulation_step_status(
                        step_id=step.id,
                        status=StepStatus.FAILED,
                        failed_at=failed_time
                    )
                    # 추가 실패 정보 업데이트 (current_repeat, progress 등)
                    await self.repository.update_simulation_step_current_repeat(
                        step_id=step.id, 
                        current_repeat=current_step_repeat
                    )
                    debug_print(f"✅ DB 업데이트 완료 - Step {step.step_order} FAILED 상태 기록")
                    
                    # ⚡ Redis 실패 상태 업데이트
                    current_status = await redis_client.get_simulation_status(simulation_id)
                    if current_status:
                        current_status["status"] = "FAILED"
                        current_status["message"] = failure_reason
                        current_status["timestamps"].update({
                            "lastUpdated": failed_time.isoformat(),
                            "failedAt": failed_time.isoformat()
                        })
                        
                        # 스텝 디테일 업데이트
                        for step_detail in current_status["stepDetails"]:
                            if step_detail["stepOrder"] == step.step_order:
                                step_detail.update({
                                    "status": "FAILED",
                                    "failedAt": failed_time.isoformat(),
                                    "error": failure_reason,
                                    "currentRepeat": current_step_repeat,
                                    "progress": current_step_progress
                                })
                                break
                        
                        await redis_client.set_simulation_status(simulation_id, current_status)
                    
                    total_execution_summary.update({
                        "failed_steps": total_execution_summary["failed_steps"] + 1,
                        "simulation_status": "FAILED",
                        "failure_reason": failure_reason
                    })
                    
                    # ✅ DB 업데이트 - 최종 시뮬레이션 실패 상태
                    await self._update_simulation_status_and_log(simulation_id, "FAILED", failure_reason)
                    return total_execution_summary

                # Pod Task 생성
                pod_tasks = {
                    asyncio.create_task(self.rosbag_executor.execute_single_pod(pod, simulation, step=step)): pod.metadata.name
                    for pod in pod_list
                }

                completed_pods = set()
                poll_interval = 1  # 1초 단위 진행상황
                
                debug_print(f"📋 Step {step.step_order} Pod Task 생성 완료: {len(pod_tasks)}개 Pod 병렬 실행 시작")

                # 3️⃣ Pod 진행상황 루프 - Redis Only 실시간 업데이트
                last_recorded_repeat = 0  # 메모리 기반 반복 횟수 관리
                
                while len(completed_pods) < len(pod_list):
                    done_tasks = [t for t in pod_tasks if t.done()]

                    # 완료된 Pod 처리
                    for task in done_tasks:
                        pod_name = pod_tasks.pop(task)
                        try:
                            result = task.result()
                            debug_print(f"✅ Pod 완료: {result.pod_name} ({len(completed_pods)}/{len(pod_list)})")
                        except asyncio.CancelledError:
                            debug_print(f"🛑 Pod CancelledError 감지: {pod_name}")
                        except Exception as e:
                            debug_print(f"💥 Pod 실행 실패: {pod_name}: {e}")

                    # 진행 중 Pod 상태 확인 및 로그
                    total_progress = 0.0
                    running_info = []
                    
                    debug_print(f"🔍 Pod 상태 체크 시작 - completed_pods: {completed_pods}")

                    status_tasks = {
                        pod.metadata.name: asyncio.create_task(self.rosbag_executor._check_pod_rosbag_status(pod))
                        for pod in pod_list
                    }

                    pod_statuses = await asyncio.gather(*status_tasks.values(), return_exceptions=True)
                    
                    current_total_loops = 0
                    max_total_loops = 0
                    
                    # 🔍 각 Pod별 상세 디버깅
                    debug_print(f"📊 === Pod별 진행률 상세 분석 (Step {step.step_order}) ===")

                    loops = []  # (current_loop, max_loops) 집계용

                    for pod_name, status in zip(status_tasks.keys(), pod_statuses):
                        debug_print(f"🔍 === Pod [{pod_name}] 상태 체크 시작 ===")
                        # 이미 완료된 Pod는 바로 100% 처리
                        if pod_name in completed_pods:
                            pod_progress = 1.0
                            running_info.append(f"{pod_name}(완료)")
                        elif isinstance(status, dict):           
                            is_playing = status.get("is_playing", False)
                            current_loop = status.get("current_loop", 0)
                            max_loops = max(status.get("max_loops") or 1, 1)
                            
                            # 집계용 리스트에 기록
                            loops.append((current_loop, max_loops)) 
                            
                            debug_print(f"  🎮 {pod_name}: is_playing = {is_playing} (기본값: False)")
                            debug_print(f"  🔄 {pod_name}: current_loop = {current_loop} (기본값: 0)")
                            debug_print(f"  🎯 {pod_name}: max_loops = {max_loops} (기본값: 1, 원본값: {status.get('max_loops')})")
                            
                            # 전체 진행률 계산을 위한 루프 수 집계
                            current_total_loops += current_loop
                            max_total_loops += max_loops
                            
                            pod_progress = min(current_loop / max_loops, 1.0)
                            
                            if current_loop >= max_loops and not is_playing:
                                # 실제로 완료된 경우
                                completed_pods.add(pod_name)
                                pod_progress = 1.0
                                running_info.append(f"{pod_name}(완료)")
                                debug_print(f"  ✅ {pod_name}: 상태체크로 완료 감지 -> completed_pods에 추가")
                            elif is_playing:
                                running_info.append(f"{pod_name}({current_loop}/{max_loops})")
                                debug_print(f"  ⏳ {pod_name}: 실행중 -> 진행률 {pod_progress:.1%}")
                            else:
                                # is_playing이 False이지만 아직 완료되지 않은 경우
                                # 무조건 1.0이 아닌 실제 진행률 사용
                                running_info.append(f"{pod_name}({current_loop}/{max_loops}-중지됨)")
                                debug_print(f"  ⏸️ {pod_name}: 중지됨 -> 진행률 {pod_progress:.1%}")
                        else:
                            pod_progress = 0.0
                            running_info.append(f"{pod_name}(상태체크실패)")
                            debug_print(f"  ❌ {pod_name}: 상태체크 실패 -> 0%")

                        total_progress += pod_progress
                        debug_print(f"  📊 {pod_name}: pod_progress={pod_progress:.2f}, 누적 total_progress={total_progress:.2f}")

                    # 그룹 반복 갱신 (Redis Only)
                    if loops:
                        group_current_loop = min(cl for cl, _ in loops)
                        group_max_loops = min(ml for _, ml in loops)
                        target_cap = step.repeat_count or group_max_loops
                        new_repeat = min(group_current_loop, target_cap)

                        if new_repeat > last_recorded_repeat:
                            last_recorded_repeat = new_repeat
                            current_step_repeat = new_repeat  # 추적 정보 업데이트
                            debug_print(f"🔁 Step {step.step_order} 반복 갱신: {new_repeat}/{target_cap} (Redis Only)")

                    group_progress = (total_progress / len(pod_list)) * 100
                    step_progress = total_progress / len(pod_list)
                    current_step_progress = step_progress  # 추적 정보 업데이트
                    
                    # ⚡ Redis Only - 실시간 진행률 업데이트
                    current_status = await redis_client.get_simulation_status(simulation_id)
                    if current_status:
                        # 전체 진행률 계산 (완료된 스텝 + 현재 스텝 진행률)
                        overall_progress = (total_execution_summary["completed_steps"] + step_progress) / len(steps)
                        
                        current_status["progress"]["overallProgress"] = overall_progress
                        current_status["message"] = f"스텝 {step.step_order}/{len(steps)} - {group_progress:.1f}% ({len(completed_pods)}/{len(pod_list)} pods)"
                        current_status["timestamps"]["lastUpdated"] = datetime.now(timezone.utc).isoformat()
                        
                        # 스텝 디테일 업데이트
                        for step_detail in current_status["stepDetails"]:
                            if step_detail["stepOrder"] == step.step_order:
                                step_detail.update({
                                    "progress": step_progress,
                                    "autonomousAgents": len(pod_list),
                                    "currentRepeat": new_repeat if loops else 0,
                                    "totalRepeats": step.repeat_count or 1
                                })
                                break
                        
                        await redis_client.set_simulation_status(simulation_id, current_status)
                    
                    debug_print(f"⏳ Step {step.step_order} 진행률: {group_progress:.1f}% ({len(completed_pods)}/{len(pod_list)}) | 진행중: {', '.join(running_info)}")

                    # stop_event 감지
                    if stop_event.is_set():
                        debug_print(f"⏹️ 중지 이벤트 감지 - 스텝 {step.step_order} 즉시 종료")
                        
                        stopped_time = datetime.now(timezone.utc)
                        
                        # ✅ DB 업데이트 - 현재 실행 중인 스텝만 정확히 중단 기록
                        await self.repository.update_simulation_step_status(
                            step_id=current_step.id,
                            status=StepStatus.STOPPED,
                            stopped_at=stopped_time 
                        )
                        # 추가 중단 정보 업데이트 (current_repeat, progress 등)
                        await self.repository.update_simulation_step_current_repeat(
                            step_id=current_step.id, 
                            current_repeat=current_step_repeat
                        )
                        debug_print(f"✅ DB 업데이트 완료 - Step {current_step.step_order} STOPPED 상태 기록 (progress: {current_step_progress:.2f}, repeat: {current_step_repeat})")
                        
                        # ⚡ Redis 중지 상태 업데이트
                        current_status = await redis_client.get_simulation_status(simulation_id)
                        if current_status:
                            current_status["status"] = "STOPPED"
                            current_status["message"] = "시뮬레이션이 사용자에 의해 중지되었습니다"
                            current_status["timestamps"].update({
                                "lastUpdated": stopped_time.isoformat(),
                                "stoppedAt": stopped_time.isoformat()
                            })
                            
                            # 스텝 디테일 업데이트
                            for step_detail in current_status["stepDetails"]:
                                if step_detail["stepOrder"] == current_step.step_order:
                                    step_detail.update({
                                        "status": "STOPPED",
                                        "stoppedAt": stopped_time.isoformat(),
                                        "currentRepeat": current_step_repeat,
                                        "progress": current_step_progress
                                    })
                                    break
                            
                            await redis_client.set_simulation_status(simulation_id, current_status)
                        
                        for t in pod_tasks.keys():
                            t.cancel()
                        await asyncio.gather(*pod_tasks.keys(), return_exceptions=True)
                        total_execution_summary["simulation_status"] = "STOPPED"
                        
                        return total_execution_summary

                    await asyncio.sleep(poll_interval)

                # 스텝 완료 처리
                step_end_time = datetime.now(timezone.utc)
                step_execution_time = (step_end_time - step_start_time).total_seconds()
                
                # 실행 결과 요약 생성
                execution_summary = self.rosbag_executor.get_execution_summary([
                    task.result() for task in done_tasks if not isinstance(task.result(), Exception)
                ])
                
                # ⚡ Redis Only - 스텝 완료 상태 업데이트
                current_status = await redis_client.get_simulation_status(simulation_id)
                if current_status:
                    current_status["progress"]["completedSteps"] += 1
                    overall_progress = current_status["progress"]["completedSteps"] / len(steps)
                    current_status["progress"]["overallProgress"] = overall_progress
                    current_status["message"] = f"스텝 {step.step_order} 완료 ({current_status['progress']['completedSteps']}/{len(steps)})"
                    current_status["timestamps"]["lastUpdated"] = step_end_time.isoformat()
                    
                    # 스텝 디테일 업데이트
                    for step_detail in current_status["stepDetails"]:
                        if step_detail["stepOrder"] == step.step_order:
                            step_detail.update({
                                "status": "COMPLETED",
                                "progress": 1.0,
                                "completedAt": step_end_time.isoformat(),
                                "currentRepeat": step.repeat_count or 1
                            })
                            break
                    
                    await redis_client.set_simulation_status(simulation_id, current_status)
                
                # 추적 정보 초기화 (스텝 완료됨)
                current_step = None
                current_step_progress = 0.0
                current_step_repeat = 0
                
                debug_print(f"✅ Step {step.step_order} 완료 (실행시간: {step_execution_time:.1f}초)")
                
                # 전체 실행 요약 업데이트
                total_execution_summary.update({
                    "completed_steps": total_execution_summary["completed_steps"] + 1,
                    "total_pods_executed": total_execution_summary["total_pods_executed"] + execution_summary['total_pods'],
                    "total_success_pods": total_execution_summary["total_success_pods"] + execution_summary['success_count']
                })
                
                total_execution_summary["step_results"].append({
                    "step_id": step.id,
                    "step_order": step.step_order,
                    "status": "success",
                    "execution_summary": execution_summary,
                    "execution_time": step_execution_time,
                    "pod_count": len(pod_list)
                })

                # 스텝 간 지연
                if i < len(steps) and step.delay_after_completion:
                    await asyncio.sleep(step.delay_after_completion)

            # 모든 스텝 성공
            completed_time = datetime.now(timezone.utc)
            
            # ⚡ Redis Only - 최종 완료 상태 업데이트
            current_status = await redis_client.get_simulation_status(simulation_id)
            if current_status:
                current_status["status"] = "COMPLETED"
                current_status["progress"]["overallProgress"] = 1.0
                current_status["message"] = f"시뮬레이션 완료 - 모든 {len(steps)}개 스텝 성공"
                current_status["timestamps"].update({
                    "lastUpdated": completed_time.isoformat(),
                    "completedAt": completed_time.isoformat()
                })
                await redis_client.set_simulation_status(simulation_id, current_status)
            
            total_execution_summary["simulation_status"] = "COMPLETED"
            
            # ✅ DB 업데이트 - 최종 완료 시에만
            await self._update_simulation_status_and_log(simulation_id, "COMPLETED", "모든 스텝 성공")
            debug_print(f"🎉 시뮬레이션 {simulation_id} 완료")
            return total_execution_summary
        except Exception as e:
            debug_print(f"❌ 시뮬레이션 {simulation_id} 실행 중 예외: {e}")
            
            error_time = datetime.now(timezone.utc)
            
            # ✅ DB 업데이트 - 현재 실행 중인 스텝만 정확히 실패 기록
            if current_step:
                await self.repository.update_simulation_step_status(
                    step_id=current_step.id,
                    status=StepStatus.FAILED,
                    failed_at=error_time
                )
                # 추가 실패 정보 업데이트 (current_repeat, progress 등)
                await self.repository.update_simulation_step_current_repeat(
                    step_id=current_step.id, 
                    current_repeat=current_step_repeat
                )
                debug_print(f"✅ DB 업데이트 완료 - Step {current_step.step_order} FAILED 상태 기록 (progress: {current_step_progress:.2f}, repeat: {current_step_repeat}, error: {str(e)})")
            
            # ⚡ Redis 실패 상태 업데이트
            current_status = await redis_client.get_simulation_status(simulation_id)
            if current_status:
                current_status["status"] = "FAILED"
                current_status["message"] = f"시뮬레이션 실행 중 오류 발생: {str(e)}"
                current_status["timestamps"].update({
                    "lastUpdated": error_time.isoformat(),
                    "failedAt": error_time.isoformat()
                })
                
                # 현재 실행 중인 스텝만 업데이트
                if current_step:
                    for step_detail in current_status["stepDetails"]:
                        if step_detail["stepOrder"] == current_step.step_order:
                            step_detail.update({
                                "status": "FAILED",
                                "failedAt": error_time.isoformat(),
                                "error": str(e),
                                "currentRepeat": current_step_repeat,
                                "progress": current_step_progress
                            })
                            break
                
                await redis_client.set_simulation_status(simulation_id, current_status)
            
            # ✅ DB 업데이트 - 최종 시뮬레이션 실패 상태
            await self._update_simulation_status_and_log(simulation_id, SimulationStatus.FAILED, str(e))
            raise
        finally:
            # Redis 정리는 TTL에 맡기고, 연결만 정리
            if redis_client.client:
                await redis_client.client.close()
            self._cleanup_simulation(simulation_id)

    async def _run_parallel_simulation_with_progress(self, simulation_id: int, stop_event: asyncio.Event):
        """
        순차 시뮬레이션 패턴 적용한 병렬 시뮬레이션 실행
        - 메모리 기반 current_repeat 추적 (순차 시뮬레이션과 동일 패턴)
        - Redis + DB 동시 업데이트 (Redis 1차 조회 대응)
        - 단순하고 효율적인 진행률 관리
        """
        debug_print("🚀 병렬 시뮬레이션 실행 시작", simulation_id=simulation_id)
        
        redis_client = RedisSimulationClient()
        await redis_client.connect()
        
        # 📊 각 그룹별 메모리 기반 진행률 추적 (순차 시뮬레이션 패턴)
        group_progress_tracker = {}  # {group_id: {"last_recorded_repeat": int, "current_progress": float}}
        
        try:
            # 1️⃣ 시뮬레이션 조회 및 초기화
            simulation = await self.find_simulation_by_id(simulation_id, "background parallel run")
            groups = await self.repository.find_simulation_groups(simulation_id)
            debug_print("✅ 시뮬레이션 조회 완료", simulation_id=simulation.id, group_count=len(groups))

            # 그룹별 추적 정보 초기화
            for group in groups:
                group_progress_tracker[group.id] = {
                    "last_recorded_repeat": 0,
                    "current_progress": 0.0,
                    "start_time": datetime.now(timezone.utc)
                }

            # ✅ DB 업데이트 - 시뮬레이션 시작 시에만
            await self._update_simulation_status_and_log(simulation_id, "RUNNING", "병렬 시뮬레이션 실행 시작")

            # Redis 초기 상태 설정
            current_time = datetime.now(timezone.utc)
            initial_status = {
                "status": "RUNNING",
                "progress": {
                    "overallProgress": 0.0,
                    "completedGroups": 0,
                    "runningGroups": len(groups),
                    "totalGroups": len(groups)
                },
                "timestamps": {
                    "createdAt": simulation.created_at.isoformat() if simulation.created_at else None,
                    "lastUpdated": current_time.isoformat(),
                    "startedAt": current_time.isoformat(),
                    "completedAt": None,
                    "failedAt": None,
                    "stoppedAt": None
                },
                "message": f"병렬 시뮬레이션 시작 - 총 {len(groups)}개 그룹",
                "groupDetails": [
                    {
                        "groupId": group.id,
                        "status": "RUNNING",
                        "progress": 0.0,
                        "startedAt": current_time.isoformat(),
                        "completedAt": None,
                        "failedAt": None,
                        "stoppedAt": None,
                        "autonomousAgents": group.autonomous_agent_count,
                        "currentRepeat": 0,
                        "totalRepeats": group.repeat_count or 1,
                        "error": None
                    } for group in groups
                ]
            }
            await redis_client.set_simulation_status(simulation_id, initial_status)

            # 2️⃣ 모든 그룹 병렬 실행
            group_tasks = []
            
            # 각 그룹을 비동기 태스크로 생성
            for group in groups:
                task = asyncio.create_task(
                    self._execute_single_group_with_memory_tracking(
                        simulation, group, redis_client, simulation_id, group_progress_tracker
                    )
                )
                task.group_id = group.id # 태스크에 그룹 ID 할당
                group_tasks.append(task)

            debug_print("🎯 모든 그룹 병렬 실행 시작", simulation_id=simulation_id, total_groups=len(groups))

            total_summary = {
                "total_groups": len(groups),
                "completed_groups": 0,
                "failed_groups": 0,
                "total_pods_executed": 0,
                "total_success_pods": 0,
                "total_failed_pods": 0,
                "group_results": [],
                "simulation_status": "RUNNING",
                "failure_reason": None
            }

            # 3️⃣ 실행 중 진행상황 감시 + 즉시 취소 처리
            poll_interval = 1.0
            while group_tasks:
                # 루프 시작 시 상태 체크
                debug_print(f"🔍 루프 시작: {len(group_tasks)}개 태스크 대기")
                for i, t in enumerate(group_tasks):
                    gid = getattr(t, 'group_id', None)
                    debug_print(f"  태스크 {i}: 그룹{gid} done={t.done()}")
                
                try:
                    done, pending = await asyncio.wait(
                        group_tasks, 
                        timeout=poll_interval, 
                        return_when=asyncio.FIRST_COMPLETED
                    )

                    debug_print(f"📊 wait 결과: done={len(done)}, pending={len(pending)}")
        
                    # 완료된 그룹 처리
                    for t in done:
                        group_id = getattr(t, 'group_id', None)
                        debug_print(f"🎯 그룹 {group_id} 결과 처리 시작")
                        try:
                            group_result = t.result()
                            debug_print(f"✅ 그룹 {group_id} 정상 완료: {group_result}")
                        except asyncio.CancelledError:
                            debug_print("🛑 그룹 CancelledError 감지", group_id=group_id)
                            group_result = {
                                "group_id": group_id,
                                "status": "stopped",
                                "total_pod_count": 0,
                                "success_pod_count": 0,
                                "failed_pod_count": 0,
                                "failure_reason": "사용자 요청으로 중지"
                            }
                        except Exception as e:
                            debug_print(f"그룹 {group_id} 실행 실패", error=str(e))
                            traceback.print_exception(type(e), e, e.__traceback__)

                        debug_print(f"그룹 {group_id} 최종 결과: {group_result}")
                        total_summary["group_results"].append(group_result)
                        
                        # 태스크 제거
                        if t in group_tasks:
                            group_tasks.remove(t)

                        # 그룹 완료/실패 처리 - ✅ DB + Redis 동시 업데이트
                        if group_result["status"] == "success":
                            debug_print(f"그룹 {group.id} 완료 처리됨")
                            total_summary["completed_groups"] += 1
                            total_summary["total_success_pods"] += group_result["success_pod_count"]
                            
                            # 📊 최종 current_repeat를 DB + Redis에 동시 기록
                            final_repeat = group_progress_tracker.get(group_id, {}).get("last_recorded_repeat", 0)
                            await self._update_group_final_status_with_redis(
                                group_id, GroupStatus.COMPLETED, final_repeat, 
                                redis_client, simulation_id,
                                group_result["total_pod_count"]
                            )
                            
                        elif group_result["status"] == "failed":
                            total_summary["failed_groups"] += 1
                            total_summary["total_failed_pods"] += group_result.get("failed_pod_count", 0)
                            if not total_summary["failure_reason"]:
                                total_summary["failure_reason"] = group_result.get("failure_reason")
                            
                            # 📊 최종 current_repeat를 DB + Redis에 동시 기록
                            final_repeat = group_progress_tracker.get(group_id, {}).get("last_recorded_repeat", 0)
                            await self._update_group_final_status_with_redis(
                                group_id, GroupStatus.FAILED, final_repeat,
                                redis_client, simulation_id,
                                group_result["total_pod_count"],
                                failure_reason=group_result.get("failure_reason")
                            )
                            
                        elif group_result["status"] == "stopped":
                            debug_print("🛑 그룹 취소 감지", group_id=group_result["group_id"])
                            
                            # 📊 최종 current_repeat를 DB + Redis에 동시 기록
                            final_repeat = group_progress_tracker.get(group_id, {}).get("last_recorded_repeat", 0)
                            await self._update_group_final_status_with_redis(
                                group_id, GroupStatus.STOPPED, final_repeat,
                                redis_client, simulation_id,
                                group_result["total_pod_count"]
                            )

                        total_summary["total_pods_executed"] += group_result["total_pod_count"]

                    # ⚡ Redis 전체 진행률 업데이트 (순차 시뮬레이션 패턴)
                    await self._update_overall_progress_redis(redis_client, simulation_id, total_summary, len(groups), group_tasks)

                    # stop_event 감지 시 남은 모든 그룹 취소
                    if stop_event.is_set():
                        debug_print("🛑 시뮬레이션 중지 감지, 남은 그룹 취소 시작", pending_groups=len(pending))
                        
                        stopped_time = datetime.now(timezone.utc)
                        
                        # ✅ DB + Redis 동시 업데이트 - 현재 실행 중인 그룹들의 최종 상태만 기록
                        for task in pending:
                            group_id = getattr(task, 'group_id', None)
                            if group_id and group_id in group_progress_tracker:
                                final_repeat = group_progress_tracker[group_id]["last_recorded_repeat"]
                                await self._update_group_final_status_with_redis(
                                    group_id, GroupStatus.STOPPED, final_repeat,
                                    redis_client, simulation_id, 0  # Pod 수는 알 수 없으므로 0
                                )
                        
                        # 그룹 태스크 취소
                        for t in pending:
                            t.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)

                        # ⚡ Redis 전체 시뮬레이션 중지 상태 업데이트
                        await self._update_simulation_stopped_redis(redis_client, simulation_id, stopped_time, group_progress_tracker)

                        total_summary["simulation_status"] = "STOPPED"
                        
                        break
                except Exception as loop_error:
                    debug_print(f"❌ 루프 실행 중 치명적 오류: {loop_error}")
                    traceback.print_exc()
                    break
                
                debug_print(f"🔄 루프 종료, 남은 태스크: {len(group_tasks)}개")

            # 4️⃣ 최종 상태 결정
            debug_print("최종 상태 결정")
            if total_summary["simulation_status"] != "STOPPED":
                completed_time = datetime.now(timezone.utc)
                
                if total_summary["failed_groups"] > 0:
                    total_summary["simulation_status"] = "FAILED"
                    reason = total_summary["failure_reason"] or f"{total_summary['failed_groups']}개 그룹 실패"
                    
                    # ⚡ Redis 최종 실패 상태 업데이트
                    current_status = await redis_client.get_simulation_status(simulation_id)
                    if current_status:
                        current_status["status"] = "FAILED"
                        current_status["message"] = reason
                        current_status["timestamps"].update({
                            "lastUpdated": completed_time.isoformat(),
                            "failedAt": completed_time.isoformat()
                        })
                        await redis_client.set_simulation_status(simulation_id, current_status)
                    
                    await self._update_simulation_status_and_log(simulation_id, "FAILED", reason)
                else:
                    total_summary["simulation_status"] = "COMPLETED"
                    
                    # ⚡ Redis 최종 완료 상태 업데이트
                    current_status = await redis_client.get_simulation_status(simulation_id)
                    if current_status:
                        current_status["status"] = "COMPLETED"
                        current_status["progress"]["overallProgress"] = 1.0
                        current_status["message"] = f"병렬 시뮬레이션 완료 - 모든 {len(groups)}개 그룹 성공"
                        current_status["timestamps"].update({
                            "lastUpdated": completed_time.isoformat(),
                            "completedAt": completed_time.isoformat()
                        })
                        await redis_client.set_simulation_status(simulation_id, current_status)
                    
                    await self._update_simulation_status_and_log(simulation_id, "COMPLETED", "모든 그룹 완료")

            debug_print("🎉 병렬 시뮬레이션 실행 완료", simulation_id=simulation_id)
            return total_summary
            
        except Exception as e:
            traceback.print_exception()
            # 📊 예외 시에도 DB + Redis 동시 기록
            await self._handle_failed_groups_with_redis(group_progress_tracker, redis_client, simulation_id, str(e))
            raise
        finally:
            if redis_client.client:
                await redis_client.client.close()
            self._cleanup_simulation(simulation_id)

    async def _execute_single_group_with_memory_tracking(self, simulation, group, redis_client, simulation_id, group_progress_tracker):
        debug_print("🔸 그룹 실행 시작", group_id=group.id, simulation_id=simulation.id)
        start_time = datetime.now(timezone.utc)

        try:
            # 1️⃣ 그룹 Pod 조회
            pod_list = self.pod_service.get_pods_by_filter(
                namespace=simulation.namespace,
                filter_params=GroupIdFilter(group_id=group.id)
            )
            if not pod_list:
                return {
                    "group_id": group.id,
                    "status": "failed",
                    "execution_time": (datetime.now(timezone.utc) - start_time).total_seconds(),
                    "total_pod_count": 0,
                    "success_pod_count": 0,
                    "failed_pod_count": 0,
                    "failure_reason": f"그룹 {group.id}에서 Pod를 찾을 수 없음"
                }

            total_pod_count = len(pod_list)
            debug_print(f"📋 그룹 {group.id} Pod 목록", pod_names=[pod.metadata.name for pod in pod_list], total_count=total_pod_count)

            # 2️⃣ Pod Task 실행 시작 (rosbag 시작 요청)
            pod_tasks = {asyncio.create_task(
                self.rosbag_executor.execute_single_pod(pod, simulation, group)
            ): pod.metadata.name for pod in pod_list}

            completed_pods = set()
            failed_pods = {}
            poll_interval = 1  # 1초 단위 진행상황 확인
            
            # ✅ 메모리 기반 반복 횟수 관리
            last_recorded_repeat = group_progress_tracker[group.id]["last_recorded_repeat"]

            # 3️⃣ Pod 진행상황 루프 - Redis 실시간 업데이트 (순차 시뮬레이션 패턴 적용)
            while len(completed_pods) < total_pod_count:
                # 완료된 Pod Task 처리
                done_tasks = [t for t in pod_tasks if t.done()]
                
                for task in done_tasks:
                    pod_name = pod_tasks.pop(task)
                    try:
                        result = task.result()
                        completed_pods.add(pod_name)
                        debug_print(f"✅ Pod 완료: {pod_name} ({len(completed_pods)}/{total_pod_count})")
                    except (asyncio.CancelledError, Exception) as e:
                        debug_print(f"💥 Pod 실행 실패: {pod_name}: {e}")
                        
                        # ✅ 실패한 Pod의 current_loop 조회
                        try:
                            failed_pod = next(pod for pod in pod_list if pod.metadata.name == pod_name)
                            failure_status = await self.rosbag_executor._check_pod_rosbag_status(failed_pod)
                            
                            if isinstance(failure_status, dict):
                                current_loop = failure_status.get("current_loop", 0)
                                max_loops = max(failure_status.get("max_loops") or 1, 1)
                                failure_progress = min(current_loop / max_loops, 1.0)
                            else:
                                failure_progress = 0.0
                                
                            failed_pods[pod_name] = failure_progress
                            debug_print(f"💥 Pod {pod_name} 실패 시점 진행률: {failure_progress:.1%} ({current_loop}/{max_loops})")
                            
                        except Exception as status_error:
                            debug_print(f"⚠️ 실패한 Pod {pod_name}의 상태 조회 실패: {status_error}")
                            failed_pods[pod_name] = 0.0
                        
                        # ✅ 즉시 시뮬레이션 종료
                        debug_print(f"🛑 Pod 실패로 인한 그룹 {group.id} 즉시 종료")
                        break
                    
                # ✅ 실패 감지 시 즉시 루프 종료
                if failed_pods:
                    debug_print(f"🛑 실패 감지 - 그룹 {group.id} 실행 중단")
                    break

                # ✅ 진행 중 Pod 상태 확인 및 로그 (asyncio.gather 패턴)
                total_progress = 0.0
                running_info = []
                
                debug_print(f"🔍 Pod 상태 체크 시작 - completed_pods: {completed_pods}")

                # 모든 Pod 상태를 동시에 확인
                status_tasks = {
                    pod.metadata.name: asyncio.create_task(self.rosbag_executor._check_pod_rosbag_status(pod))
                    for pod in pod_list
                }

                pod_statuses = await asyncio.gather(*status_tasks.values(), return_exceptions=True)
                
                # 🔍 각 Pod별 상세 디버깅
                debug_print(f"📊 === 그룹 {group.id} Pod별 진행률 상세 분석 ===")

                loops = []  # (current_loop, max_loops) 집계용

                for pod_name, status in zip(status_tasks.keys(), pod_statuses):
                    debug_print(f"🔍 === Pod [{pod_name}] 상태 체크 시작 ===")
                    
                    # 이미 완료된 Pod는 바로 100% 처리
                    if pod_name in completed_pods:
                        pod_progress = 1.0
                        running_info.append(f"{pod_name}(완료)")
                        debug_print(f"  ✅ {pod_name}: 이미 완료됨 -> 100%")
                    elif pod_name in failed_pods:
                        pod_progress = failed_pods[pod_name]  # 실패 시점까지의 진행률
                        running_info.append(f"{pod_name}(실패-{pod_progress:.1%})")
                        debug_print(f"  💥 {pod_name}: 실패 (시점 진행률: {pod_progress:.1%})")
                    elif isinstance(status, dict):           
                        is_playing = status.get("is_playing", False)
                        current_loop = status.get("current_loop", 0)
                        max_loops = max(status.get("max_loops") or 1, 1)
                        
                        # 집계용 리스트에 기록
                        loops.append((current_loop, max_loops)) 
                        
                        debug_print(f"  🎮 {pod_name}: is_playing = {is_playing}")
                        debug_print(f"  🔄 {pod_name}: current_loop = {current_loop}")
                        debug_print(f"  🎯 {pod_name}: max_loops = {max_loops}")
                        
                        pod_progress = min(current_loop / max_loops, 1.0)
                        
                        if current_loop >= max_loops and not is_playing:
                            # 실제로 완료된 경우
                            completed_pods.add(pod_name)
                            pod_progress = 1.0
                            running_info.append(f"{pod_name}(완료)")
                            debug_print(f"  ✅ {pod_name}: 상태체크로 완료 감지 -> completed_pods에 추가")
                        elif is_playing:
                            running_info.append(f"{pod_name}({current_loop}/{max_loops})")
                            debug_print(f"  ⏳ {pod_name}: 실행중 -> 진행률 {pod_progress:.1%}")
                        else:
                            # is_playing이 False이지만 아직 완료되지 않은 경우
                            running_info.append(f"{pod_name}({current_loop}/{max_loops}-중지됨)")
                            debug_print(f"  ⏸️ {pod_name}: 중지됨 -> 진행률 {pod_progress:.1%}")
                    else:
                        pod_progress = 0.0
                        running_info.append(f"{pod_name}(상태체크실패)")
                        debug_print(f"  ❌ {pod_name}: 상태체크 실패 -> 0%")

                    total_progress += pod_progress
                    debug_print(f"  📊 {pod_name}: pod_progress={pod_progress:.2f}, 누적 total_progress={total_progress:.2f}")

                # ✅ 그룹 반복 갱신 (Redis + 메모리)
                if loops:
                    group_current_loop = min(cl for cl, _ in loops)  # 가장 느린 Pod 기준
                    new_repeat = group_current_loop

                    if new_repeat > last_recorded_repeat:
                        last_recorded_repeat = new_repeat
                        group_progress_tracker[group.id]["last_recorded_repeat"] = new_repeat
                        debug_print(f"🔁 그룹 {group.id} 반복 갱신: {new_repeat} (Redis + 메모리)")

                # ✅ 그룹 진행률 계산
                debug_print(f"그룹 ID: {group.id}, total_progress: {total_progress}, total_pod_count: {total_pod_count}")
                group_progress = total_progress / total_pod_count
                group_progress_tracker[group.id]["current_progress"] = group_progress
                
                # ⚡ Redis 실시간 업데이트 - 그룹별 상태
                await self._update_group_status_in_redis(
                    redis_client, simulation_id, group.id, "RUNNING",
                    group_progress,
                    current_repeat=last_recorded_repeat
                )
                
                debug_print(f"⏳ 그룹 {group.id} 진행률: {group_progress:.1%} ({len(completed_pods)}/{total_pod_count}) | 진행중: {', '.join(running_info)}")

                # ✅ 실패한 Pod가 너무 많으면 그룹 실패 처리
                if len(failed_pods) >= total_pod_count:
                    debug_print(f"💥 그룹 {group.id}: 모든 Pod 실패")
                    break

                await asyncio.sleep(poll_interval)

            # 4️⃣ 그룹 완료 처리 - ✅ 최종 반복횟수 확정
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            success_count = len(completed_pods)
            failed_count = len(failed_pods)
            status = "success" if failed_count == 0 else "failed"

            # ✅ 최종 반복횟수 확정 및 업데이트
            final_repeat_count = group_progress_tracker[group.id]["last_recorded_repeat"]
            debug_print(f"✅ 그룹 {group.id} 최종 반복횟수 확정: {final_repeat_count}")

            debug_print(f"{group.id} 그룹 완료 처리됨")
            return {
                "group_id": group.id,
                "status": status,
                "execution_time": execution_time,
                "total_pod_count": total_pod_count,
                "success_pod_count": success_count,
                "failed_pod_count": failed_count,
                "failure_reason": f"{failed_count}개 Pod 실패" if failed_count > 0 else None
            }

        except asyncio.CancelledError:
            # ✅ 취소 시에도 현재까지의 최종 반복횟수 업데이트
            final_repeat_count = group_progress_tracker[group.id]["last_recorded_repeat"]
            debug_print(f"🛑 그룹 {group.id} 취소 - 최종 반복횟수: {final_repeat_count}")
            
            # 남은 Pod Task 취소
            for t in pod_tasks.keys():
                t.cancel()
            await asyncio.gather(*pod_tasks.keys(), return_exceptions=True)
            return {
                "group_id": group.id,
                "status": "stopped",
                "execution_time": (datetime.now(timezone.utc) - start_time).total_seconds(),
                "total_pod_count": total_pod_count if 'total_pod_count' in locals() else 0,
                "success_pod_count": len(completed_pods) if 'completed_pods' in locals() else 0,
                "failed_pod_count": len(failed_pods) if 'failed_pods' in locals() else 0,
                "failure_reason": "사용자 요청으로 중지"
            }

        except Exception as e:
            # ✅ 예외 시에도 현재까지의 최종 반복횟수 업데이트
            final_repeat_count = group_progress_tracker[group.id]["last_recorded_repeat"]
            debug_print(f"💥 그룹 {group.id} 실패 - 최종 반복횟수: {final_repeat_count}")
            
            # 남은 Pod Task 취소
            for t in pod_tasks.keys():
                t.cancel()
            await asyncio.gather(*pod_tasks.keys(), return_exceptions=True)
            return {
                "group_id": group.id,
                "status": "failed",
                "execution_time": (datetime.now(timezone.utc) - start_time).total_seconds(),
                "total_pod_count": total_pod_count if 'total_pod_count' in locals() else 0,
                "success_pod_count": len(completed_pods) if 'completed_pods' in locals() else 0,
                "failed_pod_count": len(failed_pods) if 'failed_pods' in locals() else 0,
                "failure_reason": str(e)
            }


    # 📊 Redis + DB 동시 업데이트 메서드들
    async def _update_group_final_status_with_redis(self, group_id: int, status: GroupStatus, final_repeat: int,
                                            redis_client, simulation_id: int, pod_count: int = 0,
                                            failure_reason: str = None):
        """그룹 최종 상태를 DB + Redis에 동시 기록"""
        timestamp = datetime.now(timezone.utc)
        
        try:
            debug_print(f"🔄 DB 업데이트 시도 - Group {group_id}: {status.value}")
            
            # ✅ DB 업데이트
            if status == GroupStatus.COMPLETED:
                await self.repository.update_simulation_group_status(
                    group_id=group_id,
                    status=status,
                    completed_at=timestamp
                )
            elif status == GroupStatus.FAILED:
                await self.repository.update_simulation_group_status(
                    group_id=group_id,
                    status=status,
                    failed_at=timestamp
                )
            elif status == GroupStatus.STOPPED:
                await self.repository.update_simulation_group_status(
                    group_id=group_id,
                    status=status,
                    stopped_at=timestamp
                )
            
            # 📊 최종 current_repeat 업데이트
            await self.repository.update_simulation_group_current_repeat(
                group_id=group_id,
                current_repeat=final_repeat
            )
            
            debug_print(f"✅ DB 업데이트 완료 - Group {group_id}: {status.value}")
            
        except Exception as db_error:
            debug_print(f"❌ DB 업데이트 실패 - Group {group_id}: {db_error}")
            traceback.print_exc()  # 전체 예외 스택 출력
            # DB 실패해도 Redis는 시도
            
            # 추가 디버깅: 트랜잭션 상태 확인
            try:
                in_tx = self.repository.session.in_transaction()
                debug_print(f"ℹ️ 트랜잭션 상태 - in_transaction: {in_tx}")
            except Exception as tx_error:
                debug_print(f"⚠️ 트랜잭션 상태 확인 실패: {tx_error}")
        
        try:
            # ⚡ Redis 그룹 상태 동시 업데이트
            await self._update_group_status_in_redis_final(
                redis_client, simulation_id, group_id, status.value.upper(),
                final_repeat, pod_count, timestamp, failure_reason
            )
            debug_print(f"✅ Redis 업데이트 완료 - Group {group_id}")
            
        except Exception as redis_error:
            debug_print(f"❌ Redis 업데이트 실패 - Group {group_id}: {redis_error}")
        
        debug_print(f"✅ 상태 업데이트 시도 완료 - Group {group_id}: {status.value}, current_repeat: {final_repeat}")

    async def _update_group_status_in_redis_final(self, redis_client, simulation_id: int, group_id: int,
                                                final_status: str, final_repeat: int, pod_count: int,
                                                timestamp: datetime, error_message: str = None):
        """Redis에서 특정 그룹의 최종 상태 업데이트"""
        current_status = await redis_client.get_simulation_status(simulation_id)
        if not current_status:
            return
        
        # groupDetails에서 해당 그룹 찾아서 업데이트
        for group_detail in current_status.get("groupDetails", []):
            if group_detail.get("groupId") == group_id:
                group_detail.update({
                    "status": final_status,
                    "progress": 100.0 if final_status == "COMPLETED" else group_detail.get("progress", 0.0),
                    "autonomousAgents": pod_count,
                    "currentRepeat": final_repeat,
                    "error": error_message
                })
                
                # 상태별 타임스탬프 설정
                timestamp_iso = timestamp.isoformat()
                if final_status == "COMPLETED":
                    group_detail["completedAt"] = timestamp_iso
                elif final_status == "FAILED":
                    group_detail["failedAt"] = timestamp_iso
                elif final_status == "STOPPED":
                    group_detail["stoppedAt"] = timestamp_iso
                
                debug_print(f"⚡ Redis 그룹 {group_id} 최종 상태 업데이트: {final_status}")
                break
        
        # 전체 상태 timestamps 업데이트
        current_status["timestamps"]["lastUpdated"] = timestamp.isoformat()
        
        await redis_client.set_simulation_status(simulation_id, current_status)


    async def _handle_cancelled_groups_with_redis(self, group_progress_tracker, redis_client, simulation_id):
        """취소된 그룹들의 최종 상태를 DB + Redis에 동시 기록"""
        cancelled_time = datetime.now(timezone.utc)
        
        for group_id, progress_info in group_progress_tracker.items():
            final_repeat = progress_info["last_recorded_repeat"]
            await self._update_group_final_status_with_redis(
                group_id, GroupStatus.STOPPED, final_repeat,
                redis_client, simulation_id, 0
            )
        
        # ⚡ Redis 전체 시뮬레이션 취소 상태 업데이트
        current_status = await redis_client.get_simulation_status(simulation_id)
        if current_status:
            current_status["status"] = "STOPPED"
            current_status["message"] = "병렬 시뮬레이션 태스크가 취소되었습니다"
            current_status["timestamps"].update({
                "lastUpdated": cancelled_time.isoformat(),
                "stoppedAt": cancelled_time.isoformat()
            })
            await redis_client.set_simulation_status(simulation_id, current_status)
        


    async def _handle_failed_groups_with_redis(self, group_progress_tracker, redis_client, simulation_id, error_msg):
        debug_print("_handle_failed_groups_with_redis 호출됨")
        """실패한 그룹들의 최종 상태를 DB + Redis에 동시 기록"""
        error_time = datetime.now(timezone.utc)
        
        for group_id, progress_info in group_progress_tracker.items():
            final_repeat = progress_info["last_recorded_repeat"]
            await self._update_group_final_status_with_redis(
                group_id, GroupStatus.FAILED, final_repeat,
                redis_client, simulation_id, 0, failure_reason=error_msg
            )
        
        # ⚡ Redis 전체 시뮬레이션 실패 상태 업데이트
        current_status = await redis_client.get_simulation_status(simulation_id)
        if current_status:
            current_status["status"] = "FAILED"
            current_status["message"] = f"병렬 시뮬레이션 실행 중 오류 발생: {error_msg}"
            current_status["timestamps"].update({
                "lastUpdated": error_time.isoformat(),
                "failedAt": error_time.isoformat()
            })
            await redis_client.set_simulation_status(simulation_id, current_status)
        
        await self._update_simulation_status_and_log(simulation_id, "FAILED", error_msg)

    async def _update_simulation_stopped_redis(self, redis_client, simulation_id, stopped_time, group_progress_tracker):
        """Redis 전체 시뮬레이션 중지 상태 업데이트"""
        current_status = await redis_client.get_simulation_status(simulation_id)
        if current_status:
            current_status["status"] = "STOPPED"
            current_status["message"] = "시뮬레이션이 사용자에 의해 중지되었습니다"
            current_status["timestamps"].update({
                "lastUpdated": stopped_time.isoformat(),
                "stoppedAt": stopped_time.isoformat()
            })
            
            # 실행 중인 그룹들을 STOPPED로 업데이트 (이미 완료된 것들은 그대로 유지)
            for group_detail in current_status["groupDetails"]:
                group_id = group_detail["groupId"]
                if group_id in group_progress_tracker and group_detail["status"] == "RUNNING":
                    group_detail.update({
                        "status": "STOPPED",
                        "stoppedAt": stopped_time.isoformat(),
                        "progress": group_progress_tracker[group_id]["current_progress"],
                        "currentRepeat": group_progress_tracker[group_id]["last_recorded_repeat"]
                    })
            
            await redis_client.set_simulation_status(simulation_id, current_status)


    # 📊 헬퍼 메서드들
    async def _update_group_final_status(self, group_id: int, status: GroupStatus, final_repeat: int, 
                                    failure_reason: str = None):
        """그룹 최종 상태를 DB에 기록 (current_repeat 포함)"""
        timestamp = datetime.now(timezone.utc)
        
        try:
            if status == GroupStatus.COMPLETED:
                await self.repository.update_simulation_group_status(
                    group_id=group_id,
                    status=status,
                    completed_at=timestamp
                )
            elif status == GroupStatus.FAILED:
                await self.repository.update_simulation_group_status(
                    group_id=group_id,
                    status=status,
                    failed_at=timestamp
                )
            elif status == GroupStatus.STOPPED:
                await self.repository.update_simulation_group_status(
                    group_id=group_id,
                    status=status,
                    stopped_at=timestamp
                )
            
            # 📊 최종 current_repeat 업데이트
            await self.repository.update_simulation_group_current_repeat(
                group_id=group_id,
                current_repeat=final_repeat
            )
        
            debug_print(f"✅ DB 최종 상태 기록 완료 - Group {group_id}: {status.value}, current_repeat: {final_repeat}")
        except Exception as db_error:
            # DB 실패 시 상세 로그
            debug_print(f"❌ DB 업데이트 실패 - Group {group_id}: {db_error}")
            traceback.print_exc()  # 전체 예외 스택 출력

    async def _update_overall_progress_redis(self, redis_client, simulation_id, total_summary, 
                                        total_groups, remaining_tasks):
        """전체 진행률 Redis 업데이트 (순차 시뮬레이션 패턴)"""
        current_status = await redis_client.get_simulation_status(simulation_id)
        if current_status:
            running_count = len(remaining_tasks)
            completed_count = total_summary["completed_groups"]
            failed_count = total_summary["failed_groups"]
            
            # ✅ 각 그룹의 실제 진행률을 고려한 가중 평균 계산
            overall_progress = await self._calculate_weighted_overall_progress_from_db(
                redis_client, simulation_id
            )
            
            current_status["progress"].update({
                "overallProgress": overall_progress,
                "completedGroups": completed_count,
                "runningGroups": running_count
            })
            current_status["message"] = f"병렬 실행 중 - 완료: {completed_count}, 실행중: {running_count}, 실패: {failed_count}"
            current_status["timestamps"]["lastUpdated"] = datetime.now(timezone.utc).isoformat()
            
            await redis_client.set_simulation_status(simulation_id, current_status)

    async def _calculate_weighted_overall_progress_from_db(self, redis_client: RedisSimulationClient, simulation_id):
        """DB에서 그룹 정보를 조회하여 가중 평균 계산"""
        try:
            # DB에서 그룹 목록 조회
            groups = await self.repository.find_simulation_groups(simulation_id)
            
            if not groups:
                return 0.0
            
            total_weighted_progress = 0.0
            total_agents = 0
            
            sim_status = await redis_client.get_simulation_status(simulation_id)
            for group in groups:
                # Redis에서 현재 그룹 진행률 조회
                debug_print(f"{group.id} 그룹에 대해 Redis 에서 현재 그룹 진행률 조회")
                group_status = next(
                    (g for g in sim_status["groupDetails"] if g["groupId"] == group.id),
                    None
                )
                debug_print(f"그룹 진행률 정보: {group_status}")
                
                if group_status:
                    group_progress = group_status.get("progress", 0.0)
                    if isinstance(group_progress, (int, float)) and group_progress > 1:
                        group_progress = group_progress / 100.0
                else:
                    # Redis에 없으면 DB의 calculate_progress 사용
                    group_progress = group.calculate_progress
                
                # DB의 에이전트 수 사용 (더 신뢰성 있음)
                autonomous_agents = group.autonomous_agent_count
                
                weighted_progress = group_progress * autonomous_agents
                total_weighted_progress += weighted_progress
                total_agents += autonomous_agents
                
                debug_print(f"그룹 {group.id}: progress={group_progress:.2%}, "
                        f"agents={autonomous_agents}, weighted={weighted_progress:.2f}")
            
            if total_agents > 0:
                overall_progress = total_weighted_progress / total_agents
                debug_print(f"DB 기반 전체 진행률: {overall_progress:.1f}%")
                return overall_progress
            else:
                return 0.0
        except Exception as e:
            debug_print(f"DB 기반 전체 진행률 계산 오류: {e}")
            return 0.0

    async def _handle_cancelled_groups(self, group_progress_tracker, redis_client, simulation_id):
        """취소된 그룹들의 최종 상태를 DB에 기록"""
        cancelled_time = datetime.now(timezone.utc)
        
        for group_id, progress_info in group_progress_tracker.items():
            final_repeat = progress_info["last_recorded_repeat"]
            await self._update_group_final_status(group_id, GroupStatus.STOPPED, final_repeat)
        

    async def _handle_failed_groups(self, group_progress_tracker, redis_client, simulation_id, error_msg):
        """실패한 그룹들의 최종 상태를 DB에 기록"""
        error_time = datetime.now(timezone.utc)
        
        for group_id, progress_info in group_progress_tracker.items():
            final_repeat = progress_info["last_recorded_repeat"]
            await self._update_group_final_status(group_id, GroupStatus.FAILED, final_repeat, 
                                                failure_reason=error_msg)
        
        await self._update_simulation_status_and_log(simulation_id, "FAILED", error_msg)

    async def _update_group_status_in_redis(self, redis_client, simulation_id, group_id, group_status, group_progress, current_repeat=None, error=None):
        """
        Redis에서 특정 그룹의 상태 업데이트
        """
        try:
            current_status = await redis_client.get_simulation_status(simulation_id)
            if current_status:
                current_time = datetime.now(timezone.utc).isoformat()
                current_status["timestamps"]["lastUpdated"] = current_time
                
                # 그룹 디테일 업데이트
                for group_detail in current_status["groupDetails"]:
                    if group_detail["groupId"] == group_id:
                        group_detail.update({
                            "status": group_status,
                            "progress": round(group_progress * 100, 1),  # 0.65 -> 65.0
                        })
                        
                        if current_repeat is not None:
                            group_detail["currentRepeat"] = current_repeat
                        if error:
                            group_detail["error"] = error
                            
                        # 상태별 타임스탬프 업데이트
                        if status == "COMPLETED":
                            group_detail["completedAt"] = current_time
                        elif status == "FAILED":
                            group_detail["failedAt"] = current_time
                        elif status == "STOPPED":
                            group_detail["stoppedAt"] = current_time
                        
                        break
                
                await redis_client.set_simulation_status(simulation_id, current_status)
        except Exception as e:
            debug_print(f"❌ Redis 그룹 상태 업데이트 실패: {e}")
    
    
    async def _update_simulation_status_and_log(self, simulation_id: int, status: str, reason: str):
        """시뮬레이션 상태 업데이트 및 로깅"""
        try:
            await self.repository.update_simulation_status(simulation_id, status)
            print(f"✅ 시뮬레이션 상태 업데이트 완료: {status}")
            if reason:
                print(f"   사유: {reason}")
        except Exception as update_error:
            print(f"⚠️  시뮬레이션 상태 업데이트 실패: {str(update_error)}")
            print(f"   시도한 상태: {status}")
            print(f"   사유: {reason}")

    async def stop_simulation(self, simulation_id: int):
        instances = await self.get_simulation_instances(simulation_id)
        for instance in instances:
            await self.pod_service.check_pod_status(instance)
            pod_ip = await self.pod_service.get_pod_ip(instance)
            await RosService.send_post_request(pod_ip, "/rosbag/stop")

        return SimulationControlResponse(simulation_id=simulation_id).model_dump()

    async def get_simulation_instances(self, simulation_id: int):
        simulation = await self.find_simulation_by_id(
            simulation_id, "control simulation"
        )
        query = (
            select(Instance)
            .options(joinedload(Instance.template))
            .where(Instance.simulation_id == simulation.id)
        )
        result = await self.session.execute(query)
        instances = result.scalars().all()
        return list(instances)

    async def delete_simulation(self, simulation_id: int):
        api = API.DELETE_SIMULATION.value
        simulation = await self.find_simulation_by_id(simulation_id, api)

        # 시뮬레이션이 실행 중인지 확인
        current_status = await self.get_simulation_status(simulation)
        if current_status == SimulationStatus.ACTIVE.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{api}: 실행 중인 시뮬레이션은 삭제할 수 없습니다.",
            )

        # 시뮬레이션이 존재해야 아래 코드 실행됨
        statement = select(exists().where(Instance.simulation_id == simulation_id))
        is_existed = await self.session.scalar(statement)

        if is_existed is False:
            await self.session.delete(simulation)
            await self.session.commit()

            await self.pod_service.delete_namespace(simulation_id)
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{api}: 삭제하려는 시뮬레이션에 속한 인스턴스가 있어 시뮬레이션 삭제가 불가합니다.",
            )

        return SimulationDeleteResponse(simulation_id=simulation_id).model_dump()

    async def find_simulation_by_id(self, simulation_id: int, api: str) -> Simulation:
        simulation = await self.repository.find_by_id(simulation_id)

        if not simulation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"존재하지 않는 시뮬레이션 ID",
            )
        return simulation

    async def get_simulation_status(self, simulation):
        instances = simulation.instances

        if not instances:
            return SimulationStatus.EMPTY.value

        for instance in instances:
            pod_ip = await self.pod_service.get_pod_ip(instance)
            pod_status = await RosService.get_pod_status(pod_ip)

            if pod_status == PodStatus.RUNNING.value:
                return SimulationStatus.ACTIVE.value

        return SimulationStatus.INACTIVE.value

    async def get_simulation_detailed_status(self, simulation_id: int):
        """시뮬레이션의 상세 상태 정보 반환"""
        simulation = await self.find_simulation_by_id(
            simulation_id, "get simulation status"
        )
        instances = await self.get_simulation_instances(simulation_id)

        if not instances:
            return {"status": "EMPTY", "message": "인스턴스가 없습니다"}

        detailed_status = []
        for instance in instances:
            try:
                pod_ip = await self.pod_service.get_pod_ip(instance)
                status_response = await RosService.get_pod_status(pod_ip)
                detailed_status.append(
                    {
                        "instance_id": instance.id,
                        "pod_ip": pod_ip,
                        "status": status_response,
                    }
                )
            except Exception as e:
                detailed_status.append({"instance_id": instance.id, "error": str(e)})

        return {
            "simulation_id": simulation_id,
            "simulation_name": simulation.name,
            "instances_status": detailed_status,
        }

    def _validate_pagination_range(self, pagination: PaginationParams, total_count: int) -> None:
        """페이지 범위 검증"""
        if total_count == 0:
            return  # 데이터가 없으면 검증 생략
        
        # size가 None이면 기본값 사용
        page_size = pagination.size if pagination.size and pagination.size > 0 else PaginationParams.DEFAULT_SIZE
        print(f"page_size: {page_size}")
            
        max_page = (total_count + page_size - 1) // page_size
        if pagination.page > max_page:
            raise ValueError(f"페이지 번호가 범위를 벗어났습니다. 최대 페이지: {max_page}")

    def _convert_to_list_items(self, simulations: List[Simulation]) -> List[SimulationListItem]:
        """Simulation 엔티티 리스트를 SimulationListItem 리스트로 변환"""
        if not simulations:
            return []
        return [self._convert_to_list_item(simulation) for simulation in simulations]

    def _convert_to_list_item(self, sim: Simulation) -> SimulationListItem:
        """Simulation 엔티티를 SimulationListItem으로 변환 (상태별 데이터 처리)"""
        
        sim_dict = {
            "simulationId": sim.id,
            "simulationName": sim.name,
            "patternType": sim.pattern_type,
            "status": sim.status,
            "mecId": sim.mec_id,
            "createdAt": sim.created_at,
            "updatedAt": sim.updated_at
        }
        
        return SimulationListItem(**sim_dict)

    async def get_dashboard_data(self, simulation_id: int) -> DashboardData:
        # 1️⃣ 시뮬레이션 조회
        sim = await self.repository.find_by_id(simulation_id)
        if not sim:
            raise HTTPException(status_code=404, detail=f"Simulation {simulation_id} not found")

        # 2️⃣ 패턴별 ExecutionPlan 조회
        if sim.pattern_type == PatternType.SEQUENTIAL:
            execution_plan = await self.get_execution_plan_sequential(sim.id)
        elif sim.pattern_type == PatternType.PARALLEL:
            execution_plan = await self.get_execution_plan_parallel(sim.id)
        else:
            execution_plan = None  # 필요 시 기본 처리

        # 3️⃣ 상태 DTO 구성
        if sim.status == SimulationStatus.INITIATING:
            current_status = CurrentStatusInitiating(
                status=sim.status,
                timestamps=TimestampModel(
                    created_at=sim.created_at,
                    last_updated=sim.updated_at
                )
            )
        elif sim.status == SimulationStatus.PENDING:
            current_status = CurrentStatusPENDING(
                status=sim.status,
                progress=ProgressModel(
                    overall_progress=0.0,
                    ready_to_start=True
                ),
                timestamps=TimestampModel(
                    created_at=sim.created_at,
                    last_updated=sim.updated_at
                )
            )
        else:
            # 예외 상태 기본 처리
            current_status = CurrentStatusInitiating(
                status=sim.status,
                timestamps=TimestampModel(
                    created_at=sim.created_at,
                    last_updated=sim.updated_at
                )
            )

        # 4️⃣ SimulationData 생성
        simulation_data = SimulationData(
            simulation_id=sim.id,
            simulation_name=sim.name,
            simulation_description=sim.description,
            pattern_type=sim.pattern_type,
            mec_id=sim.mec_id,
            namespace=sim.namespace,
            created_at=sim.created_at,
            execution_plan=execution_plan,
            current_status=current_status
        )

        try:
            # 5️⃣ 기본 데이터 추출
            base_data: Dict[str, Any] = extract_simulation_dashboard_data(simulation_data)
            
            # 6️⃣ 메트릭 수집
            metrics_data = await self.collector.collect_dashboard_metrics(simulation_data)
            
            # 7️⃣ DashboardData 구성
            dashboard_data = DashboardData(
                **base_data,
                resource_usage=metrics_data["resource_usage"],
                pod_status=metrics_data["pod_status"]
            )
            return dashboard_data

        except Exception as e:
            # 8️⃣ fallback 처리
            collector = self.collector
            return DashboardData(
                **extract_simulation_dashboard_data(simulation_data),
                resource_usage=collector._get_default_resource_usage(),
                pod_status=collector._get_default_pod_status()
            )
    

    async def get_simulation_summary_list(self) -> List[SimulationSummaryItem]:
        try:
            summary_tuples = await self.repository.find_summary_list()
            
            # DTO로 변환
            return [
                SimulationSummaryItem(
                    simulation_id=sim_id,
                    simulation_name=sim_name
                )
                for sim_id, sim_name in summary_tuples
            ]
        except Exception as e:
            raise

    async def stop_simulation_async(self, simulation_id: int) -> Dict[str, Any]:
        """
        🔑 통합 시뮬레이션 중지 메서드 (라우트에서 호출)
        - 시뮬레이션 패턴 타입 감지 후 적절한 중지 전략 선택
        - 순차 패턴: polling 로직 위임
        - 병렬 패턴: polling 로직 위임
        """
        print(f"🛑 시뮬레이션 중지 요청: {simulation_id}")

        try:
            # 1. 시뮬레이션 조회 및 RUNNING 상태 확인
            simulation = await self.find_simulation_by_id(simulation_id, "stop")
            
            # 2. 상태별 처리
            if simulation.status == SimulationStatus.STOPPED:
                # 이미 중지된 시뮬레이션
                raise HTTPException(
                    status_code=409,  # Conflict
                    detail=f"이미 중지된 시뮬레이션입니다 (현재 상태: {simulation.status})"
                )
            elif simulation.status != SimulationStatus.RUNNING:
                # 실행 중이 아닌 상태
                raise HTTPException(
                    status_code=400,
                    detail=f"시뮬레이션을 중지할 수 없는 상태입니다 (현재 상태: {simulation.status})"
                )
                
            # 3. 중지 진행 중인 경우 확인
            running_info = self.state.running_simulations.get(simulation_id)
            if running_info and running_info.get("is_stopping", False):
                # 이미 중지 진행 중
                raise HTTPException(
                    status_code=409,
                    detail="중지 요청이 이미 진행 중입니다. 완료될 때까지 기다려주세요."
                )

            print(f"📊 시뮬레이션 패턴: {simulation.pattern_type}")

            # 4. 패턴 타입에 따라 중지 메서드 호출
            if simulation.pattern_type == PatternType.SEQUENTIAL:
                print(f"🔄 순차 패턴 중지 처리 시작")
                result = await self._stop_sequential_simulation_via_polling(simulation_id)

            elif simulation.pattern_type == PatternType.PARALLEL:
                print(f"⚡ 병렬 패턴 중지 처리 시작")
                result = await self._stop_parallel_simulation_via_polling(simulation_id)

            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"지원하지 않는 패턴 타입: {simulation.pattern_type}"
                )

            print(f"✅ 시뮬레이션 {simulation_id} 중지 완료")
            return result

        except HTTPException:
            raise
        except Exception as e:
            failure_reason = f"시뮬레이션 중지 중 예상치 못한 오류: {str(e)}"
            print(f"❌ {failure_reason}")
            raise HTTPException(
                status_code=500,
                detail="시뮬레이션 중지 중 내부 오류가 발생했습니다"
            )
    
    async def _stop_sequential_simulation_via_polling(self, simulation_id: int) -> Dict[str, Any]:
        """
        순차 시뮬레이션 중지
        """
        print(f"🔄 순차 시뮬레이션 중지 (polling 위임): {simulation_id}")

        try:
            sim_info = self.state.running_simulations[simulation_id]
            print(f"현재 실행 중인 시뮬레이션 정보: {sim_info}")
        except KeyError as e:
            print(f"❌ 실행 중인 시뮬레이션을 찾을 수 없음: {e}")
            return await self._direct_sequential_stop(simulation_id)
        except Exception as e:
            print(f"❌ 시뮬레이션 정보 조회 중 알 수 없는 오류: {e}")
            raise

        try:
            # 1. 실행 중인 시뮬레이션 확인
            if simulation_id not in self.state.running_simulations:
                print(f"⚠️ 백그라운드 실행 중이 아님, 직접 중지 처리")
                return await self._direct_sequential_stop(simulation_id)

            if sim_info.get("is_stopping"):
                print(f"⚠️ 이미 중지 처리 진행 중")
                raise HTTPException(
                    status_code=400,
                    detail="이미 중지 처리가 진행 중입니다"
                )

            # 2. 중지 처리 플래그 설정 및 신호 전송
            sim_info["is_stopping"] = True
            sim_info["stop_handler"] = "api_sequential"

            stop_event = sim_info["stop_event"]
            stop_event.set()
            print(f"✅ 시뮬레이션 {simulation_id} 중지 신호 전송 (polling이 순차 중지 처리)")

            # 3. polling 로직의 중지 완료 대기
            max_wait_time = 120
            start_wait = datetime.now(timezone.utc)

            while (datetime.now(timezone.utc) - start_wait).total_seconds() < max_wait_time:
                if simulation_id not in self.state.running_simulations:
                    print(f"✅ polling 로직에 의한 중지 완료 확인")
                    break
                await asyncio.sleep(1)
            else:
                print(f"⏰ 중지 처리 타임아웃 ({max_wait_time}초)")
                # 타임아웃 시 FAILED 상태 업데이트
                await self._update_simulation_status_and_log(simulation_id, SimulationStatus.FAILED, "중지 처리 타임아웃")
                raise HTTPException(status_code=500, detail="중지 처리 타임아웃")
    
            await self._update_simulation_status_and_log(simulation_id, SimulationStatus.STOPPED, "사용자 요청에 의해 중지됨")

            # 4. 최종 상태 확인 및 결과 반환
            try:
                final_simulation = await self.find_simulation_by_id(simulation_id, "stop result")
                print(f"📌 최종 시뮬레이션 상태: {final_simulation.status}")
            except Exception as e:
                print(f"❌ 최종 상태 조회 실패: {e}")
                raise

            try:
                steps = await self.repository.find_simulation_steps(simulation_id)
                print(f"📌 스텝 개수 조회 성공: {len(steps)}")
            except Exception as e:
                print(f"❌ 스텝 조회 실패: {e}")
                steps = []

            # 결과 반환
            return {
                "simulationId": simulation_id,
                "status": SimulationStatus.STOPPED,
                "stoppedAt": datetime.now(timezone.utc)
            }

        except HTTPException:
            raise
        except Exception as e:
            print(f"❌ 중지 처리 중 알 수 없는 오류 발생: {e}")
            try:
                await self._update_simulation_status_and_log(simulation_id, SimulationStatus.FAILED, f"중지 처리 오류: {str(e)}")
            except:
                pass
            raise

    async def _stop_parallel_simulation_via_polling(self, simulation_id: int) -> Dict[str, Any]:
        """
        ⚡ 병렬 시뮬레이션 중지
        """
        print(f"⚡ 병렬 시뮬레이션 중지 (polling 위임): {simulation_id}")

        try:
            sim_info = self.state.running_simulations.get(simulation_id)
            if not sim_info:
                print(f"⚠️ 백그라운드 실행 중이 아님, 직접 중지 처리")
                return await self._direct_parallel_stop(simulation_id)

            if sim_info.get("is_stopping"):
                print(f"⚠️ 이미 중지 처리 진행 중")
                raise HTTPException(status_code=400, detail="이미 중지 처리가 진행 중입니다")

            # 중지 플래그 설정 및 stop_event 신호 전송
            sim_info["is_stopping"] = True
            sim_info["stop_handler"] = "api_parallel"
            stop_event = sim_info["stop_event"]
            stop_event.set()
            print(f"✅ 시뮬레이션 {simulation_id} 중지 신호 전송 (polling이 병렬 중지 처리)")

            # polling으로 중지 완료 대기
            max_wait_time = 120
            start_wait = datetime.now(timezone.utc)
            while (datetime.now(timezone.utc) - start_wait).total_seconds() < max_wait_time:
                if simulation_id not in self.state.running_simulations:
                    print(f"✅ polling 로직에 의한 중지 완료 확인")
                    break
                await asyncio.sleep(1)
            else:
                print(f"⏰ 중지 처리 타임아웃 ({max_wait_time}초)")
                # 타임아웃 시 FAILED 상태 업데이트
                await self._update_simulation_status_and_log(simulation_id, SimulationStatus.FAILED, "중지 처리 타임아웃")
                raise HTTPException(status_code=500, detail="중지 처리 타임아웃")

            # STOPPED 상태 업데이트
            await self._update_simulation_status_and_log(simulation_id, SimulationStatus.STOPPED, "polling 로직 완료")

            # 결과 반환
            return {
                "simulationId": simulation_id,
                "status": SimulationStatus.STOPPED,
                "stoppedAt": datetime.now(timezone.utc)
            }

        except HTTPException:
            raise
        except Exception as e:
            traceback.print_stack()
            print(f"❌ 중지 처리 중 알 수 없는 오류 발생: {e}")
            try:
                await self._update_simulation_status_and_log(simulation_id, SimulationStatus.FAILED, f"중지 처리 오류: {str(e)}")
            except:
                pass
            raise

    async def _direct_sequential_stop(self, simulation_id: int) -> Dict[str, Any]:
        """
        직접 순차 중지 처리 (백그라운드 실행 중이 아닌 경우)
        """
        print(f"🔧 직접 순차 중지 처리: {simulation_id}")
        
        try:
            # 스텝 역순 조회
            simulation = await self.find_simulation_by_id(simulation_id, "direct sequential stop")
            steps = await self.repository.find_simulation_steps(simulation_id)
            steps_reversed = sorted(steps, key=lambda x: x.step_order, reverse=True)
            
            total_pods = 0
            stopped_pods = 0
            failed_pods = 0
            
            # 각 스텝별로 역순 처리
            for step in steps_reversed:
                pod_list = self.pod_service.get_pods_by_filter(
                    namespace=simulation.namespace,
                    filter_params=StepOrderFilter(step_order=step.step_order)
                )
                
                if not pod_list:
                    continue
                
                total_pods += len(pod_list)
                
                # 스텝별 Pod 중지
                stop_results = await self.rosbag_executor.stop_rosbag_parallel_pods(
                    pods=pod_list,
                    step_order=step.step_order
                )
                
                # 결과 집계
                stopped_pods += sum(1 for r in stop_results if r.status == "stopped")
                failed_pods += sum(1 for r in stop_results if r.status in ["failed", "timeout"])
            
            # 상태 업데이트
            final_status = "STOPPED" if failed_pods == 0 else "FAILED"
            await self._update_simulation_status_and_log(
                simulation_id, final_status, f"직접 순차 중지 완료 - 총 {total_pods}개 Pod"
            )
            
            return {
                "simulationId": simulation_id,
                "patternType": simulation.pattern_type,
                "status": SimulationStatus.STOPPED,
                "message": "순차 시뮬레이션 직접 중지 완료",
                "totalPods": total_pods,
                "stoppedPods": stopped_pods,
                "failedPods": failed_pods,
                "stoppedAt": datetime.now(timezone.utc)
            }
            
        except Exception as e:
            traceback.print_stack()
            error_msg = f"직접 순차 중지 실패: {str(e)}"
            print(f"❌ {error_msg}")
            await self._update_simulation_status_and_log(simulation_id, "FAILED", error_msg)
            raise

    async def _direct_parallel_stop(self, simulation_id: int) -> Dict[str, Any]:
        """
        직접 병렬 중지 처리 (백그라운드 실행 중이 아닌 경우)
        """
        print(f"🔧 직접 병렬 중지 처리: {simulation_id}")
        
        try:
            # 모든 그룹의 Pod 수집
            simulation = await self.find_simulation_by_id(simulation_id, "direct parallel stop")
            groups = await self.repository.find_simulation_groups(simulation_id)
            all_pods = []
            
            for group in groups:
                pod_list = self.pod_service.get_pods_by_filter(
                    namespace=simulation.namespace,
                    filter_params=GroupIdFilter(group_id=group.id)
                )
                all_pods.extend(pod_list)
            
            if not all_pods:
                return {
                    "simulationId": simulation_id,
                    "patternType": simulation.pattern_type,
                    "status": "no_pods",
                    "message": "중지할 Pod가 없음"
                }
            
            # 모든 Pod 동시 중지
            stop_results = await self.rosbag_executor.stop_rosbag_parallel_all_pods(
                pods=all_pods
            )
            
            # 결과 집계
            stopped_count = sum(1 for r in stop_results if r.status == "stopped")
            failed_count = sum(1 for r in stop_results if r.status in ["failed", "timeout"])
            
            # 상태 업데이트
            final_status = "STOPPED" if failed_count == 0 else "FAILED"
            await self._update_simulation_status_and_log(
                simulation_id, final_status, f"직접 병렬 중지 완료 - 총 {len(all_pods)}개 Pod"
            )
            
            return {
                "simulationId": simulation_id,
                "patternType": simulation.pattern_type,
                "status": SimulationStatus.STOPPED,
                "message": "병렬 시뮬레이션 직접 중지 완료",
                "totalPods": len(all_pods),
                "stoppedPods": stopped_count,
                "failedPods": failed_count,
                "stoppedAt": datetime.now(timezone.utc)
            }
            
        except Exception as e:
            error_msg = f"직접 병렬 중지 실패: {str(e)}"
            print(f"❌ {error_msg}")
            await self._update_simulation_status_and_log(simulation_id, "FAILED", error_msg)
            raise
        
    async def _monitor_pod_progress(
        self, pods: list, rosbag_executor, stop_event: asyncio.Event, execution_context: str, poll_interval: float = 1.0
    ):
        """
        Pod 진행 상황 모니터링
        - pods: V1Pod 리스트
        - rosbag_executor: execute_single_pod / _check_pod_rosbag_status 제공 객체
        - stop_event: 중지 이벤트
        - execution_context: 로그 prefix
        """
        pod_tasks = {asyncio.create_task(rosbag_executor.execute_single_pod(pod)): pod.metadata.name for pod in pods}
        completed_pods = set()
        pod_status_dict = {pod.metadata.name: {"progress": 0.0, "status": "pending"} for pod in pods}

        while pod_tasks:
            done_tasks = [t for t in pod_tasks if t.done()]

            # 완료된 Pod 처리
            for task in done_tasks:
                pod_name = pod_tasks.pop(task)
                try:
                    result = task.result()
                    pod_status_dict[pod_name]["progress"] = 1.0
                    pod_status_dict[pod_name]["status"] = "completed"
                    completed_pods.add(result.pod_name)
                except asyncio.CancelledError:
                    pod_status_dict[pod_name]["status"] = "cancelled"
                except Exception:
                    pod_status_dict[pod_name]["status"] = "failed"

            # 진행 중 Pod 상태 확인
            status_tasks = {
                pod.metadata.name: asyncio.create_task(rosbag_executor._check_pod_rosbag_status(pod))
                for pod in pods
                if pod_status_dict[pod.metadata.name]["status"] == "pending"
            }
            pod_statuses = await asyncio.gather(*status_tasks.values(), return_exceptions=True)

            for pod_name, status in zip(status_tasks.keys(), pod_statuses):
                if isinstance(status, dict):
                    current_loop = status.get("current_loop", 0)
                    max_loops = max(status.get("max_loops", 1), 1)
                    is_playing = status.get("is_playing", True)
                    pod_status_dict[pod_name]["progress"] = min(current_loop / max_loops, 1.0)
                    pod_status_dict[pod_name]["status"] = "playing" if is_playing else "done"
                else:
                    pod_status_dict[pod_name]["status"] = "failed"

            # 전체 진행률 로그
            total_progress = sum(s["progress"] for s in pod_status_dict.values()) / len(pods)
            running_info = [f"{n}({int(s['progress']*100)}%/{s['status']})" for n, s in pod_status_dict.items()]
            debug_print(f"{execution_context} ⏳ 진행률: {total_progress*100:.1f}% ({len(completed_pods)}/{len(pods)}) | {', '.join(running_info)}")

            # stop_event 감지 시 즉시 종료
            if stop_event.is_set():
                debug_print(f"{execution_context} ⏹️ 중지 이벤트 감지 - 모든 Pod 즉시 종료")
                for t in pod_tasks.keys():
                    t.cancel()
                await asyncio.gather(*pod_tasks.keys(), return_exceptions=True)
                return "CANCELLED", pod_status_dict

            await asyncio.sleep(poll_interval)

        return "COMPLETED", pod_status_dict
    
    async def get_current_status(self, simulation_id: int) -> CurrentStatus:
        simulation = await self.find_simulation_by_id(simulation_id, "status")
        print(f"시뮬레이션 상태: {simulation.status}")
        
        now = datetime.now(timezone.utc)
        status_str = simulation.status
        
        started_at = simulation.started_at if status_str == SimulationStatus.RUNNING else None
        
        # 공통 Timestamps 
        timestamps = CurrentTimestamps(
            created_at=simulation.created_at,
            started_at=started_at,
            last_updated=now
        )

        if status_str == "INITIATING":
            return CurrentStatus(
                status=status_str,
                timestamps=timestamps,
                message="네임스페이스 및 기본 리소스 생성 중..."
            )
        elif status_str == "PENDING":
            progress = None

            if simulation.pattern_type == PatternType.SEQUENTIAL:
                total_steps = await self.repository.count_simulation_steps(simulation_id)
                
                progress = SequentialProgress(
                    overall_progress=0.0,
                    current_step=0,
                    completed_steps=0,
                    total_steps=total_steps
                )
                
                step_progress_list = await self.repository.get_simulation_step_progress(simulation_id)
                
                # 상태별 스텝 처리
                step_details = []

                for step in step_progress_list:
                    step_status = StepStatus(step["status"])

                    step_detail = StepDetail(
                        step_order=step["step_order"],
                        status=step_status,
                        progress=step["progress_percentage"],
                        started_at=step.get("started_at"),
                        completed_at=step.get("completed_at"),
                        failed_at=step.get("failed_at"),
                        stopped_at=step.get("stopped_at"),
                        autonomous_agents=step.get("autonomous_agents", 0),
                        current_repeat=step.get("current_repeat", 0),
                        total_repeats=step.get("repeat_count", 0),
                        error=step.get("error")
                    )
                    step_details.append(step_detail)
                
            elif simulation.pattern_type == PatternType.PARALLEL:
                total_groups = await self.repository.count_simulation_groups(simulation_id)
                
                progress = ParallelProgress(
                    overall_progress=0.0,
                    completed_groups=0,
                    running_groups=0,
                    total_groups=total_groups
                )
                
                # 그룹별 상세 정보 생성
                group_list = await self.repository.get_simulation_group_progress(simulation_id)
                group_details = []
                for group in group_list:
                    debug_print(f"{group}")
                    group_detail = GroupDetail(
                        group_id=group["group_id"],
                        status=GroupStatus(group["status"]),
                        progress=group.get("progress", 0.0),
                        started_at=group.get("started_at"),
                        completed_at=group.get("completed_at"),
                        failed_at=group.get("failed_at"),
                        stopped_at=group.get("stopped_at"),
                        autonomous_agents=group.get("autonomous_agents", 0),
                        current_repeat=group.get("current_repeat", 0),
                        total_repeats=group.get("total_repeats", 0),
                        error=group.get("error")
                    )
                    group_details.append(group_detail)

            
            if progress is None:
                # 알 수 없는 패턴 타입인 경우 기본값 제공
                debug_print(f"Unknown pattern type: {simulation.pattern_type}")
                progress = SequentialProgress(
                    overall_progress=0.0
                )
            
            return CurrentStatus(
                status=status_str,
                timestamps=timestamps,
                progress=progress,
                step_details=step_details if simulation.pattern_type == PatternType.SEQUENTIAL else None,
                group_details=group_details if simulation.pattern_type == PatternType.PARALLEL else None,
                message="시뮬레이션 시작 준비 완료"
            )
        elif status_str in ["RUNNING", "COMPLETED", "STOPPED", "FAILED"]:
            if simulation.pattern_type == PatternType.SEQUENTIAL:
                return await self.get_sequential_current_status(simulation_id)
            elif simulation.pattern_type == PatternType.PARALLEL:
                return await self.get_parallel_current_status(simulation_id)
                
        else:                    
            # 알 수 없는 상태 처리
            return CurrentStatus(
                status=status_str,
                timestamps=timestamps,
                message="알 수 없는 상태"
            )

    async def get_sequential_current_status(self, simulation_id: int) -> CurrentStatus:
        """
        순차(Sequential) 패턴 시뮬레이션의 현재 상태 조회
        - Redis에서 실시간 진행상황 우선 조회
        - Redis 데이터 없으면 DB fallback
        - RUNNING / COMPLETED / STOPPED / FAILED 상태 처리
        """
        redis_client = RedisSimulationClient()
        
        try:
            # 🚀 1차 시도: Redis에서 실시간 진행상황 조회
            await redis_client.connect()
            redis_status = await redis_client.get_simulation_status(simulation_id)
            
            if redis_status:
                debug_print(f"📡 Redis에서 시뮬레이션 {simulation_id} 실시간 상태 조회 성공")
                return await self._convert_redis_to_current_status(redis_status)
            else:
                debug_print(f"📡 Redis에 시뮬레이션 {simulation_id} 데이터 없음 - DB fallback")
                
        except Exception as e:
            debug_print(f"❌ Redis 조회 실패: {e} - DB fallback")
        finally:
            if redis_client.client:
                await redis_client.client.close()
        
        # 🗄️ 2차 시도: DB에서 조회 (Redis 실패 시 fallback)
        debug_print(f"🗄️ DB에서 시뮬레이션 {simulation_id} 상태 조회")
        return await self._get_status_from_db(simulation_id)


    async def _convert_redis_to_current_status(self, redis_data: dict) -> CurrentStatus:
        """
        Redis 데이터를 CurrentStatus DTO로 변환
        """
        # 타임스탬프 변환
        timestamps_data = redis_data.get("timestamps", {})
        timestamps = CurrentTimestamps(
            created_at=self._parse_datetime(timestamps_data.get("createdAt")),
            last_updated=self._parse_datetime(timestamps_data.get("lastUpdated")) or datetime.now(timezone.utc),
            started_at=self._parse_datetime(timestamps_data.get("startedAt")),
            completed_at=self._parse_datetime(timestamps_data.get("completedAt")),
            failed_at=self._parse_datetime(timestamps_data.get("failedAt")),
            stopped_at=self._parse_datetime(timestamps_data.get("stoppedAt"))
        )
        
        # 진행률 변환
        progress_data = redis_data.get("progress", {})
        progress = SequentialProgress(
            overall_progress=round(progress_data.get("overallProgress", 0.0) * 100, 1),  # 0.65 -> 65.0
            current_step=progress_data.get("currentStep"),
            completed_steps=progress_data.get("completedSteps", 0),
            total_steps=progress_data.get("totalSteps", 0)
        )
        
        # 스텝 디테일 변환
        step_details = []
        for step_data in redis_data.get("stepDetails", []):
            step_detail = StepDetail(
                step_order=step_data["stepOrder"],
                status=StepStatus(step_data["status"]),
                progress=round(step_data.get("progress", 0.0) * 100, 1),  # 0.65 -> 65.0
                started_at=self._parse_datetime(step_data.get("startedAt")),
                completed_at=self._parse_datetime(step_data.get("completedAt")),
                failed_at=self._parse_datetime(step_data.get("failedAt")),
                stopped_at=self._parse_datetime(step_data.get("stoppedAt")),
                autonomous_agents=step_data.get("autonomousAgents", 0),
                current_repeat=step_data.get("currentRepeat", 0),
                total_repeats=step_data.get("totalRepeats", 0),
                error=step_data.get("error")
            )
            step_details.append(step_detail)
        
        return CurrentStatus(
            status=SimulationStatus(redis_data["status"]),
            progress=progress,
            timestamps=timestamps,
            step_details=step_details,
            message=redis_data.get("message", "상태 정보 없음")
        )


    async def _get_status_from_db(self, simulation_id: int) -> CurrentStatus:
        """
        DB에서 시뮬레이션 상태 조회 (Redis fallback)
        - 기존 로직 그대로 유지
        """
        simulation = await self.find_simulation_by_id(simulation_id, "status")
        status_str = simulation.status
        timestamps = CurrentTimestamps(
            created_at=simulation.created_at,
            started_at=simulation.started_at,
            completed_at=simulation.completed_at,
            failed_at=simulation.failed_at,
            stopped_at=simulation.stopped_at,
            last_updated=datetime.now(timezone.utc)
        )

        # 전체 진행률 요약과 스텝별 정보 조회
        overall_summary = await self.repository.get_simulation_overall_progress(simulation_id)
        step_progress_list = await self.repository.get_simulation_step_progress(simulation_id)

        total_steps = overall_summary["total_steps"]
        completed_steps = overall_summary["completed_steps"]
        overall_progress = (completed_steps / total_steps * 100) if total_steps > 0 else 0.0

        # 상태별 스텝 처리
        step_details = []
        current_step_info = None

        for step in step_progress_list:
            step_status = StepStatus(step["status"])
            if status_str == "RUNNING" and step["status"] == "RUNNING":
                current_step_info = step
            elif status_str == "STOPPED" and step["status"] in ["RUNNING", "STOPPED"]:
                step_status = StepStatus.STOPPED
                current_step_info = step
            elif status_str == "FAILED" and step["status"] == "FAILED":
                step_status = StepStatus.FAILED
                current_step_info = step
            elif status_str == "COMPLETED":
                step_status = StepStatus.COMPLETED

            step_detail = StepDetail(
                step_order=step["step_order"],
                status=step_status,
                progress=step["progress_percentage"],
                started_at=step.get("started_at"),
                completed_at=step.get("completed_at"),
                failed_at=step.get("failed_at"),
                stopped_at=step.get("stopped_at"),
                autonomous_agents=step.get("autonomous_agents", 0),
                current_repeat=step.get("current_repeat", 0),
                total_repeats=step.get("repeat_count", 0),
                error=step.get("error")
            )
            step_details.append(step_detail)

        # 메시지 생성
        if status_str == "RUNNING" and current_step_info:
            message = f"Step {current_step_info['step_order']} 실행 중 ({current_step_info['progress_percentage']:.1f}% 완료)"
            if current_step_info.get("current_repeat", 0) > 0:
                message += f" - {current_step_info['current_repeat']}/{current_step_info['repeat_count']} 반복"
        elif status_str == "COMPLETED":
            message = "모든 스텝 실행 완료"
        elif status_str == "STOPPED":
            message = f"Step {current_step_info['step_order']} 중지됨" if current_step_info else "시뮬레이션 중지됨"
        elif status_str == "FAILED":
            error_msg = current_step_info.get("error") if current_step_info else None
            message = f"Step {current_step_info['step_order']} 실패: {error_msg}" if error_msg else "시뮬레이션 실패"
        else:
            message = "상태 정보 없음"

        # 현재 진행 중 스텝 번호
        current_step_number = current_step_info["step_order"] if current_step_info else 0

        progress = SequentialProgress(
            overall_progress=round(overall_progress, 1),
            current_step=current_step_number,
            completed_steps=completed_steps,
            total_steps=total_steps
        )

        return CurrentStatus(
            status=SimulationStatus(status_str),
            progress=progress,
            timestamps=timestamps,
            step_details=step_details,
            message=message
        )


    def _parse_datetime(self, datetime_str: str | None) -> datetime | None:
        """
        ISO 포맷 문자열을 datetime 객체로 변환
        """
        if not datetime_str:
            return None
        try:
            # ISO 포맷 파싱 (2024-01-01T12:00:00.123456+00:00)
            if datetime_str.endswith('Z'):
                datetime_str = datetime_str[:-1] + '+00:00'
            return datetime.fromisoformat(datetime_str)
        except (ValueError, TypeError) as e:
            debug_print(f"❌ 날짜 파싱 실패: {datetime_str}, 오류: {e}")
            return None

    async def get_parallel_current_status(self, simulation_id: int) -> CurrentStatus:
        """
        병렬(Parallel) 패턴 시뮬레이션의 현재 상태 조회
        - Redis에서 실시간 진행상황 우선 조회
        - Redis 데이터 없으면 DB fallback
        - RUNNING / COMPLETED / STOPPED / FAILED 상태 처리
        """
        redis_client = RedisSimulationClient()
        
        try:
            # 🚀 1차 시도: Redis에서 실시간 진행상황 조회
            await redis_client.connect()
            redis_status = await redis_client.get_simulation_status(simulation_id)
            
            debug_print(f"Redis 에 저장된 시뮬레이션 정보: {redis_status}")
            
            if redis_status:
                debug_print(f"📡 Redis에서 병렬 시뮬레이션 {simulation_id} 실시간 상태 조회 성공")
                return await self._convert_redis_to_parallel_status(redis_status)
            else:
                debug_print(f"📡 Redis에 병렬 시뮬레이션 {simulation_id} 데이터 없음 - DB fallback")
                
        except Exception as e:
            debug_print(f"❌ Redis 조회 실패: {e} - DB fallback")
        finally:
            if redis_client.client:
                await redis_client.client.close()
        
        # 🗄️ 2차 시도: DB에서 조회 (Redis 실패 시 fallback)
        debug_print(f"🗄️ DB에서 병렬 시뮬레이션 {simulation_id} 상태 조회")
        return await self._get_parallel_status_from_db(simulation_id)


    async def _convert_redis_to_parallel_status(self, redis_data: dict) -> CurrentStatus:
        """
        Redis 데이터를 병렬 시뮬레이션용 CurrentStatus DTO로 변환
        """
        # 타임스탬프 변환
        timestamps_data = redis_data.get("timestamps", {})
        timestamps = CurrentTimestamps(
            created_at=self._parse_datetime(timestamps_data.get("createdAt")),
            last_updated=self._parse_datetime(timestamps_data.get("lastUpdated")) or datetime.now(timezone.utc),
            started_at=self._parse_datetime(timestamps_data.get("startedAt")),
            completed_at=self._parse_datetime(timestamps_data.get("completedAt")),
            failed_at=self._parse_datetime(timestamps_data.get("failedAt")),
            stopped_at=self._parse_datetime(timestamps_data.get("stoppedAt"))
        )
        
        # 병렬 진행률 변환
        progress_data = redis_data.get("progress", {})
        progress = ParallelProgress(
            overall_progress=round(progress_data.get("overallProgress", 0.0) * 100, 1),  # 0.65 -> 65.0
            completed_groups=progress_data.get("completedGroups", 0),
            running_groups=progress_data.get("runningGroups", 0),
            total_groups=progress_data.get("totalGroups", 0)
        )
        
        # 그룹 디테일 변환
        group_details = []
        for group_data in redis_data.get("groupDetails", []):
            debug_print(f"Redis 에 저장된 그룹 별 상세정보: {group_data}")
            group_detail = GroupDetail(
                group_id=group_data["groupId"],
                status=GroupStatus(group_data["status"]),
                progress=round(group_data.get("progress", 0.0), 1),  # Redis에서 이미 %로 저장됨
                started_at=self._parse_datetime(group_data.get("startedAt")),
                completed_at=self._parse_datetime(group_data.get("completedAt")),
                failed_at=self._parse_datetime(group_data.get("failedAt")),
                stopped_at=self._parse_datetime(group_data.get("stoppedAt")),
                autonomous_agents=group_data.get("autonomousAgents", 0),
                current_repeat=group_data.get("currentRepeat", 0),
                total_repeats=group_data.get("totalRepeats", 0),
                error=group_data.get("error")
            )
            group_details.append(group_detail)
        
        return CurrentStatus(
            status=SimulationStatus(redis_data["status"]),
            progress=progress,
            timestamps=timestamps,
            group_details=group_details,
            message=redis_data.get("message", "상태 정보 없음")
        )


    async def _get_parallel_status_from_db(self, simulation_id: int) -> CurrentStatus:
        """
        DB에서 병렬 시뮬레이션 상태 조회 (Redis fallback)
        - 기존 로직 그대로 유지
        """
        simulation = await self.find_simulation_by_id(simulation_id, "status")
        status_str = simulation.status
        timestamps = CurrentTimestamps(
            created_at=simulation.created_at,
            started_at=simulation.started_at,
            completed_at=simulation.completed_at,
            failed_at=simulation.failed_at,
            stopped_at=simulation.stopped_at,
            last_updated=datetime.now(timezone.utc)
        )

        # 그룹별 상세 정보 조회
        group_list = await self.repository.get_simulation_group_progress(simulation_id)
        overall_summary = await self.repository.get_simulation_overall_group_progress(simulation_id)
        
        # 전체 가중 평균 진행률 계산
        overall_progress = overall_summary["overall_progress"]
        
        # 완료 / 실행 / 총 그룹 수
        total_groups = overall_summary["total_groups"]
        completed_groups = overall_summary["completed_groups"]
        running_groups = overall_summary["running_groups"]

        # 그룹별 상세 정보 생성
        group_details = []
        for group in group_list:
            debug_print(f"{group}")
            group_detail = GroupDetail(
                group_id=group["group_id"],
                status=GroupStatus(group["status"]),
                progress=group.get("progress", 0.0),
                started_at=group.get("started_at"),
                completed_at=group.get("completed_at"),
                failed_at=group.get("failed_at"),
                stopped_at=group.get("stopped_at"),
                autonomous_agents=group.get("autonomous_agents", 0),
                current_repeat=group.get("current_repeat", 0),
                total_repeats=group.get("total_repeats", 0),
                error=group.get("error")
            )
            group_details.append(group_detail)

        # 메시지 생성
        if status_str == "RUNNING":
            message = f"{running_groups}개 그룹 병렬 실행 중"
        elif status_str == "COMPLETED":
            message = "모든 그룹 시뮬레이션 완료"
        elif status_str == "STOPPED":
            message = "사용자에 의해 시뮬레이션 중단됨"
        elif status_str == "FAILED":
            failed_group = next((g for g in group_list if g["status"] == "FAILED"), None)
            if failed_group:
                error_msg = failed_group.get("error")
                message = f"그룹 {failed_group['group_id']}에서 치명적 오류 발생으로 전체 시뮬레이션 실패"
                if error_msg:
                    message += f": {error_msg}"
            else:
                message = "시뮬레이션 실패"
        else:
            message = "상태 정보 없음"

        # Progress 객체 생성
        progress = ParallelProgress(
            overall_progress=round(overall_progress, 1),
            completed_groups=completed_groups,
            running_groups=running_groups,
            total_groups=total_groups
        )

        return CurrentStatus(
            status=SimulationStatus(status_str),
            progress=progress,
            timestamps=timestamps,
            group_details=group_details,
            message=message
        )

    async def update_simulation_description(self, simulation_id: int, description: str):
        success = await self.repository.update_simulation_description(simulation_id, description)
        if not success:
            raise ValueError(f"Simulation ID {simulation_id} not found")
        return True
    
    async def update_pattern_background(self, simulation_id: int, pattern_update: PatternUpdateRequest):
        """
        BackgroundTasks로 실행되는 실제 패턴 업데이트
        - steps_update / groups_update 처리
        - 진행상황 Redis 기록
        """
        # 1. 시뮬레이션 조회
        simulation = await self.find_simulation_by_id(simulation_id)
        pattern_type = simulation.pattern_type
        namespace = simulation.namespace
        
        # 2. step/group 한 번에 조회
        steps_map = {}
        groups_map = {}
        if pattern_type == PatternType.SEQUENTIAL:
            steps_map = {step.step_order: step for step in await self.repository.find_simulation_steps(simulation_id)}
        elif pattern_type == PatternType.PARALLEL:
            groups_map = {group.group_id: group for group in await self.repository.find_simulation_groups(simulation_id)}
            
        # 3. steps_update 처리
        if pattern_type == PatternType.SEQUENTIAL and pattern_update.steps_update:
            for step_update in pattern_update.steps_update:
                step_entity = steps_map.get(step_update.step_order)
                if step_entity:
                    """기존 단계 업데이트"""
                    update_data = step_update.dict(exclude_unset=True, exclude={"step_order"})
                    await self.repository.update_simulation_step_configuration(
                        step_id=update_data["step_id"],
                        execution_time=update_data["execution_time"],
                        delay_after_completion=update_data["delay_after_completion"],
                        repeat_count=update_data["repeat_count"]
                    )
                else:
                    """새 단계 생성"""
                    step_successful = 0
                    step_failed = 0
                    step_created = 0
                    
                    # 템플릿 조회
                    template = await self.template_repository.find_by_id(step_update.template_id)
                    
                    # SimulationStep DB 저장
                    step = await self.repository.create_simulation_step(
                        simulation_id=simulation_id,
                        step_order=step_update.step_order,
                        template_id=template.template_id,
                        autonomous_agent_count=step_update.autonomous_agent_count,
                        execution_time=step_update.execution_time,
                        delay_after_completion=step_update.delay_after_completion,
                        repeat_count=step_update.repeat_count
                    )
                    
                    # 시뮬레이션 설정 정보 구성
                    simulation_config = {
                        'bag_file_path': template.bag_file_path,
                        'repeat_count': step.repeat_count,
                        'max_execution_time': f"{step.execution_time}s" if step.execution_time else "3600s",
                        'communication_port': 11311,  # ROS 기본 포트
                        'data_format': 'ros-bag',
                        'debug_mode': False, 
                        'log_level': 'INFO',
                        'delay_after_completion': step.delay_after_completion,
                        'simulation_name': simulation.name,
                        'pattern_type': simulation.pattern_type,
                        'mec_id': simulation.mec_id
                    }
                    
                    instances = await self.instance_repository.create_instances_batch(
                        simulation,
                        template,
                        step_order=step.step_order,
                        agent_count=step.autonomous_agent_count
                    )
                    
                    # Pod 생성 (동시 처리로 성능 향상)
                    pod_creation_tasks = []
                    
                    for instance in instances:
                        """단일 Pod 생성 및 상태 업데이트"""
                        try:
                            # 실제 Pod 생성
                            pod_name = await PodService.create_pod(instance, template, simulation_config)
                            
                            return pod_name
                        except Exception as e:
                            raise e
                    
                    # 동시 Pod 생성 실행
                    print(f"  -> {len(pod_creation_tasks)}개 Pod 동시 생성 시작...")
                    results = await asyncio.gather(*pod_creation_tasks, return_exceptions=True)
                    
                    # 결과 집계
                    for result in results:
                        step_created += 1
                        if isinstance(result, Exception):
                            step_failed += 1
                            print(f"    -> Pod 생성 실패: {result}")
                        else:
                            step_successful += 1
                            print(f"    -> Pod 생성 성공: {result}")
                    
                