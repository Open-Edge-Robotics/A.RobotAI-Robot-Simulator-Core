from datetime import datetime
import traceback
from typing import Tuple, List, Optional
from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select, exists, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload
from starlette.status import HTTP_409_CONFLICT

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
    SimulationOverview
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

    async def start_simulation(self, simulation_id: int):
        simulation = await self.find_simulation_by_id(simulation_id, "start simulation")

        # 스케줄된 시작 시간 확인
        if simulation.scheduled_start_time:
            current_time = datetime.now()
            if current_time < simulation.scheduled_start_time:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"시뮬레이션은 {simulation.scheduled_start_time}에 시작할 수 있습니다.",
                )

        # 스케줄된 종료 시간 확인
        if simulation.scheduled_end_time:
            current_time = datetime.now()
            if current_time > simulation.scheduled_end_time:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"시뮬레이션의 예정 종료 시간({simulation.scheduled_end_time})이 지났습니다.",
                )

        instances = await self.get_simulation_instances(simulation_id)

        for instance in instances:
            object_path = instance.template.bag_file_path
            await self.pod_service.check_pod_status(instance)
            pod_ip = await self.pod_service.get_pod_ip(instance)

            # 고도화된 rosbag 실행 파라미터 준비
            rosbag_params = {
                "object_path": object_path,
                "max_loops": simulation.repeat_count,
                "delay_between_loops": simulation.delay_time or 0,
                "execution_duration": simulation.execution_time,
            }

            await self.ros_service.send_post_request(
                pod_ip, "/rosbag/play", rosbag_params
            )

        return SimulationControlResponse(simulation_id=simulation_id).model_dump()

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
            .options(selectinload(Simulation.instances))
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