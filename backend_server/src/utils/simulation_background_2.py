from sqlalchemy import delete

from exception.simulation_exceptions import SimulationNotFoundError
from exception.template_exceptions import TemplateNotFoundError
from utils.simulation_utils import generate_final_group_name, generate_instance_name, generate_temp_group_name
from database.minio_conn import get_storage_client
from .status_update_manager import StatusUpdateManager, get_status_manager
from models.enums import GroupStatus, PatternType, SimulationStatus, StepStatus
from models.simulation_groups import SimulationGroup
from crud.template import TemplateService
from models.instance import Instance
from models.simulation import Simulation
from models.simulation_steps import SimulationStep
from schemas.simulation import ParallelAgent, SequentialStep

async def handle_sequential_pattern_background(
    sessionmaker,
    simulation_id: int,
    steps_data: list[SequentialStep],
    api: str
):
    print(f"\n--- [Sequential 패턴] 백그라운드 작업 시작 (Simulation ID: {simulation_id}) ---")
    
    status_manager = None
    overall_created_instances = 0
    
    try:
        # 상태 관리자 초기화
        status_manager = get_status_manager()
        
        # 시뮬레이션 기본 정보 조회
        simulation = None
        async with sessionmaker() as session:
            simulation = await session.get(Simulation, simulation_id)
            if not simulation:
                raise SimulationNotFoundError(simulation.id)
            # 변경: Pod 수 대신 인스턴스 수로 변경
            print(f"시뮬레이션 정보: 이름='{simulation.name}', 예상 인스턴스 수={simulation.total_expected_pods}")
        
        # 단계별 처리
        sorted_steps = sorted(steps_data, key=lambda s: s.step_order)
        print(f"총 {len(sorted_steps)}개의 단계 처리 시작")
        
        for step_index, step in enumerate(sorted_steps, 1):
            print(f"\n=== [단계 {step.step_order}] 처리 시작 ({step_index}/{len(sorted_steps)}) ===")

            # 단계별 처리 결과
            step_results = await process_single_step_without_pod(
                sessionmaker, 
                simulation, 
                step, 
                api, 
                status_manager,
            )
            
            overall_created_instances += step_results['created_instances']
            
            await status_manager.update_simulation_status(
                simulation_id,
                total_instances=overall_created_instances,
            )
            
            print(f"단계 {step.step_order} 완료: 인스턴스 생성={step_results['created_instances']}")
        
        await finalize_simulation_success(
            simulation_id, 
            status_manager,
            overall_created_instances
        )
        
        print(f"=== 전체 완료: 인스턴스 생성={overall_created_instances} ===")
        
    except Exception as e:
        print(f"ERROR: Sequential 패턴 백그라운드 작업 실패: {e}")
        await handle_simulation_failure_without_pod(sessionmaker, simulation_id, str(e))
        raise
        
    print(f"--- [Sequential 패턴] 백그라운드 작업 종료 (Simulation ID: {simulation_id}) ---")

async def process_single_step_without_pod(
    sessionmaker,
    simulation,
    step: SequentialStep,
    api: str,
    status_manager: StatusUpdateManager,
) -> dict:
    """단일 단계 처리 - Pod 생성 없이 DB 저장만"""
    
    # 변경: Pod 관련 집계 변수 제거
    step_created_instances = 0
    
    # 템플릿 조회
    storage_client = get_storage_client()
    template_service = TemplateService(session_factory=sessionmaker, storage_client=storage_client)
    template = await template_service.find_template_by_id(step.template_id)
    if template is None:
        raise TemplateNotFoundError(template.id)
    print(f"템플릿 조회 완료: ID={template.template_id}, 타입='{template.type}'")
    
    # SimulationStep DB 저장
    simulation_step = None
    async with sessionmaker() as session:
        simulation_step = SimulationStep(
            simulation_id=simulation.id,
            step_order=step.step_order,
            template_id=template.template_id,
            autonomous_agent_count=step.autonomous_agent_count,
            execution_time=step.execution_time,
            delay_after_completion=step.delay_after_completion,
            repeat_count=step.repeat_count,
            current_repeat=0,
            status=StepStatus.PENDING
        )
        session.add(simulation_step)
        await session.commit()
        print(f"시뮬레이션 단계 DB 저장 완료 (StepOrder={step.step_order})")
    
    # Instance 정보 준비 및 DB 저장
    async with sessionmaker() as session:
        for _ in range(step.autonomous_agent_count):
            instance = Instance(
                name=generate_instance_name(simulation_id=simulation.id, step_order=step.step_order),
                description=f"Step {step.step_order} - Instance",
                pod_namespace=simulation.namespace,
                simulation_id=simulation.id,
                template_id=template.template_id,
                step_order=step.step_order,
            )
            session.add(instance)
            step_created_instances += 1
        
        await session.commit()
        print(f"  -> {step_created_instances}개 인스턴스 DB 저장 완료")
    
    await status_manager.update_step_status(
        simulation.id,
        step.step_order,
        created_instances_count=step_created_instances
    )
    
    return {
        'created_instances': step_created_instances
    }


