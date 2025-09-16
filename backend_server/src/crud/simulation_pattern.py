import asyncio
from datetime import datetime, timezone
import traceback
from typing import Annotated, Dict, List, Optional

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
    PatternCreateRequestDTO,
    PatternCreateResponseDTO,
    PatternDeleteRequestDTO,
    PatternDeleteResponseDTO,
    StepCreateDTO
)

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
   
    async def create_pattern(self, simulation_id: int, body: PatternCreateRequestDTO) -> PatternCreateResponseDTO:
        try:
            print(f"{body}")
            await self._validate_request_data(body)
            
            # 검증 (ID들만 확인)
            await self._validate_before_creation(simulation_id, body)
            
            # 패턴 별 처리
            if body.step:
                return await self._create_step_pattern(simulation_id, body.step)
            elif body.group:
                return await self._create_group_pattern(simulation_id, body.group)
            else:
                raise ValueError("❌ Step 또는 Group 중 하나는 필수입니다")
        except Exception as e:
            print(f"❌ 패턴 생성 실패: {e}")
            raise
    
    async def delete_pattern(self, simulation_id: int, body: PatternDeleteRequestDTO) -> PatternDeleteResponseDTO:
        """패턴 삭제 - Kubernetes 먼저, DB 나중 순서로 처리"""
    
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
                    step_order=body.step_order, 
                    session=session
                )
                if not step:
                    raise ValueError(f"❌ Step {body.step_id}를 찾을 수 없습니다")
                
                if step.simulation_id != simulation_id:
                    raise ValueError(f"❌ Step {body.step_id}는 Simulation {simulation_id}에 속하지 않습니다")
                
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
                    group_id=body.group_id, 
                    session=session
                )
                if not group:
                    raise ValueError(f"❌ Group {body.group_id}를 찾을 수 없습니다")
                
                if group.simulation_id != simulation_id:
                    raise ValueError(f"❌ Group {body.group_id}는 Simulation {simulation_id}에 속하지 않습니다")
        
        # 3. Kubernetes 리소스 정리 (트랜잭션 외부)
        try:
            if simulation.pattern_type == PatternType.SEQUENTIAL:
                kubernetes_deleted  = await self._delete_kubernetes_resources(
                    namespace=simulation.namespace,
                    step_order=step.step_order,
                    resource_name=f"Step {step.step_order} of Simulation {simulation.id}"
                )
            elif simulation.pattern_type == PatternType.PARALLEL:
                kubernetes_deleted  = await self._delete_kubernetes_resources(
                    namespace=simulation.namespace,
                    group_id=group.id,
                    resource_name=f"Group {group.id} of Simulation {simulation.id}"
                )

            print(f"✅ Kubernetes 리소스 삭제 완료")

        except Exception as e:
            print(f"❌ Kubernetes 리소스 삭제 실패: {e}")
            raise ValueError(f"Kubernetes 리소스 삭제 실패로 패턴 삭제를 중단합니다: {e}")
        
        # 4. DB 리소스 삭제 (단일 트랜잭션)
        try:
            async with self.sessionmaker() as session:
                async with session.begin():  # 명시적 트랜잭션 시작
                    if simulation.pattern_type == PatternType.SEQUENTIAL:
                        # Instance bulk 삭제
                        deleted_count = await self.instance_repository.delete_by_step(
                            step_order=body.step_order,
                            simulation_id=simulation.id,
                            session=session
                        )
                        print(f"✅ {deleted_count}개 Instance 삭제")
                        # Step 삭제
                        await self.simulation_repository.delete_step(
                            session=session,
                            step_order=body.step_order,
                            simulation_id=simulation.id
                        )
                        print(f"✅ SimulationStep {body.step_order} 삭제")
                        
                    elif simulation.pattern_type == PatternType.PARALLEL:
                        # Instance bulk 삭제
                        deleted_count = await self.instance_repository.delete_by_group(
                            group_id=body.group_id,
                            session=session
                        )
                        print(f"✅ {deleted_count}개 Instance 삭제")
                        # Group 삭제
                        await self.simulation_repository.delete_group(
                            session=session,
                            group_id=body.group_id
                        )
                        print(f"✅ SimulationGroup {body.group_id} 삭제")
                    
                    # 트랜잭션 자동 커밋
                    print("✅ DB 트랜잭션 커밋 완료")
        except Exception as e:
            print(f"❌ DB 삭제 실패: {e}")
            # DB 삭제 실패 시 orphaned pods에 대한 정리 필요성 로깅
            await self._log_orphaned_resources_warning(simulation, step, group)
            raise ValueError(f"DB 삭제 실패: {e}")
    
        print("🎉 패턴 삭제 완료")
        
        # 5. DB 삭제 완료 후 Response DTO 구성
        response_data = {
            "simulation_id": simulation_id,
            "pattern_type": simulation.pattern_type.value,
            "deleted_target": body.step_order if simulation.pattern_type == PatternType.SEQUENTIAL else body.group_id,
            "kubernetes_deleted": kubernetes_deleted,
            "deleted_at": datetime.now(timezone.utc)
        }

        return PatternDeleteResponseDTO(
            statusCode=200,
            data=response_data,
            message="패턴 삭제 완료"
        )
        
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
    # 비즈니스 규칙 검증
    # =====================================================   
    async def _validate_request_data(self, body: PatternCreateRequestDTO):
        """요청 데이터 기본 검증"""
        
        # 1. Step과 Group 둘 다 없는 경우
        if not body.step and not body.group:
            raise ValueError("❌ Step 또는 Group 중 하나는 필수입니다")
        
        # 2. Step과 Group 둘 다 있는 경우
        if body.step and body.group:
            raise ValueError("❌ Step과 Group을 동시에 요청할 수 없습니다")
        
        # 3. Step 데이터 기본 검증
        if body.step:
            if body.step.template_id is None:
                raise ValueError("❌ Step Template ID는 필수입니다")
            if body.step.step_order is None:
                raise ValueError("❌ Step Order는 필수입니다")
            if body.step.autonomous_agent_count is None:
                raise ValueError("❌ Step Agent 수는 필수입니다")
        
        # 4. Group 데이터 기본 검증
        if body.group:
            if body.group.template_id is None:
                raise ValueError("❌ Group Template ID는 필수입니다")
            if body.group.autonomous_agent_count is None:
                raise ValueError("❌ Group Agent 수는 필수입니다")

         
    async def _validate_before_creation(self, simulation_id: int, body: PatternCreateRequestDTO):
        """실제 작업 전에 모든 조건을 미리 확인"""
        async with self.sessionmaker() as session:
            # 1. Simulation 존재 여부 확인
            simulation = await self.simulation_repository.find_by_id(simulation_id, session)
            if not simulation:
                raise ValueError(f"❌ Simulation {simulation_id}를 찾을 수 없습니다")
            
            # 2. 시뮬레이션 상태 검증
            if simulation.status == SimulationStatus.RUNNING:
                raise ValueError(f"❌ 실행 중인 시뮬레이션에는 패턴을 추가할 수 없습니다")
            
            if simulation.status in SimulationStatus.COMPLETED:
                raise ValueError(f"❌ 완료된 시뮬레이션에는 패턴을 추가할 수 없습니다 (상태: {simulation.status})")
            
            # 3. 시뮬레이션 패턴 타입과 요청 패턴 일치성 검증
            if simulation.pattern_type == PatternType.SEQUENTIAL:
                if body.group:
                    raise ValueError("❌ SEQUENTIAL 시뮬레이션에서는 Group 패턴을 사용할 수 없습니다. Step 패턴만 가능합니다")
                if not body.step:
                    raise ValueError("❌ SEQUENTIAL 시뮬레이션에서는 Step 패턴이 필요합니다")
                    
            elif simulation.pattern_type == PatternType.PARALLEL:
                if body.step:
                    raise ValueError("❌ PARALLEL 시뮬레이션에서는 Step 패턴을 사용할 수 없습니다. Group 패턴만 가능합니다")
                if not body.group:
                    raise ValueError("❌ PARALLEL 시뮬레이션에서는 Group 패턴이 필요합니다")
            
            # 3. 패턴별 세부 검증 (이제 타입 일치가 보장된 상태)
            if simulation.pattern_type == PatternType.SEQUENTIAL:
                await self._validate_step_business_rules(simulation_id, body.step, session)
                
            elif simulation.pattern_type == PatternType.PARALLEL:        
                await self._validate_group_business_rules(simulation_id, body.group, session)
        
        # 4. Kubernetes 네임스페이스 존재 확인 (간단한 체크)
        try:
            # 실제로는 PodService에 간단한 연결 테스트 메서드 추가
            await PodService._validate_pod_creation_prerequisites(simulation.namespace)
            print("✅ 모든 사전 조건 확인 완료")
        except Exception as e:
            raise ValueError(f"❌ Kubernetes 연결 실패: {e}")
        
        
    async def _validate_step_business_rules(self, simulation_id: int, step_data: StepCreateDTO, session):
        existing_step = await self.simulation_repository.find_step_by_order(
            simulation_id, step_data.step_order, session
        )
            
        # Step Order 중복 확인
        if existing_step:
            raise ValueError(f"❌ Step Order {step_data.step_order}가 이미 존재합니다.")
    
        # 순차 실행 규칙 확인
        if step_data.step_order > 1:
            previous_step = await self.simulation_repository.find_step_by_order(
                simulation_id, step_data.step_order - 1, session
            )
            if not previous_step:
                raise ValueError(f"❌ Step Order {step_data.step_order - 1}이 먼저 생성되어야 합니다.")
        
        # Template 존재 여부 확인    
        template = await self.template_repository.find_by_id(step_data.template_id, session)
        if not template:
                raise ValueError(f"❌ Template {step_data.template_id}를 찾을 수 없습니다")
            
    async def _validate_group_business_rules(self, simulation_id: int, group_data: GroupCreateDTO, session):
        # Template 존재 여부 확인    
        template = await self.template_repository.find_by_id(group_data.template_id, session)
        if not template:
                raise ValueError(f"❌ Template {group_data.template_id}를 찾을 수 없습니다")
    
    async def _validate_before_deletion(self, simulation_id: int, body: PatternDeleteRequestDTO):
        """패턴 삭제 전에 조건만 확인"""
        async with self.sessionmaker() as session:
            simulation = await self.simulation_repository.find_by_id(simulation_id, session)
            if not simulation:
                raise ValueError(f"❌ Simulation {simulation_id}를 찾을 수 없습니다")
            
            # 상태 검증
            if simulation.status in {
                SimulationStatus.RUNNING,
                SimulationStatus.INITIATING,
                SimulationStatus.DELETING,
                SimulationStatus.DELETED,
            }:
                raise ValueError(f"❌ 상태 {simulation.status} 에서는 패턴을 삭제할 수 없습니다")
            
            # 타입/요청 검증
            if simulation.pattern_type == PatternType.SEQUENTIAL:
                if body.group_id:
                    raise ValueError("❌ SEQUENTIAL 시뮬레이션에서는 Group 삭제 불가")
                if not body.step_order:
                    raise ValueError("❌ SEQUENTIAL 시뮬레이션에서는 Step Order 필요")
            elif simulation.pattern_type == PatternType.PARALLEL:
                if body.step_order:
                    raise ValueError("❌ PARALLEL 시뮬레이션에서는 Step 삭제 불가")
                if not body.group_id:
                    raise ValueError("❌ PARALLEL 시뮬레이션에서는 Group ID 필요")
 
        
    # =====================================================
    # STEP 및 GROUP 패턴 메인 메서드
    # =====================================================    
    async def _create_step_pattern(self, simulation_id: int, step_data: StepCreateDTO):
        """Step 패턴 생성만 담당"""
        created_step_id = None
        created_instance_ids = []
        created_pods = []

        try:
            # Step 생성
            step_result = await self._create_step_and_instances(simulation_id, step_data)
            
            created_step_id = step_result["step_id"]
            created_instance_ids = step_result["instance_ids"]
            
            # 2. Pod 생성 (추출된 데이터 사용)
            created_pods = await self._create_pods_for_instances(
                step_result["instances_data"],
                step_result["simulation_data"], 
                step_result["step_data"]
            )
            
            return PatternCreateResponseDTO(
                statusCode=200, 
                data={"step": step_result["step_data"]},
                message="Step created successfully"
            )
            
        except Exception as e:
            # 실패 시 정리
            await self._cleanup_step_resources(created_step_id, created_instance_ids, created_pods)
            raise
    
    async def _create_group_pattern(self, simulation_id: int, group_data: GroupCreateDTO):
        """Group 패턴 생성만 담당"""
        created_group_id = None
        created_instance_ids = []
        created_pods = []

        try:
            # 1. DB에서 Group과 Instance 생성 + 데이터 추출
            print("DB에서 Group과 Instance 생성 + 데이터 추출")
            group_result = await self._create_group_and_instances(simulation_id, group_data)
            print(f"group_result: {group_result}")
            
            created_group_id = group_result["group_id"]
            created_instance_ids = group_result["instance_ids"]
            
            # 2. Pod 생성 (추출된 데이터 사용)
            print("Pod 생성 (추출된 데이터 사용)")
            created_pods = await self._create_pods_for_instances(
                group_result["instances_data"],
                group_result["simulation_data"], 
                None,  # step_data
                group_result["group_data"]
            )
            
            return PatternCreateResponseDTO(
                statusCode=200, 
                data={"group": group_result["group_data"]},
                message="Group created successfully"
            )
            
        except Exception as e:
            # 실패 시 정리
            await self._cleanup_group_resources(created_group_id, created_instance_ids, created_pods)
            raise
    
    # =====================================================
    # STEP 및 GROUP DB 작업 및 데이터 추출
    # =====================================================
    async def _create_step_and_instances(self, simulation_id: int, step_data: StepCreateDTO):
        """DB 작업과 데이터 추출을 동시에"""
        async with self.sessionmaker() as session:
            async with session.begin():
                # 1. Simulation과 Template 재조회 (세션 내에서)
                simulation = await self.simulation_repository.find_by_id(simulation_id, session)
                template = await self.template_repository.find_by_id(step_data.template_id, session)
                
                # 2. Step 생성
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
                
                # 🎯 세션이 살아있을 때 필요한 데이터 모두 추출
                simulation_data = {
                    "id": simulation.id,
                    "name": simulation.name,
                    "description": simulation.description,
                    "namespace": simulation.namespace,
                    "pattern_type": simulation.pattern_type,
                    "mec_id": simulation.mec_id
                }
                
                step_data_dict = {
                    "id": step.id,
                    "step_order": step.step_order,
                    "template_id": step.template_id,
                    "autonomous_agent_count": step.autonomous_agent_count,
                    "execution_time": step.execution_time,
                    "delay_after_completion": step.delay_after_completion,
                    "repeat_count": step.repeat_count,
                    "template": {
                        "template_id": step.template.template_id,
                        "type": step.template.type
                    } if step.template else None
                }
                
                instances_data = []
                for instance in instances:
                    instances_data.append({
                        "id": instance.id,
                        "simulation_id": instance.simulation_id,
                        "name": getattr(instance, 'name', f"instance_{instance.id}"),
                        "pod_namespace": getattr(instance, 'pod_namespace', simulation.namespace),
                        "template_id": getattr(instance, 'template_id', template.template_id),
                        "step_order": getattr(instance, 'step_order', step.step_order),
                    })
                
                return {
                    "step_id": step.id,
                    "instance_ids": [inst.id for inst in instances],
                    "simulation_data": simulation_data,
                    "step_data": step_data_dict,
                    "instances_data": instances_data
                }
           
    async def _create_group_and_instances(self, simulation_id: int, group_data: GroupCreateDTO):
        """Group DB 작업과 데이터 추출을 동시에"""
        
        async with self.sessionmaker() as session:
            async with session.begin():
                
                # 1. Simulation과 Template 재조회 (세션 내에서)
                print("Simulation과 Template 재조회 (세션 내에서)")
                simulation = await self.simulation_repository.find_by_id(simulation_id, session)
                template = await self.template_repository.find_by_id(group_data.template_id, session)
                
                # 2. Group 생성
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
                
                # 3. Instance 생성 (Group용)
                instances = await self.instance_repository.create_instances_batch(
                    simulation=simulation,
                    group=group,
                    session=session
                )
                
                # 🎯 세션이 살아있을 때 필요한 데이터 모두 추출
                simulation_data = {
                    "id": simulation.id,
                    "name": simulation.name,
                    "description": simulation.description,
                    "namespace": simulation.namespace,
                    "pattern_type": simulation.pattern_type,
                    "mec_id": simulation.mec_id
                }
                
                group_data_dict = {
                    "id": group.id,
                    "group_name": group.group_name,
                    "template_id": group.template_id,
                    "autonomous_agent_count": group.autonomous_agent_count,
                    "execution_time": group.execution_time,
                    "repeat_count": group.repeat_count,
                    "assigned_area": group.assigned_area,
                    "template": {
                        "template_id": group.template.template_id,
                        "type": group.template.type
                    } if group.template else None
                }
                
                instances_data = []
                for instance in instances:
                    instances_data.append({
                        "id": instance.id,
                        "simulation_id": instance.simulation_id,
                        "name": getattr(instance, 'name', f"group_instance_{instance.id}"),
                        "pod_namespace": getattr(instance, 'pod_namespace', simulation.namespace),
                        "template_id": getattr(instance, 'template_id', template.template_id),
                        "group_id": getattr(instance, 'group_id', group.id),
                    })
                
                return {
                    "group_id": group.id,
                    "instance_ids": [inst.id for inst in instances],
                    "simulation_data": simulation_data,
                    "group_data": group_data_dict,
                    "instances_data": instances_data
                }       
                
    # =====================================================
    # 공통 메서드 수정 (Step과 Group 모두 지원)
    # =====================================================            
    async def _create_pods_for_instances(
        self, 
        instances_data: List[Dict], 
        simulation_data: Dict, 
        step_data: Optional[Dict], 
        group_data: Optional[Dict] 
    ):
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
    async def _cleanup_step_resources(self, step_id: int = None, instance_ids: List[int] = None, created_pods: List[Dict] = None):
        """Step 리소스 정리"""
        print("🧹 Step 리소스 정리 시작")
        
        cleanup_errors = []
        
        try:
            # 1. 생성된 Pod 삭제
            if created_pods:
                print(f"  📦 {len(created_pods)}개 Pod 삭제 중...")
                for pod_info in created_pods:
                    try:
                        await PodService.delete_pod(
                            pod_name=pod_info["pod_name"],
                            namespace=pod_info["namespace"]
                        )
                        print(f"  ✅ Pod 삭제 완료: {pod_info['pod_name']}")
                    except Exception as e:
                        error_msg = f"Pod {pod_info['pod_name']} 삭제 실패: {str(e)}"
                        print(f"  ❌ {error_msg}")
                        cleanup_errors.append(error_msg)

            # 2. DB 정리
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
    
    async def _cleanup_group_resources(self, group_id: int = None, instance_ids: List[int] = None, created_pods: List[Dict] = None):
        """Group 리소스 정리"""
        print("🧹 Group 리소스 정리 시작")
        
        cleanup_errors = []
        
        try:
            # 1. 생성된 Pod 삭제
            if created_pods:
                print(f"  📦 {len(created_pods)}개 Pod 삭제 중...")
                for pod_info in created_pods:
                    try:
                        await PodService.delete_pod(
                            pod_name=pod_info["pod_name"],
                            namespace=pod_info["namespace"]
                        )
                        print(f"  ✅ Pod 삭제 완료: {pod_info['pod_name']}")
                    except Exception as e:
                        error_msg = f"Pod {pod_info['pod_name']} 삭제 실패: {str(e)}"
                        print(f"  ❌ {error_msg}")
                        cleanup_errors.append(error_msg)

            # 2. DB 정리
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