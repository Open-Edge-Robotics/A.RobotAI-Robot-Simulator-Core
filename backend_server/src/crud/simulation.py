import asyncio
from datetime import datetime, timezone
import traceback
from typing import Any, Dict, Tuple, List, Optional
from fastapi import HTTPException, status
from sqlalchemy import select, exists, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload
from starlette.status import HTTP_409_CONFLICT
from kubernetes.client import V1Pod

from state import SimulationState
from utils.debug_print import debug_print
from utils.rosbag_executor import RosbagExecutor
from schemas.simulation_detail import CurrentStatusInitiating, CurrentStatusReady, ExecutionPlanParallel, ExecutionPlanSequential, GroupModel, ProgressModel, SimulationData, StepModel, TimestampModel
from schemas.pod import GroupIdFilter, StepOrderFilter
from repositories.simulation_repository import SimulationRepository
from schemas.pagination import PaginationMeta, PaginationParams
from models.enums import ExecutionStatus, PatternType, SimulationStatus
from utils.simulation_background import (
    handle_parallel_pattern_background,
    handle_sequential_pattern_background,
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
    SimulationOverview
)
from utils.my_enum import PodStatus, API
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import async_sessionmaker

class SimulationService:
    def __init__(self, session: AsyncSession, sessionmaker: async_sessionmaker, repository: SimulationRepository, state: SimulationState):
        self.session = session
        self.sessionmaker = sessionmaker
        self.repository = repository
        self.state = state
        self.ros_service = RosService()
        self.pod_service = PodService()
        self.templates_service = TemplateService(session)
        
        # RosbagExecutor 초기화 (pod_service와 ros_service 의존성 주입)
        self.rosbag_executor = RosbagExecutor(self.pod_service, self.ros_service)   

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
        elif sim.status == SimulationStatus.READY:
            current_status = CurrentStatusReady(
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
                background_task = self._run_sequential_simulation(simulation_id, stop_event)
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
        try:
            debug_print(f"백그라운드에서 시뮬레이션 실행 시작: {simulation_id}")

            # 1️⃣ 스텝 조회
            simulation = await self.find_simulation_by_id(simulation_id, "background run")
            steps = await self.repository.find_simulation_steps(simulation_id)
            debug_print(f"📊 스텝 조회 완료: {len(steps)}개")

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

                # Pod 조회
                pod_list = self.pod_service.get_pods_by_filter(
                    namespace=simulation.namespace,
                    filter_params=StepOrderFilter(step_order=step.step_order)
                )

                if not pod_list:
                    failure_reason = f"스텝 {step.step_order}에서 Pod를 찾을 수 없음"
                    debug_print(f"❌ {failure_reason}")
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

                # 3️⃣ Pod 진행상황 루프
                while pod_tasks:
                    done_tasks = [t for t in pod_tasks if t.done()]

                    # 완료된 Pod 처리
                    for task in done_tasks:
                        pod_name = pod_tasks.pop(task)
                        try:
                            result = task.result()
                            completed_pods.add(result.pod_name)
                            debug_print(f"✅ Pod 완료: {result.pod_name} ({len(completed_pods)}/{len(pod_list)})")
                        except asyncio.CancelledError:
                            debug_print(f"🛑 Pod CancelledError 감지: {pod_name}")
                        except Exception as e:
                            debug_print(f"💥 Pod 실행 실패: {pod_name}: {e}")

                    # 진행 중 Pod 상태 확인 및 로그
                    total_progress = 0.0
                    running_info = []

                    status_tasks = {
                        pod.metadata.name: asyncio.create_task(self.rosbag_executor._check_pod_rosbag_status(pod))
                        for pod in pod_list
                    }

                    pod_statuses = await asyncio.gather(*status_tasks.values(), return_exceptions=True)

                    for pod_name, status in zip(status_tasks.keys(), pod_statuses):
                        if pod_name in completed_pods:
                            pod_progress = 1.0
                            running_info.append(f"{pod_name}(완료)")
                        elif isinstance(status, dict):
                            is_playing = status.get("isPlaying", True)
                            current_loop = status.get("current_loop", 0)
                            max_loops = max(status.get("max_loops", 1), 1)
                            pod_progress = min(current_loop / max_loops, 1.0)
                            if is_playing:
                                running_info.append(f"{pod_name}({current_loop}/{max_loops})")
                            else:
                                pod_progress = 1.0
                                running_info.append(f"{pod_name}(종료)")
                        else:
                            pod_progress = 0.0
                            running_info.append(f"{pod_name}(상태체크실패)")

                        total_progress += pod_progress

                    group_progress = (total_progress / len(pod_list)) * 100
                    debug_print(f"⏳ Step {step.step_order} 진행률: {group_progress:.1f}% ({len(completed_pods)}/{len(pod_list)}) | 진행중: {', '.join(running_info)}")

                    # stop_event 감지
                    if stop_event.is_set():
                        debug_print(f"⏹️ 중지 이벤트 감지 - 스텝 {step.step_order} 즉시 종료")
                        for t in pod_tasks.keys():
                            t.cancel()
                        await asyncio.gather(*pod_tasks.keys(), return_exceptions=True)
                        total_execution_summary["simulation_status"] = "CANCELLED"
                        return total_execution_summary

                    await asyncio.sleep(poll_interval)

                # 스텝 완료 처리
                step_execution_time = (datetime.now(timezone.utc) - step_start_time).total_seconds()
                execution_summary = self.rosbag_executor.get_execution_summary([
                    task.result() for task in done_tasks if not isinstance(task.result(), Exception)
                ])
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
            total_execution_summary["simulation_status"] = "COMPLETED"
            await self._update_simulation_status_and_log(simulation_id, "COMPLETED", "모든 스텝 성공")
            debug_print(f"🎉 시뮬레이션 {simulation_id} 완료")
            return total_execution_summary

        except asyncio.CancelledError:
            debug_print(f"🛑 시뮬레이션 {simulation_id} 태스크 취소됨")
            await self._handle_simulation_stop(simulation_id, "태스크 취소로 인한 중지")
            raise
        except Exception as e:
            debug_print(f"❌ 시뮬레이션 {simulation_id} 실행 중 예외: {e}")
            await self._update_simulation_status_and_log(simulation_id, "FAILED", str(e))
            raise
        finally:
            self._cleanup_simulation(simulation_id)

      
    
    async def _run_parallel_simulation_with_progress(self, simulation_id: int, stop_event: asyncio.Event):
        """
        병렬 패턴 시뮬레이션 실행 - 즉시 취소 + 실시간 진행상황
        """
        debug_print("🚀 병렬 시뮬레이션 실행 시작", simulation_id=simulation_id)

        # 1️⃣ 시뮬레이션 조회
        simulation = await self.find_simulation_by_id(simulation_id, "background parallel run")
        debug_print("✅ 시뮬레이션 조회 완료", simulation_id=simulation.id)

        groups = await self.repository.find_simulation_groups(simulation_id)
        debug_print("✅ 그룹 조회 완료", simulation_id=simulation_id, group_count=len(groups))

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

        # 2️⃣ 모든 그룹 병렬 실행
        group_tasks = [
            asyncio.create_task(self._execute_single_group_with_progress(simulation, group))
            for group in groups
        ]

        debug_print("🎯 모든 그룹 병렬 실행 시작", simulation_id=simulation_id, total_groups=len(groups))

        # 3️⃣ 실행 중 진행상황 감시 + 즉시 취소 처리
        poll_interval = 1.0
        while group_tasks:
            done, pending = await asyncio.wait(group_tasks, timeout=poll_interval, return_when=asyncio.ALL_COMPLETED)

            # 완료된 그룹 처리
            for t in done:
                try:
                    group_result = t.result()
                except asyncio.CancelledError:
                    debug_print("🛑 그룹 CancelledError 감지")
                    group_result = {
                        "group_id": getattr(t, "group_id", None),
                        "status": "cancelled",
                        "total_pod_count": 0,
                        "success_pod_count": 0,
                        "failed_pod_count": 0,
                        "failure_reason": "사용자 요청으로 중지"
                    }

                total_summary["group_results"].append(group_result)

                if group_result["status"] == "success":
                    total_summary["completed_groups"] += 1
                    total_summary["total_success_pods"] += group_result["success_pod_count"]
                elif group_result["status"] == "failed":
                    total_summary["failed_groups"] += 1
                    total_summary["total_failed_pods"] += group_result.get("failed_pod_count", 0)
                    if not total_summary["failure_reason"]:
                        total_summary["failure_reason"] = group_result.get("failure_reason")
                elif group_result["status"] == "cancelled":
                    debug_print("🛑 그룹 취소 감지", group_id=group_result["group_id"])

                total_summary["total_pods_executed"] += group_result["total_pod_count"]
                group_tasks.remove(t)

            # stop_event 감지 시 남은 모든 그룹 취소
            if stop_event.is_set():
                debug_print("🛑 시뮬레이션 중지 감지, 남은 그룹 취소 시작",
                            pending_groups=len(pending),
                            total_groups=len(group_tasks) + len(done))
                
                # pending에 남아 있는 그룹 태스크 리스트 확인
                tasks_to_cancel = list(pending)
                debug_print(f"⚡ cancel 대상 그룹 태스크: {len(tasks_to_cancel)}개",
                            task_ids=[id(t) for t in tasks_to_cancel],
                            task_names=[getattr(t, 'get_name', lambda: 'unnamed')() for t in tasks_to_cancel])
                
                # 각 그룹 태스크 cancel 호출
                for t in tasks_to_cancel:
                    t.cancel()
                    debug_print(f"🔹 그룹 태스크 cancel 호출: id={id(t)} name={getattr(t, 'get_name', lambda: 'unnamed')()}")

                # 모든 cancel 호출 완료 후 gather로 안전하게 종료 대기
                if tasks_to_cancel:
                    debug_print("⏳ asyncio.gather로 남은 그룹 태스크 종료 대기 중...")
                    await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
                    debug_print("✅ 모든 그룹 태스크 cancel 완료")

                # 시뮬레이션 상태 갱신
                total_summary["simulation_status"] = "CANCELLED"
                debug_print("🛑 시뮬레이션 stop_event 처리 완료, total_summary 상태 업데이트", 
                            completed_groups=total_summary["completed_groups"],
                            failed_groups=total_summary["failed_groups"],
                            total_pods_executed=total_summary["total_pods_executed"])
                
                # ✅ _cleanup_simulation 호출
                sim_state = self.state.running_simulations.get(simulation_id)
                debug_print("🧹 _cleanup_simulation 호출 전 상태 확인", simulation_id=simulation_id, running_simulation_entry=sim_state)
                self._cleanup_simulation(simulation_id)
                sim_state_after = self.state.running_simulations.get(simulation_id)
                debug_print("🧹 _cleanup_simulation 호출 후 상태 확인", simulation_id=simulation_id, running_simulation_entry=sim_state_after)
                
                break  # while 루프 종료


            # 실시간 진행상황 로그
            running_groups = len(group_tasks)
            debug_print(f"📊 진행상황: 완료 {total_summary['completed_groups']}/{len(groups)}, 남은 그룹 {running_groups}")

        # 4️⃣ 최종 상태 결정
        if total_summary["simulation_status"] != "CANCELLED":
            if total_summary["failed_groups"] > 0:
                total_summary["simulation_status"] = "FAILED"
                reason = total_summary["failure_reason"] or f"{total_summary['failed_groups']}개 그룹 실패"
                await self._update_simulation_status_and_log(simulation_id, "FAILED", reason)
            else:
                total_summary["simulation_status"] = "COMPLETED"
                await self._update_simulation_status_and_log(simulation_id, "COMPLETED", "모든 그룹 완료")

        debug_print("🎉 병렬 시뮬레이션 실행 완료", simulation_id=simulation_id)
        return total_summary

    async def _execute_single_group_with_progress(self, simulation, group):
        """
        단일 그룹 실행 - 실시간 1초 단위 진행상황 출력 + stop_event 즉시 취소
        - Pod별 반복 횟수(current_loop/max_loops) 반영
        - 그룹 진행률 계산 및 표시
        """
        debug_print("🔸 그룹 실행 시작", group_id=group.id, simulation_id=simulation.id)
        start_time = datetime.now(timezone.utc)

        try:
            # 1️⃣ 그룹 Pod 조회
            pod_list = self.pod_service.get_pods_by_filter(
                namespace=simulation.namespace,
                filter_params=GroupIdFilter(group_id=group.id)
            )
            debug_print("✅ 그룹 Pod 조회 완료", group_id=group.id, pod_count=len(pod_list))

            if not pod_list:
                failure_reason = f"그룹 {group.id}에서 Pod를 찾을 수 없음"
                return {
                    "group_id": group.id,
                    "status": "failed",
                    "execution_time": (datetime.now(timezone.utc) - start_time).total_seconds(),
                    "total_pod_count": 0,
                    "success_pod_count": 0,
                    "failed_pod_count": 0,
                    "failure_reason": failure_reason
                }

            # 2️⃣ Pod 실행
            pod_tasks = {}
            for pod in pod_list:
                task = asyncio.create_task(self.rosbag_executor.execute_single_pod(pod, simulation, group))
                task.latest_status = {}  # 최신 상태 저장용
                pod_tasks[task] = pod.metadata.name

            total_pods = len(pod_tasks)
            completed_pods = set()
            poll_interval = 1  # 1초 단위 진행상황 확인

            # 3️⃣ Pod 진행상황 루프
            while pod_tasks:
                done_tasks = [t for t in pod_tasks if t.done()]

                # 3-1️⃣ 완료된 Pod 처리
                for task in done_tasks:
                    pod_name = pod_tasks.pop(task)
                    try:
                        result = task.result()
                        completed_pods.add(result.pod_name)
                        debug_print(f"✅ Pod 완료: {result.pod_name} ({len(completed_pods)}/{total_pods})")
                    except asyncio.CancelledError:
                        debug_print(f"🛑 Pod CancelledError 감지: {pod_name}")
                    except Exception as e:
                        debug_print(f"💥 Pod 실행 실패: {pod_name}: {e}")

                # 3-2️⃣ 진행 중 Pod 상태 체크 및 그룹 진행률 계산
                total_progress = 0.0
                running_info = []

                # 전체 Pod 기준으로 상태 조회
                status_tasks = {
                    pod.metadata.name: asyncio.create_task(self.rosbag_executor._check_pod_rosbag_status(pod))
                    for pod in pod_list
                }

                # 상태 조회 완료
                pod_statuses = await asyncio.gather(*status_tasks.values(), return_exceptions=True)

                for pod_name, status in zip(status_tasks.keys(), pod_statuses):
                    if pod_name in completed_pods:
                        pod_progress = 1.0
                        running_info.append(f"{pod_name}(완료)")
                    elif isinstance(status, dict):
                        is_playing = status.get("isPlaying", True)
                        current_loop = status.get("current_loop", 0)
                        max_loops = max(status.get("max_loops", 1), 1)
                        pod_progress = min(current_loop / max_loops, 1.0)
                        if is_playing:
                            running_info.append(f"{pod_name}({current_loop}/{max_loops})")
                        else:
                            pod_progress = 1.0
                            running_info.append(f"{pod_name}(종료)")
                    else:
                        pod_progress = 0.0
                        running_info.append(f"{pod_name}(상태체크실패)")

                    total_progress += pod_progress

                # 그룹 전체 진행률 계산
                group_progress = (total_progress / total_pods) * 100
                debug_print(f"⏳ 그룹 진행률: {group_progress:.1f}% | 완료 {len(completed_pods)}/{total_pods} | 진행중: {', '.join(running_info)}")

                await asyncio.sleep(poll_interval)


            # 4️⃣ 모든 Pod 완료 시 summary
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            success_count = len(completed_pods)
            failed_count = total_pods - success_count
            status = "success" if failed_count == 0 else "failed"

            return {
                "group_id": group.id,
                "status": status,
                "execution_time": execution_time,
                "total_pod_count": total_pods,
                "success_pod_count": success_count,
                "failed_pod_count": failed_count,
            }

        except asyncio.CancelledError:
            debug_print(f"🛑 그룹 Task CancelledError 감지", group_id=group.id)

            # 남은 Pod Task cancel + gather
            for t in pod_tasks.keys():
                t.cancel()
            await asyncio.gather(*pod_tasks.keys(), return_exceptions=True)

            # cancelled 결과 반환
            return {
                "group_id": group.id,
                "status": "cancelled",
                "execution_time": (datetime.now(timezone.utc) - start_time).total_seconds(),
                "total_pod_count": total_pods,
                "success_pod_count": len(completed_pods),
                "failed_pod_count": len(pod_tasks),
                "failure_reason": "사용자 요청으로 중지"
            }

        except Exception as e:
            debug_print(f"💥 그룹 실행 중 예외 발생: {e}", group_id=group.id)
            return {
                "group_id": group.id,
                "status": "failed",
                "execution_time": (datetime.now(timezone.utc) - start_time).total_seconds(),
                "total_pod_count": 0,
                "success_pod_count": 0,
                "failed_pod_count": 0,
                "failure_reason": str(e)
            }

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
            await self.ros_service.send_post_request(pod_ip, "/rosbag/stop")

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

    async def find_simulation_by_id(self, simulation_id: int, api: str):
        simulation = await self.repository.find_by_id(simulation_id)

        if not simulation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{api}: 존재하지 않는 시뮬레이션id 입니다.",
            )
        return simulation

    async def get_simulation_status(self, simulation):
        instances = simulation.instances

        if not instances:
            return SimulationStatus.EMPTY.value

        for instance in instances:
            pod_ip = await self.pod_service.get_pod_ip(instance)
            pod_status = await self.ros_service.get_pod_status(pod_ip)

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
                status_response = await self.ros_service.get_pod_status(pod_ip)
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
                "stoppedAt": datetime.now(timezone.utc).isoformat()
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
                "stoppedAt": datetime.now(timezone.utc).isoformat()
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
                    is_playing = status.get("isPlaying", True)
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

