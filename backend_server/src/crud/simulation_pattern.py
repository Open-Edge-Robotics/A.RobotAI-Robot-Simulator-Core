import asyncio
import traceback
from typing import Annotated, Dict, List, Optional, Union

from fastapi import Depends
from schemas.pod import GroupIdFilter, StepOrderFilter
from crud.pod import PodService
from crud.simulation import SimulationRepository
from models.enums import PatternType, SimulationStatus
from models.simulation import Simulation
from models.simulation_steps import SimulationStep
from models.simulation_groups import SimulationGroup
from database.db_conn import AsyncSession, async_sessionmaker, get_async_sessionmaker
from repositories.simulation_repository import SimulationRepository
from repositories.instance_repository import InstanceRepository
from repositories.template_repository import TemplateRepository
from schemas.simulation_pattern import (
    GroupCreateDTO,
    GroupResponseData,
    GroupUpdateDTO,
    PatternCreateRequestDTO,
    PatternDeleteRequestDTO,
    PatternResponseDTO,
    PatternUpdateRequestDTO,
    StepCreateDTO,
    StepResponseData,
    StepUpdateDTO
)
from exception.simulation_exceptions import (
    PatternTypeMismatchError,
    SimulationError,
    SimulationNotFoundError,
    SimulationStatusError,
    SimulationStepNotFoundError,
    SimulationGroupNotFoundError
)
from exception.template_exceptions import TemplateNotFoundError