async def finalize_simulation_success(
    simulation_id: int, 
    status_manager: StatusUpdateManager,
    total_instances: int 
):
    """시뮬레이션 성공 완료 처리 - Pod 생성 없이"""
    await status_manager.update_simulation_status(
        simulation_id,
        status=SimulationStatus.PENDING, 
        total_instances=total_instances 
    )
    print("DB 최종 업데이트 완료: 상태='PENDING' (Pod 생성 준비 완료)")

async def handle_simulation_failure_without_pod(sessionmaker, simulation_id: int, error_message: str):
    """시뮬레이션 실패 처리 - Pod 정리 없이 DB 정리만"""
    
    print(f"시뮬레이션 {simulation_id} 실패 처리 시작: {error_message}")
    
    # DB 데이터 정리만 수행
    try:
        async with sessionmaker() as db_session:
            async with db_session.begin():
                # 시뮬레이션 정보 조회
                simulation = await db_session.get(Simulation, simulation_id)
                if not simulation:
                    print(f"시뮬레이션 {simulation_id}를 찾을 수 없음")
                    return
                
                pattern_type = simulation.pattern_type
                print(f"패턴 타입: {pattern_type}")
                
                # 인스턴스 삭제 (공통)
                instance_delete_stmt = delete(Instance).where(Instance.simulation_id == simulation_id)
                instance_result = await db_session.execute(instance_delete_stmt)
                print(f"인스턴스 삭제 완료: {instance_result.rowcount}개")
                
                # 패턴별 데이터 삭제
                if pattern_type == PatternType.SEQUENTIAL:
                    # 순차 패턴: SimulationStep 삭제
                    step_delete_stmt = delete(SimulationStep).where(SimulationStep.simulation_id == simulation_id)
                    step_result = await db_session.execute(step_delete_stmt)
                    print(f"SimulationStep 삭제 완료: {step_result.rowcount}개")
                    
                elif pattern_type == PatternType.PARALLEL:
                    # 병렬 패턴: SimulationGroup 삭제
                    group_delete_stmt = delete(SimulationGroup).where(SimulationGroup.simulation_id == simulation_id)
                    group_result = await db_session.execute(group_delete_stmt)
                    print(f"SimulationGroup 삭제 완료: {group_result.rowcount}개")
                
                # 시뮬레이션 정보 삭제
                await db_session.delete(simulation)
                print(f"시뮬레이션 정보 삭제 완료: {simulation_id}")
                
                print(f"DB 정리 트랜잭션 커밋 완료")
                
    except Exception as e:
        print(f"DB 정리 실패: {e}")
        raise
    
    print(f"시뮬레이션 {simulation_id} DB 정리 완료")
    
