import asyncio
from datetime import datetime, timezone
import traceback
from typing import Tuple, List, Optional
from fastapi import HTTPException, status
from sqlalchemy import select, exists, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload
from starlette.status import HTTP_409_CONFLICT

from utils.debug_print import debug_print
from utils.rosbag_executor import RosbagExecutor
from schemas.simulation_detail import CurrentStatusInitiating, CurrentStatusReady, ExecutionPlanParallel, ExecutionPlanSequential, GroupModel, ProgressModel, SimulationData, StepModel, TimestampModel
from schemas.pod import GroupIdFilter, StepOrderFilter
from repositories.simulation_repository import SimulationRepository
from schemas.pagination import PaginationMeta, PaginationParams
from models.enums import PatternType, SimulationStatus
from utils.simulation_background import (
    handle_parallel_pattern_background,
    handle_sequential_pattern_background,
)
from .template import TemplateService

from .pod import PodService
from .rosbag import RosService
from models.instance import Instance
from models.simulation import Simulation
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
    def __init__(self, session: AsyncSession, sessionmaker: async_sessionmaker, repository: SimulationRepository):
        self.session = session
        self.sessionmaker = sessionmaker
        self.repository = repository
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

            simulation_data = {
                "id": simulation.id,
                "name": simulation.name,
                "pattern_type": simulation.pattern_type
            }
            
            debug_print("✅ 시뮬레이션 조회 완료", 
                    simulation_id=simulation_data["id"], 
                    name=simulation_data["name"], 
                    pattern_type=simulation_data["pattern_type"])

            # 패턴 타입에 따른 분기 처리 (simulation_data 사용)
            if simulation_data["pattern_type"] == "sequential":
                pattern_name = "순차"
                background_task = self._run_sequential_simulation(simulation_id)
                debug_print("🔄 순차 패턴 선택", simulation_id=simulation_id)
            elif simulation_data["pattern_type"] == "parallel":
                pattern_name = "병렬"
                background_task = self._run_parallel_simulation(simulation_id)
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

            debug_print("📤 API 응답 반환", simulation_id=simulation_id)
            return {
                "simulation_id": simulation_id,
                "status": "RUNNING",
                "pattern_type": simulation_data["pattern_type"],
                "message": f"{pattern_name} 시뮬레이션 실행을 시작했습니다."
            }
            
        except Exception as e:
            traceback.print_stack()
            debug_print("💥 시뮬레이션 시작 중 예외 발생", simulation_id=simulation_id, error=str(e), error_type=type(e).__name__)
            raise
    
    async def _run_sequential_simulation(self, simulation_id: int):
        """
        기존 start_sequential_simulation() 로직을 그대로 사용하되,
        각 스텝 실행을 비동기 Task로 처리
        """
        print(f"백그라운드에서 시뮬레이션 실행 시작: {simulation_id}")
        

        # 1. 스텝 조회
        simulation = await self.find_simulation_by_id(simulation_id, "background run")
        steps = await self.repository.find_simulation_steps(simulation_id)
        print(f"📊 스텝 조회 완료: {len(steps)}개")

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

        for i, step in enumerate(steps, 1):
            print(f"\n🔄 스텝 {i}/{len(steps)} 처리 시작 - Step ID: {step.id}")

            step_start_time = datetime.now(timezone.utc)

            try:
                # Pod 조회
                pod_list = self.pod_service.get_pods_by_filter(
                    namespace=simulation.namespace,
                    filter_params=StepOrderFilter(step_order=step.step_order)
                )

                if not pod_list:
                    failure_reason = f"스텝 {step.step_order}에서 Pod를 찾을 수 없음"
                    print(f"❌ {failure_reason}")
                    total_execution_summary["failed_steps"] += 1
                    total_execution_summary["simulation_status"] = "FAILED"
                    total_execution_summary["failure_reason"] = failure_reason
                    await self._update_simulation_status_and_log(simulation_id, "FAILED", failure_reason)
                    return total_execution_summary

                # RosbagExecutor 실행
                execution_results = await self.rosbag_executor.execute_rosbag_parallel_pods(
                    pods=pod_list,
                    simulation=simulation,
                    step=step
                )

                execution_summary = self.rosbag_executor.get_execution_summary(execution_results)
                step_execution_time = (datetime.now(timezone.utc) - step_start_time).total_seconds()

                failed_pod_count = execution_summary['failed_count'] + execution_summary['timeout_count']

                if failed_pod_count > 0:
                    failure_details = [
                        f"{r.pod_name}({r.message})"
                        for r in execution_results if r.pod_name in execution_summary['failed_pods']
                    ]
                    failure_reason = f"스텝 {step.step_order}에서 {failed_pod_count}개 Pod 실패: {', '.join(failure_details)}"
                    print(f"❌ {failure_reason}")

                    total_execution_summary.update({
                        "total_pods_executed": execution_summary['total_pods'],
                        "total_success_pods": execution_summary['success_count'],
                        "total_failed_pods": failed_pod_count,
                        "failed_steps": total_execution_summary['failed_steps'] + 1,
                        "simulation_status": "FAILED",
                        "failure_reason": failure_reason
                    })

                    total_execution_summary["step_results"].append({
                        "step_id": step.id,
                        "step_order": step.step_order,
                        "status": "failed",
                        "execution_summary": execution_summary,
                        "execution_time": step_execution_time,
                        "pod_count": len(pod_list),
                        "failure_reason": failure_reason
                    })

                    await self._update_simulation_status_and_log(simulation_id, "FAILED", failure_reason)
                    return total_execution_summary

                # 성공 처리
                print(f"✅ 스텝 {step.step_order} 완료")
                total_execution_summary["completed_steps"] += 1
                total_execution_summary["total_pods_executed"] += execution_summary['total_pods']
                total_execution_summary["total_success_pods"] += execution_summary['success_count']

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

            except Exception as e:
                failure_reason = f"스텝 {step.step_order} 처리 중 오류: {str(e)}"
                print(f"❌ {failure_reason}")
                total_execution_summary["failed_steps"] += 1
                total_execution_summary["simulation_status"] = "FAILED"
                total_execution_summary["failure_reason"] = failure_reason
                await self._update_simulation_status_and_log(simulation_id, "FAILED", failure_reason)
                return total_execution_summary

        # 모든 스텝 성공
        total_execution_summary["simulation_status"] = "COMPLETED"
        await self._update_simulation_status_and_log(simulation_id, "COMPLETED", "모든 스텝 성공")
        print(f"🎉 시뮬레이션 {simulation_id} 완료")
        return total_execution_summary    
    
    async def _run_parallel_simulation(self, simulation_id: int):
        """
        병렬 패턴 시뮬레이션 실행 로직
        모든 그룹을 병렬 실행하며, 각 그룹 내 Pod들도 병렬 실행
        """
        debug_print("🚀 병렬 시뮬레이션 백그라운드 실행 시작", simulation_id=simulation_id)
        
        try:
            # 1. 시뮬레이션 조회
            debug_print("📋 백그라운드에서 시뮬레이션 재조회 시작", simulation_id=simulation_id)
            simulation = await self.find_simulation_by_id(simulation_id, "background parallel run")
            debug_print("✅ 백그라운드 시뮬레이션 조회 완료", simulation_id=simulation.id)
            
            debug_print("📋 그룹 조회 시작", simulation_id=simulation_id)
            groups = await self.repository.find_simulation_groups(simulation_id)
            debug_print("✅ 그룹 조회 완료", simulation_id=simulation_id, group_count=len(groups))

            total_execution_summary = {
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

            # 모든 그룹을 병렬로 실행
            debug_print("🎯 그룹 병렬 태스크 생성 시작", simulation_id=simulation_id, total_groups=len(groups))
            group_tasks = []
            for i, group in enumerate(groups):
                debug_print("🔧 그룹 태스크 생성", simulation_id=simulation_id, group_id=group.id, group_index=i)
                task = asyncio.create_task(self._execute_single_group(simulation, group))
                task.set_name(f"group_{group.id}_sim_{simulation_id}")
                group_tasks.append((group, task))
                debug_print("✅ 그룹 태스크 생성 완료", 
                        simulation_id=simulation_id, 
                        group_id=group.id, 
                        task_name=task.get_name(),
                        task_id=id(task))

            debug_print("🚀 모든 그룹 병렬 실행 시작", simulation_id=simulation_id, total_tasks=len(group_tasks))

            # 모든 그룹 완료 대기
            for i, (group, task) in enumerate(group_tasks):
                debug_print("⏳ 그룹 태스크 완료 대기 시작", 
                        simulation_id=simulation_id, 
                        group_id=group.id, 
                        task_index=i,
                        task_name=task.get_name())
                
                try:
                    group_result = await task
                    debug_print("✅ 그룹 태스크 완료", 
                            simulation_id=simulation_id, 
                            group_id=group.id, 
                            status=group_result["status"],
                            success_pods=group_result["success_pod_count"],
                            failed_pods=group_result["failed_pod_count"])
                    
                    total_execution_summary["group_results"].append(group_result)
                    
                    if group_result["status"] == "success":
                        total_execution_summary["completed_groups"] += 1
                        total_execution_summary["total_success_pods"] += group_result["success_pod_count"]
                        debug_print("✅ 그룹 성공 처리 완료", simulation_id=simulation_id, group_id=group.id)
                    else:
                        total_execution_summary["failed_groups"] += 1
                        total_execution_summary["total_failed_pods"] += group_result["failed_pod_count"]
                        
                        if not total_execution_summary["failure_reason"]:
                            total_execution_summary["failure_reason"] = group_result["failure_reason"]
                        
                        debug_print("❌ 그룹 실패 처리 완료", 
                                simulation_id=simulation_id, 
                                group_id=group.id,
                                failure_reason=group_result["failure_reason"])
                    
                    total_execution_summary["total_pods_executed"] += group_result["total_pod_count"]
                    
                except Exception as e:
                    debug_print("💥 그룹 태스크 실행 중 예외 발생", 
                            simulation_id=simulation_id, 
                            group_id=group.id,
                            error=str(e),
                            error_type=type(e).__name__)
                    
                    failure_reason = f"그룹 {group.id} 실행 중 예외 발생: {str(e)}"
                    total_execution_summary["failed_groups"] += 1
                    
                    if not total_execution_summary["failure_reason"]:
                        total_execution_summary["failure_reason"] = failure_reason

            debug_print("📊 모든 그룹 결과 수집 완료", 
                    simulation_id=simulation_id,
                    completed_groups=total_execution_summary["completed_groups"],
                    failed_groups=total_execution_summary["failed_groups"])

            # 최종 실행 결과 평가
            if total_execution_summary["failed_groups"] > 0:
                debug_print("❌ 시뮬레이션 실패로 판정", simulation_id=simulation_id, failed_groups=total_execution_summary["failed_groups"])
                total_execution_summary["simulation_status"] = "FAILED"
                failure_reason = total_execution_summary["failure_reason"] or f"{total_execution_summary['failed_groups']}개 그룹 실패"
                
                debug_print("📝 실패 상태 업데이트 시작", simulation_id=simulation_id, reason=failure_reason)
                await self._update_simulation_status_and_log(simulation_id, "FAILED", failure_reason)
                debug_print("✅ 실패 상태 업데이트 완료", simulation_id=simulation_id)
            else:
                debug_print("✅ 시뮬레이션 성공으로 판정", simulation_id=simulation_id)
                total_execution_summary["simulation_status"] = "COMPLETED"
                
                debug_print("📝 완료 상태 업데이트 시작", simulation_id=simulation_id)
                await self._update_simulation_status_and_log(simulation_id, "COMPLETED", "모든 그룹 완료")
                debug_print("✅ 완료 상태 업데이트 완료", simulation_id=simulation_id)

            debug_print("🎉 병렬 시뮬레이션 백그라운드 실행 완료", simulation_id=simulation_id)
            return total_execution_summary

        except Exception as e:
            debug_print("💥 병렬 시뮬레이션 실행 중 치명적 예외 발생", 
                    simulation_id=simulation_id, 
                    error=str(e),
                    error_type=type(e).__name__)
            raise

        
    async def _execute_single_group(self, simulation, group):
        """
        단일 그룹을 실행하는 헬퍼 메서드
        그룹에 소속된 모든 Pod들을 병렬로 실행
        """
        debug_print("🔸 그룹 실행 시작", group_id=group.id, simulation_id=simulation.id)
    
        group_start_time = datetime.now(timezone.utc)
        
        try:
            debug_print("🔍 그룹 Pod 조회 시작", group_id=group.id, namespace=simulation.namespace)
            pod_list = self.pod_service.get_pods_by_filter(
                namespace=simulation.namespace,
                filter_params=GroupIdFilter(group_id=group.id)
            )
            debug_print("✅ 그룹 Pod 조회 완료", group_id=group.id, pod_count=len(pod_list) if pod_list else 0)

            if not pod_list:
                failure_reason = f"그룹 {group.id}에서 Pod를 찾을 수 없음"
                debug_print("❌ Pod 없음으로 그룹 실패", group_id=group.id, reason=failure_reason)
                return {
                    "group_id": group.id,
                    "status": "failed",
                    "execution_time": (datetime.now(timezone.utc) - group_start_time).total_seconds(),
                    "total_pod_count": 0,
                    "success_pod_count": 0,
                    "failed_pod_count": 0,
                    "failure_reason": failure_reason
                }

            debug_print("🎯 RosbagExecutor 병렬 실행 시작", group_id=group.id, pod_count=len(pod_list))
            execution_results = await self.rosbag_executor.execute_rosbag_parallel_pods(
                pods=pod_list,
                simulation=simulation,
                group=group
            )
            debug_print("✅ RosbagExecutor 병렬 실행 완료", group_id=group.id, result_count=len(execution_results))

            execution_summary = self.rosbag_executor.get_execution_summary(execution_results)
            group_execution_time = (datetime.now(timezone.utc) - group_start_time).total_seconds()

            failed_pod_count = execution_summary['failed_count'] + execution_summary['timeout_count']

            if failed_pod_count > 0:
                failure_details = [
                    f"{r.pod_name}({r.message})"
                    for r in execution_results if r.pod_name in execution_summary['failed_pods']
                ]
                failure_reason = f"그룹 {group.id}에서 {failed_pod_count}개 Pod 실패: {', '.join(failure_details)}"
                debug_print("❌ 그룹 실행 실패", group_id=group.id, failed_pod_count=failed_pod_count, reason=failure_reason)

                return {
                    "group_id": group.id,
                    "status": "failed",
                    "execution_time": group_execution_time,
                    "total_pod_count": len(pod_list),
                    "success_pod_count": execution_summary['success_count'],
                    "failed_pod_count": failed_pod_count,
                    "failure_reason": failure_reason,
                    "execution_summary": execution_summary
                }

            debug_print("✅ 그룹 실행 성공", 
                    group_id=group.id, 
                    pod_count=len(pod_list),
                    execution_time=group_execution_time)
            return {
                "group_id": group.id,
                "status": "success",
                "execution_time": group_execution_time,
                "total_pod_count": len(pod_list),
                "success_pod_count": execution_summary['success_count'],
                "failed_pod_count": 0,
                "execution_summary": execution_summary
            }

        except Exception as e:
            debug_print("💥 그룹 실행 중 예외 발생", 
                    group_id=group.id, 
                    error=str(e),
                    error_type=type(e).__name__)
            failure_reason = f"그룹 {group.id} 실행 중 예외 발생: {str(e)}"
            return {
                "group_id": group.id,
                "status": "failed",
                "execution_time": (datetime.now(timezone.utc) - group_start_time).total_seconds(),
                "total_pod_count": 0,
                "success_pod_count": 0,
                "failed_pod_count": 0,
                "failure_reason": failure_reason
            }

        
    async def start_sequential_simulation(self, simulation_id: int):
        print(f"🚀 시뮬레이션 시작 요청: simulation_id={simulation_id}")
        
        # 1. 시뮬레이션 조회
        simulation = await self.find_simulation_by_id(simulation_id, "start simulation")
        print(f"✅ 시뮬레이션 조회 완료:")
        print(f"   - ID: {simulation.id}")
        print(f"   - 이름: {simulation.name}")
        print(f"   - 패턴: {simulation.pattern_type}")
        print(f"   - 네임스페이스: {simulation.namespace}")
        print(f"   - 상태: {simulation.status}")
        
        # 2. 패턴 타입별 분기
        if simulation.pattern_type == PatternType.SEQUENTIAL:
            print(f"📋 SEQUENTIAL 패턴 처리 시작")
            print(f"⚠️  실패 정책: Pod 1개라도 실패 시 즉시 시뮬레이션 중단")
            
            # 3. 시뮬레이션 스텝들 조회
            steps = await self.repository.find_simulation_steps(simulation_id)
            print(f"📊 시뮬레이션 스텝 조회 완료: {len(steps)}개 스텝")
            
            # 전체 시뮬레이션 실행 결과 추적
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
            
            # 4. 각 스텝 순차 실행
            for i, step in enumerate(steps, 1):
                print(f"\n🔄 스텝 {i}/{len(steps)} 처리 중:")
                print(f"   - Step ID: {step.id}")
                print(f"   - Step Order: {step.step_order}")
                print(f"   - Repeat Count: {step.repeat_count}")
                print(f"   - Execution Time: {step.execution_time}")
                
                step_start_time = datetime.now(timezone.utc)
                
                # 5. 해당 스텝의 Pod 목록 조회
                namespace = simulation.namespace
                print(f"🔍 Pod 조회 시작 - 네임스페이스: {namespace}, step_order: {step.step_order}")
                
                try:
                    # PodService 인스턴스 생성 및 조회
                    pod_list = self.pod_service.get_pods_by_filter(
                        namespace=namespace,
                        filter_params=StepOrderFilter(step_order=step.step_order)
                    )
                    
                    print(f"✅ Pod 조회 완료: {len(pod_list)}개 Pod 발견")
                    
                    # Pod 목록 상세 출력
                    if pod_list:
                        for j, pod in enumerate(pod_list, 1):
                            print(f"   📦 Pod {j}: {pod.metadata.name}")
                            print(f"      - 상태: {pod.status.phase}")
                            print(f"      - IP: {pod.status.pod_ip or 'N/A'}")
                            print(f"      - 노드: {pod.spec.node_name}")
                            if pod.metadata.labels:
                                relevant_labels = {k: v for k, v in pod.metadata.labels.items() 
                                                if 'step' in k or 'group' in k}
                                if relevant_labels:
                                    print(f"      - 관련 라벨: {relevant_labels}")
                    else:
                        # Pod가 없는 경우 즉시 실패 처리
                        step_execution_time = datetime.now(timezone.utc) - step_start_time
                        failure_reason = f"스텝 {step.step_order}에서 Pod를 찾을 수 없음"
                        
                        print(f"❌ 시뮬레이션 즉시 실패: {failure_reason}")
                        
                        total_execution_summary["failed_steps"] += 1
                        total_execution_summary["simulation_status"] = "FAILED"
                        total_execution_summary["failure_reason"] = failure_reason
                        total_execution_summary["step_results"].append({
                            "step_id": step.id,
                            "step_order": step.step_order,
                            "status": "failed",
                            "reason": "no_pods_found",
                            "execution_time": step_execution_time
                        })
                        
                        # 시뮬레이션 상태 업데이트
                        await self._update_simulation_status_and_log(simulation_id, "FAILED", failure_reason)
                        return total_execution_summary
                    
                    # 6. RosbagExecutor를 사용하여 병렬 실행
                    print(f"\n🎯 스텝 {step.step_order}에서 {len(pod_list)}개 Pod 병렬 rosbag 실행 시작")
                    
                    try:
                        # RosbagExecutor 인스턴스 사용
                        execution_results = await self.rosbag_executor.execute_rosbag_parallel_pods(
                            pods=pod_list,
                            simulation=simulation,
                            step=step
                        )
                        
                        # 7. 실행 결과 처리 - 실패 즉시 감지
                        execution_summary = self.rosbag_executor.get_execution_summary(execution_results)
                        step_execution_time = (datetime.now(timezone.utc)- step_start_time).total_seconds()
                        
                        print(f"📊 스텝 {step.step_order} 실행 결과:")
                        print(f"   - 총 Pod 수: {execution_summary['total_pods']}")
                        print(f"   - 성공: {execution_summary['success_count']}")
                        print(f"   - 실패: {execution_summary['failed_count']}")
                        print(f"   - 타임아웃: {execution_summary['timeout_count']}")
                        print(f"   - 평균 실행 시간: {execution_summary['average_execution_time']}초")
                        
                        # 🚨 중요: Pod 1개라도 실패하면 즉시 시뮬레이션 중단
                        failed_pod_count = execution_summary['failed_count'] + execution_summary['timeout_count']
                        
                        if failed_pod_count > 0:
                            # 실패한 Pod 상세 정보 출력
                            print(f"❌ 실패한 Pod 목록:")
                            failure_details = []
                            for failed_pod in execution_summary['failed_pods']:
                                failed_result = next((r for r in execution_results if r.pod_name == failed_pod), None)
                                if failed_result:
                                    print(f"   - {failed_pod}: {failed_result.message}")
                                    failure_details.append(f"{failed_pod}({failed_result.message})")
                            
                            failure_reason = f"스텝 {step.step_order}에서 {failed_pod_count}개 Pod 실행 실패: {', '.join(failure_details)}"
                            
                            print(f"\n🛑 시뮬레이션 즉시 중단: Pod 실행 실패 감지")
                            print(f"   실패 원인: {failure_reason}")
                            
                            # 전체 요약에 현재까지 결과 추가
                            total_execution_summary["total_pods_executed"] += execution_summary['total_pods']
                            total_execution_summary["total_success_pods"] += execution_summary['success_count']
                            total_execution_summary["total_failed_pods"] += failed_pod_count
                            total_execution_summary["failed_steps"] += 1
                            total_execution_summary["simulation_status"] = "FAILED"
                            total_execution_summary["failure_reason"] = failure_reason
                            
                            total_execution_summary["step_results"].append({
                                "step_id": step.id,
                                "step_order": step.step_order,
                                "status": "failed",
                                "execution_summary": execution_summary,
                                "execution_time": step_execution_time,
                                "pod_count": len(pod_list),
                                "failure_reason": failure_reason
                            })
                            
                            # 시뮬레이션 상태 업데이트 및 즉시 종료
                            await self._update_simulation_status_and_log(simulation_id, "FAILED", failure_reason)
                            return total_execution_summary
                        
                        # 모든 Pod가 성공한 경우
                        print(f"✅ 스텝 {step.step_order} 실행 성공 ({step_execution_time:.2f}초)")
                        print(f"   🎉 모든 {execution_summary['total_pods']}개 Pod 성공적으로 완료")
                        
                        # 전체 요약에 추가
                        total_execution_summary["total_pods_executed"] += execution_summary['total_pods']
                        total_execution_summary["total_success_pods"] += execution_summary['success_count']
                        total_execution_summary["completed_steps"] += 1
                        
                        total_execution_summary["step_results"].append({
                            "step_id": step.id,
                            "step_order": step.step_order,
                            "status": "success",
                            "execution_summary": execution_summary,
                            "execution_time": step_execution_time,
                            "pod_count": len(pod_list)
                        })
                        
                        # 8. 스텝 간 지연 시간 적용 (마지막 스텝이 아닌 경우)
                        if i < len(steps) and step.delay_after_completion and step.delay_after_completion > 0:
                            print(f"⏱️  다음 스텝까지 {step.delay_after_completion}초 대기...")
                            await asyncio.sleep(step.delay_after_completion)
                    
                    except Exception as rosbag_error:
                        step_execution_time = (datetime.now(timezone.utc) - step_start_time).total_seconds()
                        failure_reason = f"스텝 {step.step_order}에서 rosbag 실행 오류: {str(rosbag_error)}"
                        
                        print(f"❌ 시뮬레이션 즉시 실패: {failure_reason}")
                        
                        total_execution_summary["failed_steps"] += 1
                        total_execution_summary["simulation_status"] = "FAILED"
                        total_execution_summary["failure_reason"] = failure_reason
                        total_execution_summary["step_results"].append({
                            "step_id": step.id,
                            "step_order": step.step_order,
                            "status": "error",
                            "error_message": str(rosbag_error),
                            "execution_time": step_execution_time,
                            "pod_count": len(pod_list) if 'pod_list' in locals() else 0
                        })
                        
                        # 시뮬레이션 상태 업데이트 및 즉시 종료
                        await self._update_simulation_status_and_log(simulation_id, "FAILED", failure_reason)
                        return total_execution_summary
                    
                except Exception as pod_error:
                    step_execution_time = (datetime.now(timezone.utc) - step_start_time).total_seconds()
                    failure_reason = f"스텝 {step.step_order}에서 Pod 조회 실패: {str(pod_error)}"
                    
                    print(f"❌ 시뮬레이션 즉시 실패: {failure_reason}")
                    print(f"   네임스페이스: {namespace}")
                    print(f"   라벨 셀렉터: step-order={step.step_order}")
                    
                    total_execution_summary["failed_steps"] += 1
                    total_execution_summary["simulation_status"] = "FAILED"
                    total_execution_summary["failure_reason"] = failure_reason
                    total_execution_summary["step_results"].append({
                        "step_id": step.id,
                        "step_order": step.step_order,
                        "status": "error",
                        "error_message": f"Pod 조회 실패: {str(pod_error)}",
                        "execution_time": step_execution_time
                    })
                    
                    # 시뮬레이션 상태 업데이트 및 즉시 종료
                    await self._update_simulation_status_and_log(simulation_id, "FAILED", failure_reason)
                    return total_execution_summary
            
            # 9. 모든 스텝이 성공적으로 완료된 경우
            total_execution_summary["simulation_status"] = "COMPLETED"
            
            print(f"\n🎉 SEQUENTIAL 시뮬레이션 {simulation_id} 성공적으로 완료!")
            print(f"📊 최종 실행 요약:")
            print(f"   - 총 스텝 수: {total_execution_summary['total_steps']}")
            print(f"   - 완료된 스텝: {total_execution_summary['completed_steps']}")
            print(f"   - 실패한 스텝: {total_execution_summary['failed_steps']}")
            print(f"   - 총 실행 Pod 수: {total_execution_summary['total_pods_executed']}")
            print(f"   - 총 성공 Pod 수: {total_execution_summary['total_success_pods']}")
            print(f"   - 총 실패 Pod 수: {total_execution_summary['total_failed_pods']}")
            print(f"   🏆 모든 Pod가 성공적으로 실행되었습니다!")
            
            # 시뮬레이션 상태 업데이트
            await self._update_simulation_status_and_log(simulation_id, "COMPLETED", "모든 스텝 성공적으로 완료")
            
            return total_execution_summary
            
        else:
            print(f"🔄 다른 패턴 타입 처리 예정: {simulation.pattern_type}")
            print(f"   현재 SEQUENTIAL 패턴만 지원됩니다.")
            raise NotImplementedError(f"패턴 타입 {simulation.pattern_type}는 아직 구현되지 않았습니다.")

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
        query = (
            select(Simulation)
            .where(Simulation.id == simulation_id)
        )
        result = await self.session.execute(query)
        simulation = result.scalar_one_or_none()

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
            pod_status = await self.ros_service.send_get_request(pod_ip)

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
                status_response = await self.ros_service.send_get_request(
                    pod_ip, "/rosbag/status"
                )
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