class SimulationPatternService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        simulation_repository: SimulationRepository,
        instance_repository: InstanceRepository,
        template_repository: TemplateRepository
    ):
        self.sessionmaker = sessionmaker
        self.simulation_repository = simulation_repository
        self.instance_repository = instance_repository
        self.template_repository = template_repository
        
    # =====================================================
    # 공통 검증 함수
    # =====================================================
    async def _get_simulation(self, simulation_id: int, session: AsyncSession):
        """Simulation 존재 여부 확인"""
        simulation = await self.simulation_repository.find_by_id(simulation_id, session)
        if not simulation:
            raise SimulationNotFoundError(simulation_id)
        return simulation

    def _check_simulation_status(self, simulation, action: str, forbidden_statuses: set):
        """
        시뮬레이션 상태 검증
        action: "패턴 추가", "패턴 수정", "패턴 삭제" 등 API별 메시지
        forbidden_statuses: 상태에 따라 검증
        """
        if simulation.status in forbidden_statuses:
            raise SimulationStatusError(simulation.status, action)

    def _check_pattern_type_match(self, expected: str, actual: str):
        """Step/Group 타입 일치 여부 검증"""
        if expected != actual:
            raise PatternTypeMismatchError(expected, actual)
        
    # =====================================================
    # Step / Group 비즈니스 검증
    # =====================================================
    async def _validate_step_creation_business_rules(self, simulation_id: int, step_data: StepCreateDTO, session: AsyncSession):
        """Step 생성 전 비즈니스 규칙 검증"""
        # Step Order 중복 확인
        existing_step = await self.simulation_repository.find_step_by_order(
            simulation_id, step_data.step_order, session
        )
        if existing_step:
            raise SimulationError(f"❌ Step Order {step_data.step_order}가 이미 존재합니다.")

        # 순차 실행 규칙 확인
        if step_data.step_order > 1:
            prev_step = await self.simulation_repository.find_step_by_order(
                simulation_id, step_data.step_order - 1, session
            )
            if not prev_step:
                raise SimulationError(f"❌ Step Order {step_data.step_order - 1}이 먼저 생성되어야 합니다.")

        # Template 존재 여부 확인
        template = await self.template_repository.find_by_id(step_data.template_id, session)
        if not template:
            raise TemplateNotFoundError(step_data.template_id)

    async def _validate_group_creation_business_rules(self, simulation_id: int, group_data: GroupCreateDTO, session: AsyncSession):
        """Group 생성 전 비즈니스 규칙 검증"""
        template = await self.template_repository.find_by_id(group_data.template_id, session)
        if not template:
            raise TemplateNotFoundError(group_data.template_id)
        
    # =====================================================
    # 패턴 생성 전 검증
    # =====================================================
    async def _validate_pattern_modification(self, simulation_id: int, body: Union[PatternCreateRequestDTO, PatternUpdateRequestDTO]):
        """패턴 생성 전 API 공통 검증"""
        async with self.sessionmaker() as session:
            # 1. Simulation 존재 여부 확인
            simulation = await self._get_simulation(simulation_id, session)

            # 2. 시뮬레이션 상태 검증 (패턴 추가 금지 상태 확인)
            self._check_simulation_status(
                simulation,
                action="패턴 추가",
                forbidden_statuses={
                    SimulationStatus.INITIATING,
                    SimulationStatus.RUNNING,
                    SimulationStatus.DELETING,
                    SimulationStatus.DELETED,
                }
            )

            # 3. 패턴 타입과 요청 타입 검증
            if simulation.pattern_type == PatternType.SEQUENTIAL:
                if body.group:
                    self._check_pattern_type_match(expected="Step", actual="Group")
                if not body.step:
                    raise SimulationError("❌ SEQUENTIAL 시뮬레이션에서는 Step 패턴이 필요합니다")

            elif simulation.pattern_type == PatternType.PARALLEL:
                if body.step:
                    self._check_pattern_type_match(expected="Group", actual="Step")
                if not body.group:
                    raise SimulationError("❌ PARALLEL 시뮬레이션에서는 Group 패턴이 필요합니다")
                
    # =====================================================
    # 패턴 삭제 전 검증
    # =====================================================
    async def _validate_before_deletion(self, simulation_id: int, body: PatternDeleteRequestDTO):
        """패턴 삭제 전 API 공통 검증"""
        async with self.sessionmaker() as session:
            # 1. Simulation 존재 여부 확인
            simulation = await self._get_simulation(simulation_id, session)

            # 2. 시뮬레이션 상태 검증 (삭제 금지 상태 확인)
            self._check_simulation_status(
                simulation,
                action="패턴 삭제",
                forbidden_statuses={
                    SimulationStatus.INITIATING,
                    SimulationStatus.RUNNING,
                    SimulationStatus.DELETING,
                    SimulationStatus.DELETED,
                }
            )

            # 3. 패턴 타입/요청 검증
            if simulation.pattern_type == PatternType.SEQUENTIAL:
                if getattr(body.group, "group_id", None):
                    self._check_pattern_type_match(expected="Step", actual="Group")
                if not getattr(body.step, "step_order", None):
                    raise SimulationError("❌ SEQUENTIAL 시뮬레이션에서는 Step Order가 필요합니다")

            elif simulation.pattern_type == PatternType.PARALLEL:
                if getattr(body.step, "step_order", None):
                    self._check_pattern_type_match(expected="Group", actual="Step")
                if not getattr(body.group, "group_id", None):
                    raise SimulationError("❌ PARALLEL 시뮬레이션에서는 Group ID가 필요합니다")
   
    async def create_pattern(self, simulation_id: int, body: PatternCreateRequestDTO) -> PatternResponseDTO:
        try:
            # 검증 (ID들만 확인)
            await self._validate_pattern_modification(simulation_id, body)
            
            # 패턴 별 처리
            if body.step:
                return await self._create_step_pattern(simulation_id, body.step)
            elif body.group:
                return await self._create_group_pattern(simulation_id, body.group)
            else:
                raise ValueError("❌ Step 또는 Group 중 하나는 필수입니다")
        except Exception as e:
            traceback.print_stack()
            print(f"❌ 패턴 생성 실패: {e}")
            raise
    
    async def delete_pattern(self, simulation_id: int, body: PatternDeleteRequestDTO) -> PatternResponseDTO:
        # 1. 사전 검증 (상태/타입만 체크)
        await self._validate_before_deletion(simulation_id, body)
        
        simulation: Simulation = None
        step: SimulationStep = None
        group: SimulationGroup = None
        
        # 2. 삭제 대상 정보 조회 (읽기 전용 트랜잭션)
        async with self.sessionmaker() as session:
            simulation = await self.simulation_repository.find_by_id(simulation_id, session)
            
            if simulation.pattern_type == PatternType.SEQUENTIAL:
                step = await self.simulation_repository.find_step(
                    simulation_id=simulation_id, 
                    step_order=body.step.step_order, 
                    session=session
                )
                if not step:
                    raise ValueError(f"❌ Step {body.step.step_order}를 찾을 수 없습니다")
        
                # 마지막 Step인지 확인
                last_step = await self.simulation_repository.find_step(
                    simulation_id=simulation_id,
                    last=True,
                    session=session
                )
                if step.step_order != last_step.step_order:
                    raise ValueError(
                        f"❌ 순차 패턴: 마지막 단계 Step {last_step.step_order}부터 삭제 가능합니다"
                    )
                    
            elif simulation.pattern_type == PatternType.PARALLEL:
                group = await self.simulation_repository.find_group(
                    simulation_id=simulation_id,
                    group_id=body.group.group_id, 
                    session=session
                )
                if not group:
                    raise ValueError(f"❌ Group {body.group.group_id}를 찾을 수 없습니다")
        
        # 3. DB 리소스 삭제 (단일 트랜잭션)
        try:
            async with self.sessionmaker() as session:
                async with session.begin():  # 명시적 트랜잭션 시작
                    if simulation.pattern_type == PatternType.SEQUENTIAL:
                        # Instance bulk 삭제
                        deleted_count = await self.instance_repository.delete_by_step(
                            step_order=body.step.step_order,
                            simulation_id=simulation.id,
                            session=session
                        )
                        print(f"✅ {deleted_count}개 Instance 삭제")
                        # Step 삭제
                        await self.simulation_repository.delete_step(
                            session=session,
                            step_order=body.step.step_order,
                            simulation_id=simulation.id
                        )
                        print(f"✅ SimulationStep {body.step.step_order} 삭제")
                        
                    elif simulation.pattern_type == PatternType.PARALLEL:
                        # Instance bulk 삭제
                        deleted_count = await self.instance_repository.delete_by_group(
                            group_id=body.group.group_id,
                            session=session
                        )
                        print(f"✅ {deleted_count}개 Instance 삭제")
                        # Group 삭제
                        await self.simulation_repository.delete_group(
                            session=session,
                            group_id=body.group.group_id
                        )
                        print(f"✅ SimulationGroup {body.group.group_id} 삭제")
                    
                    # 트랜잭션 자동 커밋
                    print("✅ DB 트랜잭션 커밋 완료")
        except Exception as e:
            print(f"❌ DB 삭제 실패: {e}")
            raise ValueError(f"DB 삭제 실패: {e}")
    
        print("🎉 패턴 삭제 완료")
        
        # 4. 삭제 DTO 구성
        if simulation.pattern_type == PatternType.SEQUENTIAL:
            deleted_data = StepResponseData(step_order=body.step.step_order).model_dump()
            message = "Step 삭제 완료"
        else:
            deleted_data = GroupResponseData(group_id=body.group.group_id).model_dump()
            message = "Group 삭제 완료"

        return PatternResponseDTO(
            statusCode=200,
            data={"step" if simulation.pattern_type == PatternType.SEQUENTIAL else "group": deleted_data},
            message=message
        )
        
    async def update_pattern(
        self, 
        simulation_id: int, 
        body: PatternUpdateRequestDTO
    ) -> PatternResponseDTO:
        """
        시뮬레이션 패턴 업데이트
        
        Args:
            simulation_id: 시뮬레이션 ID
            request_dto: 패턴 업데이트 요청 DTO
            
        Raises:
            SimulationNotFoundError: 시뮬레이션이 존재하지 않는 경우
            PatternMismatchingError: 패턴 타입이 맞지 않는 경우
            SimulationError: 기타 비즈니스 검증 실패
        """
        async with self.sessionmaker() as session:
            await self._validate_pattern_modification(simulation_id, body)
            
            updated_data = {}
                
            if body.step:
                
                # Step 업데이트
                updated_step = await self._update_step(
                    simulation_id,
                    body.step,
                    session
                )
                
                updated_data = StepResponseData(
                    step_order=updated_step.step_order,
                    template_id=updated_step.template_id,
                    template_name=updated_step.template.name if updated_step.template else None,
                    template_type=updated_step.template.type if updated_step.template else None,
                    autonomous_agent_count=updated_step.autonomous_agent_count,
                    execution_time=updated_step.execution_time,
                    repeat_count=updated_step.repeat_count,
                    delay_after_completion=updated_step.delay_after_completion
                ).model_dump()
                
                message = "Step 수정 완료"
                
            
            elif body.group:
                # Group 업데이트 처리
                updated_group = await self._update_group(
                    simulation_id,
                    body.group,
                    session
                )
                
                updated_data = GroupResponseData(
                    group_id=updated_group.id,
                    group_name=updated_group.group_name,
                    template_id=updated_group.template_id,
                    template_name=updated_group.template.name if updated_group.template else None,
                    template_type=updated_group.template.type if updated_group.template else None,
                    autonomous_agent_count=updated_group.autonomous_agent_count,
                    execution_time=updated_group.execution_time,
                    repeat_count=updated_group.repeat_count,
                    assigned_area=updated_group.assigned_area,
                    template={
                        "template_id": updated_group.template.template_id,
                        "name": updated_group.template.name,
                        "type": updated_group.template.type
                    } if updated_group.template else None
                ).model_dump()
                
                message = "Group 수정 완료"
            
            return PatternResponseDTO(
                statusCode=200,
                data={"step" if body.step else "group": updated_data},
                message=message
            )
                
                
                
    async def _update_step(
        self, 
        simulation_id: int, 
        step_dto: StepUpdateDTO, 
        session: AsyncSession
    ) -> SimulationStep:
        """
        시뮬레이션 스텝 업데이트
        
        Args:
            simulation_id: 시뮬레이션 ID
            step_dto: 스텝 업데이트 DTO
            session: 데이터베이스 세션
            
        Raises:
            SimulationStepNotFoundError: 스텝이 존재하지 않는 경우 
            TemplateNotFoundError: 템플릿이 존재하지 않는 경우
        """
        # 기존 스텝 조회
        existing_step = await self.simulation_repository.find_step(
            simulation_id,
            step_order=step_dto.step_order,
            session=session
        )
        
        if not existing_step:
            raise SimulationStepNotFoundError(step_dto.step_order)
            
        # 템플릿 ID 가 제공된 경우 템플릿 존재 여부 확인
        if step_dto.template_id is not None:
            template = await self.template_repository.find_by_id(step_dto.template_id, session)
            if not template:
                raise TemplateNotFoundError(step_dto.template_id)
            existing_step.template_id = step_dto.template_id
            
        # 다른 필드들 업데이트
        if step_dto.autonomous_agent_count is not None:
            existing_step.autonomous_agent_count = step_dto.autonomous_agent_count
        if step_dto.execution_time is not None:
            existing_step.execution_time = step_dto.execution_time
    
        if step_dto.delay_after_completion is not None:
            existing_step.delay_after_completion = step_dto.delay_after_completion
        
        if step_dto.repeat_count is not None:
            existing_step.repeat_count = step_dto.repeat_count
        
            
        session.add(existing_step)  # 이미 영속 객체라 필요 없을 수도 있지만 안전
        await session.commit()       # DB에 UPDATE 실행
        await session.refresh(existing_step)  # 최신 DB 상태 반영 
            
        return existing_step
            
    async def _update_group(
        self, 
        simulation_id: int, 
        group_dto: GroupUpdateDTO, 
        session: AsyncSession
    ) -> SimulationGroup:
        """
        시뮬레이션 그룹 업데이트
        
        Args:
            simulation_id: 시뮬레이션 ID
            group_dto: 그룹 업데이트 DTO
            session: 데이터베이스 세션
            
        Raises:
            SimulationGroupNotFoundError: 그룹이 존재하지 않는 경우 
            TemplateNotFoundError: 템플릿이 존재하지 않는 경우
        """            
        # 기존 그룹 조회
        existing_group = await self.simulation_repository.find_group(
            simulation_id, 
            group_id=group_dto.group_id, 
            session=session
        )
        
        if not existing_group:
            raise SimulationGroupNotFoundError(group_dto.group_id)
        
        # 템플릿 ID가 제공된 경우 템플릿 존재 여부 확인
        if group_dto.template_id is not None:
            template = await self.template_repository.find_by_id(group_dto.template_id, session)
            if not template:
                raise TemplateNotFoundError(group_dto.template_id)
            existing_group.template_id = group_dto.template_id
        
        # 다른 필드들 업데이트
        if group_dto.autonomous_agent_count is not None:
            existing_group.autonomous_agent_count = group_dto.autonomous_agent_count
        if group_dto.execution_time is not None:
            existing_group.execution_time = group_dto.execution_time
        
        if group_dto.repeat_count is not None:
            existing_group.repeat_count = group_dto.repeat_count  
            
        session.add(existing_group)  # 이미 영속 객체라 필요 없을 수도 있지만 안전
        await session.commit()       # DB에 UPDATE 실행
        await session.refresh(existing_group)  # 최신 DB 상태 반영    
            
        return existing_group

        
    # -----------------------------
    # 고아 리소스 경고 + 예약 정리
    # -----------------------------
    async def _log_orphaned_resources_warning(self, simulation, step=None, group=None):
        """
        고아 리소스 경고 로깅 및 정리 작업 예약
        """
        if step:
            print(f"⚠️ WARNING: Step {step.id}의 Pod들은 삭제되었지만 DB 레코드 삭제 실패")
            print(f"⚠️ 수동 정리 필요: step_order={step.step_order}, namespace={simulation.namespace}")
            # 선택적 자동 정리
            await self.cleanup_orphaned_pods(
                simulation_id=simulation.id,
                pattern_type=PatternType.SEQUENTIAL,
                step_order=step.step_order
            )
        elif group:
            print(f"⚠️ WARNING: Group {group.id}의 Pod들은 삭제되었지만 DB 레코드 삭제 실패")
            print(f"⚠️ 수동 정리 필요: group_id={group.id}, namespace={simulation.namespace}")
            # 선택적 자동 정리
            await self.cleanup_orphaned_pods(
                simulation_id=simulation.id,
                pattern_type=PatternType.PARALLEL,
                group_id=group.id
            )
            
    # -----------------------------
    # 고아 Pod 정리
    # -----------------------------
    async def cleanup_orphaned_pods(
        self,
        simulation_id: int,
        pattern_type: PatternType,
        step_order: int = None,
        group_id: int = None
    ):
        """
        고아 Pod 정리 (DB 삭제 후 Pod만 남은 경우)
        """
        try:
            async with self.sessionmaker() as session:
                simulation = await self.simulation_repository.find_by_id(simulation_id, session)

            if pattern_type == PatternType.SEQUENTIAL and step_order is not None:
                filter_params = {"step_order": step_order}
                await PodService.delete_pods_by_filter(simulation.namespace, filter_params)
                print(f"✅ 고아 Pod 정리 완료: step_order={step_order}")

            elif pattern_type == PatternType.PARALLEL and group_id is not None:
                filter_params = {"group_id": group_id}
                await PodService.delete_pods_by_filter(simulation.namespace, filter_params)
                print(f"✅ 고아 Pod 정리 완료: group_id={group_id}")

            else:
                print(f"⚠️ 고아 Pod 정리 실패: 필수 파라미터(step_order/group_id)가 누락됨")

        except Exception as e:
            print(f"❌ 고아 Pod 정리 실패: {e}")

        
    async def _delete_kubernetes_resources(
        self,
        namespace: str,
        step_order: Optional[int] = None,
        group_id: Optional[int] = None,
        resource_name: Optional[str] = None  # 로그용 (step.name 또는 group.name)
    ) -> bool:
        """
        Kubernetes 리소스 삭제
        - 삭제 성공 시 True 반환
        - 이미 삭제된 경우 False 반환
        """
        print("_delete_kubernetes_resources 호출")
        if not step_order and not group_id:
            raise ValueError("step_order 또는 group_id 중 하나는 반드시 필요합니다.")
        
        try:
            # 필터 구성
            filter_params = {}
            if step_order is not None:
                filter_params = StepOrderFilter(step_order=step_order)
            if group_id is not None:
                filter_params = GroupIdFilter(group_id=group_id)

            # Pod 조회
            pods = PodService.get_pods_by_filter(
                namespace=namespace, 
                filter_params=filter_params
            )
            
            if not pods:
                target = resource_name or ("Step" if step_order else "Group")
                print(f"ℹ️ {target} 관련 Pod이 이미 삭제되어 있음 → Kubernetes 리소스 정리 스킵")
                return False

            # Pod 존재 -> 삭제 진행
            pod_names = [pod.metadata.name for pod in pods]
            print(f"🗑️  {len(pods)}개 Pod 삭제 시작: {', '.join(pod_names)}")

            # Pod 삭제
            delete_tasks = [PodService.delete_pod(pod, namespace) for pod in pod_names]
            await asyncio.gather(*delete_tasks)

            # Pod 삭제 완료 대기
            await PodService.wait_for_pods_deletion(namespace, filter_params, timeout=60)

            target = resource_name or ("Step" if step_order else "Group")
            print(f"✅ {target} Kubernetes 리소스 정리 완료")
            return True

        except Exception as e:
            # 실제 삭제 과정에서 오류 발생 → DB 삭제 중단
            raise RuntimeError(f"Kubernetes 리소스 삭제 실패: {e}")
        
        
    # =====================================================
    # STEP 및 GROUP 패턴 메인 메서드
    # =====================================================    
    async def _create_step_pattern(self, simulation_id: int, step_data: StepCreateDTO):
        """Step 패턴 생성만 담당"""
        created_step_id = None
        created_instance_ids = []
        
        try:
            # Step 생성
            step_response, created_instance_ids = await self._create_step_and_instances(simulation_id, step_data)
            created_step_id = step_response.id
            
            return PatternResponseDTO(
                statusCode=200, 
                data={"step": step_response.model_dump()},
                message="Step 생성 완료"
            )
            
        except Exception as e:
            # 실패 시 정리
            await self._cleanup_step_resources(created_step_id, created_instance_ids)
            raise
    
    async def _create_group_pattern(self, simulation_id: int, group_data: GroupCreateDTO):
        """Group 패턴 생성만 담당"""
        created_group_id = None
        created_instance_ids = []

        try:
            group_response, created_instance_ids = await self._create_group_and_instances(simulation_id, group_data)
            created_group_id = group_response.group_id
            
            return PatternResponseDTO(
                statusCode=200, 
                data={ "group": group_response.model_dump() },
                message="Group 생성 완료"
            )
            
        except Exception as e:
            # 실패 시 정리
            await self._cleanup_group_resources(created_group_id, created_instance_ids)
            raise
    
    # =====================================================
    # STEP 및 GROUP DB 작업 및 데이터 추출
    # =====================================================
    async def _create_step_and_instances(self, simulation_id: int, step_data: StepCreateDTO):
        """DB 작업과 데이터 추출을 동시에"""
        async with self.sessionmaker() as session:
            async with session.begin():
                await self._validate_step_creation_business_rules(simulation_id, step_data, session)
                
                # Simulation과 Template 재조회 (세션 내에서)
                simulation = await self.simulation_repository.find_by_id(simulation_id, session)
                template = await self.template_repository.find_by_id(step_data.template_id, session)
                
                # Step 생성
                step = await self.simulation_repository.create_simulation_step(
                    session=session,
                    simulation_id=simulation.id,
                    step_order=step_data.step_order,
                    template_id=template.template_id,
                    execution_time=step_data.execution_time,
                    autonomous_agent_count=step_data.autonomous_agent_count,
                    delay_after_completion=step_data.delay_after_completion,
                    repeat_count=step_data.repeat_count,
                )
                
                # 3. Instance 생성
                instances = await self.instance_repository.create_instances_batch(
                    simulation=simulation,
                    step=step,
                    session=session
                )
                
                # StepResponseData 생성
                step_response = StepResponseData(
                    id=step.id,
                    step_order=step.step_order,
                    template_id=step.template_id,
                    template_name=template.name if template else None,
                    template_type=template.type if template else None,
                    autonomous_agent_count=step.autonomous_agent_count,
                    execution_time=step.execution_time,
                    delay_after_completion=step.delay_after_completion,
                    repeat_count=step.repeat_count
                )
                
                return step_response, [inst.id for inst in instances]
           
    async def _create_group_and_instances(self, simulation_id: int, group_data: GroupCreateDTO):
        """Group DB 작업과 데이터 추출을 동시에"""
        
        async with self.sessionmaker() as session:
            async with session.begin():
                await self._validate_group_creation_business_rules(simulation_id, group_data, session)
                
                # Simulation과 Template 재조회 (세션 내에서)
                print("Simulation과 Template 재조회 (세션 내에서)")
                simulation = await self.simulation_repository.find_by_id(simulation_id, session)
                template = await self.template_repository.find_by_id(group_data.template_id, session)
                
                # Group 생성
                print("Group 생성")
                group = await self.simulation_repository.create_simulation_group(
                    session=session,
                    simulation_id=simulation.id,
                    template_id=template.template_id,
                    autonomous_agent_count=group_data.autonomous_agent_count,
                    repeat_count=group_data.repeat_count,
                    execution_time=group_data.execution_time,
                    assigned_area=simulation.namespace,
                )
                
                # Instance 생성 (Group용)
                instances = await self.instance_repository.create_instances_batch(
                    simulation=simulation,
                    group=group,
                    session=session
                )
                
                # GroupResponseData 생성
                group_response = GroupResponseData(
                    group_id=group.id,
                    group_name=group.group_name,
                    template_id=group.template_id,
                    template_name=template.name if template else None,
                    template_type=template.type if template else None,
                    autonomous_agent_count=group.autonomous_agent_count,
                    execution_time=group.execution_time,
                    repeat_count=group.repeat_count,
                    assigned_area=group.assigned_area
                )
                
                return group_response, [inst.id for inst in instances]      
                
    # =====================================================
    # 공통 메서드 수정 (Step과 Group 모두 지원)
    # =====================================================            
    async def _create_pods_for_instances(
        self, 
        instances_data: List[Dict], 
        simulation_data: Dict, 
        step_data: Optional[Dict] = None, 
        group_data: Optional[Dict] = None 
    ):
        if step_data is None and group_data is None:
            raise ValueError("step_data와 group_data 중 하나는 반드시 필요합니다.")
        
        """Pod 생성 - 빠른 실패 적용"""
        created_pods = []
        
        print(f"🚀 {len(instances_data)}개 Pod 순차 생성 시작")
        
        for i, instance_data in enumerate(instances_data):
            try:
                print(f"📦 Pod {i+1}/{len(instances_data)} 생성 중: {instance_data['name']}")
                
                # 타임아웃과 함께 Pod 생성
                await asyncio.wait_for(
                    PodService.create_pod_v2(
                        instance_data, 
                        simulation_data, 
                        step_data, 
                        group_data
                    ),
                    timeout=60  # 60초 타임아웃
                )
                
                created_pods.append({
                    "pod_name": instance_data["name"],
                    "namespace": instance_data["pod_namespace"]
                })
                
                print(f"✅ Pod 생성 성공: {instance_data['name']}")
                
            except asyncio.TimeoutError:
                print(f"❌ Pod 생성 타임아웃: {instance_data['name']} (60초 초과)")
                raise Exception(f"Pod {instance_data['name']} 생성 타임아웃")
                
            except Exception as e:
                print(f"❌ Pod 생성 실패: {instance_data['name']} -> {str(e)}")
                raise Exception(f"Pod {instance_data['name']} 생성 실패: {str(e)}")
        
        print(f"🎉 모든 Pod 생성 완료: {len(created_pods)}개")
        return created_pods
    
    # =====================================================
    # 리소스 정리
    # =====================================================
    async def _cleanup_step_resources(self, step_id: int = None, instance_ids: List[int] = None):
        """Step 리소스 정리"""
        print("🧹 Step 리소스 정리 시작")
        
        cleanup_errors = []
        
        try:
            # DB 정리
            if step_id or instance_ids:
                print("  🗄️ DB 리소스 정리 중...")
                try:
                    async with self.sessionmaker() as session:
                        async with session.begin():
                            
                            # Instance 삭제
                            if instance_ids:
                                for instance_id in instance_ids:
                                    try:
                                        await self.instance_repository.delete(session, instance_id)
                                        print(f"  ✅ Instance 삭제 완료: {instance_id}")
                                    except Exception as e:
                                        error_msg = f"Instance {instance_id} 삭제 실패: {str(e)}"
                                        print(f"  ❌ {error_msg}")
                                        cleanup_errors.append(error_msg)

                            # Step 삭제
                            if step_id:
                                try:
                                    await self.simulation_repository.delete_step(session, step_id)
                                    print(f"  ✅ Step 삭제 완료: {step_id}")
                                except Exception as e:
                                    error_msg = f"Step {step_id} 삭제 실패: {str(e)}"
                                    print(f"  ❌ {error_msg}")
                                    cleanup_errors.append(error_msg)
                                    
                except Exception as e:
                    error_msg = f"DB 정리 트랜잭션 실패: {str(e)}"
                    print(f"  ❌ {error_msg}")
                    cleanup_errors.append(error_msg)

        except Exception as e:
            cleanup_errors.append(f"정리 작업 예외: {str(e)}")

        if cleanup_errors:
            print(f"❌ 정리 작업 중 {len(cleanup_errors)}개 오류 발생")
            for error in cleanup_errors:
                print(f"  - {error}")
            # 정리 실패는 로그만 남기고 원본 예외 유지
        else:
            print("✅ 모든 리소스 정리 완료")
    
    async def _cleanup_group_resources(self, group_id: int = None, instance_ids: List[int] = None):
        """Group 리소스 정리"""
        print("🧹 Group 리소스 정리 시작")
        
        cleanup_errors = []
        
        try:
            # DB 정리
            if group_id or instance_ids:
                print("  🗄️ DB 리소스 정리 중...")
                try:
                    async with self.sessionmaker() as session:
                        async with session.begin():
                            
                            # Instance 삭제
                            if instance_ids:
                                for instance_id in instance_ids:
                                    try:
                                        await self.instance_repository.delete(session, instance_id)
                                        print(f"  ✅ Instance 삭제 완료: {instance_id}")
                                    except Exception as e:
                                        error_msg = f"Instance {instance_id} 삭제 실패: {str(e)}"
                                        print(f"  ❌ {error_msg}")
                                        cleanup_errors.append(error_msg)

                            # Group 삭제
                            if group_id:
                                try:
                                    await self.simulation_repository.delete_group(session, group_id)
                                    print(f"  ✅ Group 삭제 완료: {group_id}")
                                except Exception as e:
                                    error_msg = f"Group {group_id} 삭제 실패: {str(e)}"
                                    print(f"  ❌ {error_msg}")
                                    cleanup_errors.append(error_msg)
                                    
                except Exception as e:
                    error_msg = f"DB 정리 트랜잭션 실패: {str(e)}"
                    print(f"  ❌ {error_msg}")
                    cleanup_errors.append(error_msg)

        except Exception as e:
            cleanup_errors.append(f"정리 작업 예외: {str(e)}")

        if cleanup_errors:
            print(f"❌ 정리 작업 중 {len(cleanup_errors)}개 오류 발생")
            for error in cleanup_errors:
                print(f"  - {error}")
            # 정리 실패는 로그만 남기고 원본 예외 유지
        else:
            print("✅ 모든 리소스 정리 완료")
    
# FastAPI 의존성 주입 함수
async def get_simulation_pattern_service(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_async_sessionmaker)]
) -> SimulationPatternService:
    simulation_repository = SimulationRepository(session_factory)
    instance_repository = InstanceRepository(session_factory)
    template_repository = TemplateRepository(session_factory)
    return SimulationPatternService(
        sessionmaker=session_factory,
        simulation_repository=simulation_repository,
        instance_repository=instance_repository,
        template_repository=template_repository
    )