async def handle_parallel_pattern_background(
    sessionmaker,
    simulation_id: int,
    groups_data: list[ParallelAgent],
    api: str
):
    """병렬 패턴 백그라운드 처리 - Pod 생성 제거"""
    print(f"\n--- [Parallel 패턴] 백그라운드 작업 시작 (Simulation ID: {simulation_id}) ---")
    
    status_manager = None
    overall_created_instances = 0
    
    try:
        # 상태 관리자 초기화
        status_manager = get_status_manager()
        
        # 시뮬레이션 기본 정보 조회
        simulation = None
        async with sessionmaker() as session:
            simulation = await session.get(Simulation, simulation_id)
            if not simulation:
                raise SimulationNotFoundError(simulation.id)
            print(f"시뮬레이션 정보: 이름='{simulation.name}', 예상 인스턴스 수={simulation.total_expected_pods}")
        
        # 그룹별 처리
        print(f"총 {len(groups_data)}개의 그룹 처리 시작")
        
        for group_index, group in enumerate(groups_data):
            print(f"[그룹 {group_index}] 처리 시작: 템플릿 ID={group.template_id}, 인스턴스 수={group.autonomous_agent_count}")
            
            result = await process_single_group_without_pod(
                sessionmaker,
                simulation,
                group,
                group_index,
                api,
                status_manager
            )
            
            overall_created_instances += result['created_instances']
            print(f"그룹 {group_index} 완료: 인스턴스 생성={result['created_instances']}")
        
        await status_manager.update_simulation_status(
            simulation_id,
            total_instances=overall_created_instances
        )
        
        await finalize_simulation_success(
            simulation_id,
            status_manager,
            overall_created_instances
        )
        
        print(f"=== 전체 완료: 인스턴스 생성={overall_created_instances} ===")
        
    except Exception as e:
        print(f"ERROR: Parallel 패턴 백그라운드 작업 실패: {e}")
        await handle_simulation_failure_without_pod(sessionmaker, simulation_id, str(e))
        raise
        
    print(f"--- [Parallel 패턴] 백그라운드 작업 종료 (Simulation ID: {simulation_id}) ---")

async def process_single_group_without_pod(
    sessionmaker,
    simulation,
    group: ParallelAgent,
    group_index: int,
    api: str,
    status_manager: StatusUpdateManager
) -> dict:
    """단일 그룹 처리 - Pod 생성 없이 DB 저장만"""
    
    group_created_instances = 0
    
    # 템플릿 조회
    async with sessionmaker() as db:
        storage_client = get_storage_client()
        template_service = TemplateService(
            session_factory=sessionmaker, 
            storage_client=storage_client
        )
        template = await template_service.find_template_by_id(group.template_id)
        if template is None:
            raise TemplateNotFoundError(template.id)
        print(f"[그룹 {group_index}] 템플릿 조회 완료: ID={template.template_id}, 타입='{template.type}'")
    
    # SimulationGroup DB 저장
    group_id = None
    async with sessionmaker() as session:
        # 임시 이름 생성
        temp_name = generate_temp_group_name(simulation.id)
        
        simulation_group = SimulationGroup(
            simulation_id=simulation.id,
            group_name=temp_name,
            template_id=template.template_id,
            autonomous_agent_count=group.autonomous_agent_count,
            repeat_count=group.repeat_count,
            current_repeat=0,
            execution_time=group.execution_time,
            assigned_area=simulation.namespace,
            status=GroupStatus.PENDING
        )
        session.add(simulation_group)
        await session.flush()
        group_id = simulation_group.id
        
        # 최종 이름 업데이트
        simulation_group.group_name = generate_final_group_name(simulation.id, group_id)
        
        await session.commit()
        print(f"[그룹 {group_index}] SimulationGroup DB 저장 완료 (ID: {group_id})")
    
    # Instance 정보 준비 및 DB 저장
    async with sessionmaker() as session:
        for _ in range(group.autonomous_agent_count):
            instance = Instance(
                name=generate_instance_name(simulation_id=simulation.id, group_id=group_id),
                description=f"Group {group_id} - Instance",
                pod_namespace=simulation.namespace,
                simulation_id=simulation.id,
                template_id=template.template_id,
                group_id=group_id, 
            )
            session.add(instance)
            group_created_instances += 1
        
        await session.commit()
        print(f"[그룹 {group_index}] {group_created_instances}개 인스턴스 DB 저장 완료")

    await status_manager.update_group_status(
        simulation.id,
        group_id,
        created_instances_count=group_created_instances
    )
    
    return {
        'created_instances': group_created_instances,
        'group_id': group_id
    }