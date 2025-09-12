import asyncio
import traceback
from typing import Annotated, Dict, List

from fastapi import Depends
from crud.pod import PodService
from crud.simulation import SimulationRepository
from models.enums import PatternType, SimulationStatus
from database.db_conn import AsyncSession, async_sessionmaker, get_async_sessionmaker
from repositories.simulation_repository import SimulationRepository
from repositories.instance_repository import InstanceRepository
from repositories.template_repository import TemplateRepository
from schemas.simulation_pattern import (
    GroupCreateDTO,
    PatternCreateRequestDTO,
    PatternCreateResponseDTO,
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
            group_result = await self._create_group_and_instances(simulation_id, group_data)
            
            created_group_id = group_result["group_id"]
            created_instance_ids = group_result["instance_ids"]
            
            # 2. Pod 생성 (추출된 데이터 사용)
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
                simulation = await self.simulation_repository.find_by_id(simulation_id, session)
                template = await self.template_repository.find_by_id(group_data.template_id, session)
                
                # 2. Group 생성
                group = await self.simulation_repository.create_simulation_group(
                    session=session,
                    simulation_id=simulation.id,
                    group_name=f"{simulation.name}_group_{int(time.time())}",  # 유니크한 그룹명
                    template_id=template.template_id,
                    autonomous_agent_count=group_data.autonomous_agent_count,
                    repeat_count=group_data.repeat_count,
                    execution_time=group_data.execution_time,
                    assigned_area=simulation.namespace,
                )
                
                # 3. Instance 생성 (Group용)
                instances = await self.instance_repository.create_instances_for_group(
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
    async def _create_pods_for_instances(self, instances_data: List[Dict], simulation_data: Dict, step_data: Dict):
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
                        None  # group_data
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