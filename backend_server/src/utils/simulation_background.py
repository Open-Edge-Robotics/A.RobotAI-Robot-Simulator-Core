import asyncio

from sqlalchemy import delete, select
from .status_update_manager import get_status_manager
from models.enums import GroupStatus, PatternType, SimulationStatus, StepStatus
from models.simulation_groups import SimulationGroup
from crud.pod import PodService
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
    overall_successful = 0
    overall_failed = 0
    overall_created = 0
    
    try:
        # 상태 관리자 초기화
        status_manager = get_status_manager()
        
        # 시뮬레이션 기본 정보 조회
        simulation = None
        async with sessionmaker() as session:
            simulation = await session.get(Simulation, simulation_id)
            if not simulation:
                raise ValueError(f"Simulation {simulation_id} not found")
            print(f"시뮬레이션 정보: 이름='{simulation.name}', 예상 Pod 수={simulation.total_expected_pods}")
        
        # 단계별 처리
        sorted_steps = sorted(steps_data, key=lambda s: s.step_order)
        print(f"총 {len(sorted_steps)}개의 단계 처리 시작")
        
        for step_index, step in enumerate(sorted_steps, 1):
            print(f"\n=== [단계 {step.step_order}] 처리 시작 ({step_index}/{len(sorted_steps)}) ===")

            
            # 단계별 처리 결과
            step_results = await process_single_step(
                sessionmaker, 
                simulation, 
                step, 
                api, 
                status_manager,
            )
            
            # 전체 결과 업데이트
            overall_successful += step_results['successful']
            overall_failed += step_results['failed'] 
            overall_created += step_results['created']
            
            # 진행 상황 업데이트
            await status_manager.update_simulation_status(
                simulation_id,
                total_pods=overall_created,
            )
            
            print(f"단계 {step.step_order} 완료: 생성={step_results['created']}, 성공={step_results['successful']}, 실패={step_results['failed']}")
        
        # 전체 완료 처리
        await finalize_simulation_success(
            simulation_id, 
            status_manager,
            overall_successful
        )
        
        print(f"=== 전체 완료: 생성={overall_created}, 성공={overall_successful}, 실패={overall_failed} ===")
        
    except Exception as e:
        print(f"ERROR: Sequential 패턴 백그라운드 작업 실패: {e}")
        await handle_simulation_failure(sessionmaker, simulation_id, str(e))
        raise
        
    print(f"--- [Sequential 패턴] 백그라운드 작업 종료 (Simulation ID: {simulation_id}) ---")


async def process_single_step(
    sessionmaker,
    simulation,
    step: SequentialStep,
    api: str,
    status_manager,
) -> dict:
    """단일 단계 처리"""
    
    step_successful = 0
    step_failed = 0
    step_created = 0
    
    # 템플릿 조회
    async with sessionmaker() as session:
        templates_service = TemplateService(session)
        template = await templates_service.find_template_by_id(step.template_id, api)
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
            repeat_count=step.repeat_count,  # 저장은 하되 Pod 생성에는 미사용
            current_repeat=1,  # 반복 없으므로 항상 1
            status=StepStatus.PENDING
        )
        session.add(simulation_step)
        await session.commit()
        print(f"시뮬레이션 단계 DB 저장 완료 (StepOrder={step.step_order})")
        
    # 시뮬레이션 설정 정보 구성
    simulation_config = {
        'bag_file_path': template.bag_file_path,
        'repeat_count': step.repeat_count,
        'max_execution_time': f"{step.execution_time}s" if step.execution_time else "3600s",
        'communication_port': 11311,  # ROS 기본 포트
        'data_format': 'ros-bag',
        'debug_mode': False,  # TODO: 환경변수나 설정파일에서 가져올 수 있음
        'log_level': 'INFO',
        'delay_after_completion': step.delay_after_completion,
        # Simulation 스키마에서 실제 사용 가능한 필드들
        'simulation_name': simulation.name,
        'pattern_type': simulation.pattern_type,
        'mec_id': simulation.mec_id
    }
    
    instance_data_list = []
    
    # Instance 정보 준비
    async with sessionmaker() as session:
        for i in range(step.autonomous_agent_count):
            instance = Instance(
                name=f"{simulation.name}_step{step.step_order}_agent_{i}",
                description=f"Step {step.step_order} - Agent {i}",
                pod_namespace=simulation.namespace,
                simulation_id=simulation.id,
                template_id=template.template_id,
                step_order=step.step_order,
            )
            session.add(instance)
            await session.flush() # ID 생성을 위해 flush
            
            # 세션 분리되어도 사용할 수 있는 데이터만 추출
            instance_data = {
                'id': instance.id,
                'name': instance.name,
                'description': instance.description,
                'pod_namespace': instance.pod_namespace,
                'simulation_id': instance.simulation_id,
                'simulation_name': simulation.name,
                'pattern_type': 'sequential',  # 순차 실행 패턴
                'template_id': instance.template_id,
                'step_order': instance.step_order
            }
            instance_data_list.append(instance_data)
        
        await session.commit()
        print(f"  -> {len(instance_data_list)}개 인스턴스 DB 저장 완료")
    
    # Pod 생성 (동시 처리로 성능 향상)
    pod_creation_tasks = []
    for instance in instance_data_list:
        task = create_single_pod_with_status(
            instance, template, status_manager, simulation_config
        )
        pod_creation_tasks.append(task)
    
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
    
    # 단계 완료 상태 업데이트
    await status_manager.update_step_status(
        simulation.id,
        step.step_order,
        successful_agents=step_successful,
        failed_agents=step_failed,
        created_pods_count=step_created
    )
    
    return {
        'successful': step_successful,
        'failed': step_failed,
        'created': step_created
    }


async def create_single_pod_with_status(instance, template, status_manager, simulation_config):
    """단일 Pod 생성 및 상태 업데이트"""
    instance_id = instance['id']
    
    try:
        # 실제 Pod 생성
        pod_name = await PodService.create_pod(instance, template, simulation_config)
        
        # 성공 상태 업데이트
        await status_manager.update_instance_status(
            instance_id,
            pod_name=pod_name
        )
        
        return pod_name
        
    except Exception as e:
        # 실패 상태 업데이트
        await status_manager.update_instance_status(
            instance_id,
            error_message=str(e),
            error_code="POD_CREATION_FAILED"
        )
        raise e

async def finalize_simulation_success(
    simulation_id: int, 
    status_manager,
    successful_count: int
):
    """시뮬레이션 성공 완료 처리"""
    await status_manager.update_simulation_status(
        simulation_id,
        status=SimulationStatus.READY,
        total_pods=successful_count
    )
    print("DB 최종 업데이트 완료: 상태='READY'")

async def handle_simulation_failure(sessionmaker, simulation_id: int, error_message: str):
    """시뮬레이션 완전 실패 처리 - 모든 관련 데이터 삭제"""
    
    print(f"시뮬레이션 {simulation_id} 실패 처리 시작: {error_message}")
    cleanup_errors = []
    
    # 1. 실패한 Pod들 정리 (가장 먼저)
    try:
        print(f"Pod 정리 시작: simulation-{simulation_id}")
        if 'cleanup_failed_simulation_pods' in globals():
            await cleanup_failed_simulation_pods(simulation_id)
        print(f"Pod 정리 완료")
    except Exception as e:
        cleanup_errors.append(f"Pod 정리 실패: {e}")
        print(f"Pod 정리 실패: {e}")
    
    # 2. DB 데이터 정리 (외래키 순서 고려)
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
                
                # 2-1. 인스턴스 삭제 (공통)
                instance_delete_stmt = delete(Instance).where(Instance.simulation_id == simulation_id)
                instance_result = await db_session.execute(instance_delete_stmt)
                print(f"인스턴스 삭제 완료: {instance_result.rowcount}개")
                
                # 2-2. 패턴별 데이터 삭제
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
                
                # 2-3. 시뮬레이션 정보 삭제 (마지막)
                await db_session.delete(simulation)
                print(f"시뮬레이션 정보 삭제 완료: {simulation_id}")
                
                # 커밋
                print(f"DB 정리 트랜잭션 커밋 완료")
                
    except Exception as e:
        cleanup_errors.append(f"DB 정리 실패: {e}")
        print(f"DB 정리 실패: {e}")
    
    # 3. 네임스페이스 정리 (마지막)
    try:
        print(f"네임스페이스 정리 시작: simulation-{simulation_id}")
        # 네임스페이스 삭제 로직 (pod_service 사용)
        pod_service = PodService()
        await pod_service.delete_namespace(simulation_id)
        print(f"네임스페이스 정리 완료")
    except Exception as e:
        cleanup_errors.append(f"네임스페이스 정리 실패: {e}")
        print(f"네임스페이스 정리 실패: {e}")
    
    # 4. 최종 결과 로깅
    if cleanup_errors:
        print(f"시뮬레이션 {simulation_id} 정리 중 발생한 오류들: {cleanup_errors}")
    else:
        print(f"시뮬레이션 {simulation_id} 완전 정리 성공")
    
async def cleanup_failed_simulation_pods(simulation_id: int):
    """실패한 시뮬레이션의 Pod들 정리"""
    print(f"[CLEANUP] 시뮬레이션 {simulation_id}의 실패/성공 Pod 정리 시작")
    status_manager = get_status_manager()
    async with status_manager.get_session() as session:
        # 생성된 모든 Instance 조회 (성공/실패 모두)
        result = await session.execute(
            select(Instance)
            .where(Instance.simulation_id == simulation_id)
            .where(Instance.pod_name.isnot(None))  # Pod가 실제 생성된 것들만
        )
        instances = result.scalars().all()
        
        pod_service = PodService()
        cleaned_count = 0
        
        for instance in instances:
            try:
                print(f"[CLEANUP] Pod 삭제 시도: {instance.pod_name}")
                await pod_service.delete_pod(instance.pod_name, instance.pod_namespace)
                
                cleaned_count += 1
                print(f"[CLEANUP] Pod 삭제 성공: {instance.pod_name}")
                
            except Exception as e:
                print(f"[CLEANUP] Pod 삭제 실패: {instance.pod_name}, 오류: {e}")
                # 삭제 실패해도 계속 진행
        
        print(f"[CLEANUP] 정리 완료: {cleaned_count}/{len(instances)} Pod 삭제 성공")
    
async def handle_parallel_pattern_background(
    sessionmaker,
    simulation_id: int,
    groups_data: list[ParallelAgent],
    api: str
):
    """병렬 패턴 백그라운드 처리"""
    print(f"\n--- [Parallel 패턴] 백그라운드 작업 시작 (Simulation ID: {simulation_id}) ---")
    
    status_manager = None
    overall_successful = 0
    overall_failed = 0
    overall_created = 0
    
    try:
        # 상태 관리자 초기화
        status_manager = get_status_manager()
        
        # 시뮬레이션 기본 정보 조회
        simulation = None
        async with sessionmaker() as session:
            simulation = await session.get(Simulation, simulation_id)
            if not simulation:
                raise ValueError(f"Simulation {simulation_id} not found")
            print(f"시뮬레이션 정보: 이름='{simulation.name}', 예상 Pod 수={simulation.total_expected_pods}")
        
        # 그룹별 처리
        print(f"총 {len(groups_data)}개의 그룹 처리 시작")
        
        # 모든 그룹을 동시에 처리
        group_tasks = []
        for group_index, group in enumerate(groups_data):
            print(f"[그룹 {group_index}] 처리 준비: 템플릿 ID={group.template_id}, Pod 수={group.autonomous_agent_count}")
            task = process_single_group(
                sessionmaker,
                simulation,
                group,
                group_index,
                api,
                status_manager
            )
            group_tasks.append(task)
        
        # 동시 그룹 처리 실행 (1개 그룹이라도 실패하면 전체 실패)
        print(f"=== {len(group_tasks)}개 그룹 동시 처리 시작 ===")
        group_results = await asyncio.gather(*group_tasks)  # return_exceptions=False로 즉시 실패 처리
        
        # 결과 집계 (모든 그룹이 성공한 경우만 여기 도달)
        for group_index, result in enumerate(group_results):
            overall_successful += result['successful']
            overall_failed += result['failed']
            overall_created += result['created']
            print(f"그룹 {group_index} 완료: 생성={result['created']}, 성공={result['successful']}, 실패={result['failed']}")
        
        # 진행 상황 업데이트
        await status_manager.update_simulation_status(
            simulation_id,
            total_pods=overall_created
        )
        
        # 전체 완료 처리
        await finalize_simulation_success(
            simulation_id,
            status_manager,
            overall_successful
        )
        
        print(f"=== 전체 완료: 생성={overall_created} ===")
        
    except Exception as e:
        print(f"ERROR: Parallel 패턴 백그라운드 작업 실패: {e}")
        await handle_simulation_failure(sessionmaker, simulation_id, str(e))
        raise
        
    print(f"--- [Parallel 패턴] 백그라운드 작업 종료 (Simulation ID: {simulation_id}) ---")

async def process_single_group(
    sessionmaker,
    simulation,
    group: ParallelAgent,
    group_index: int,
    api: str,
    status_manager
) -> dict:
    """단일 그룹 처리"""
    
    group_successful = 0
    group_failed = 0
    group_created = 0
    
    # 템플릿 조회
    async with sessionmaker() as session:
        templates_service = TemplateService(session)
        template = await templates_service.find_template_by_id(group.template_id, api)
        print(f"[그룹 {group_index}] 템플릿 조회 완료: ID={template.template_id}, 타입='{template.type}'")
    
    # SimulationGroup DB 저장
    group_id = None
    async with sessionmaker() as session:
        simulation_group = SimulationGroup(
            simulation_id=simulation.id,
            group_name=f"{simulation.name}_group_{group_index}",
            template_id=template.template_id,
            autonomous_agent_count=group.autonomous_agent_count,
            execution_time=group.execution_time,
            assigned_area=simulation.namespace,
            status=GroupStatus.PENDING
        )
        session.add(simulation_group)
        await session.flush()
        group_id = simulation_group.id
        await session.commit()
        print(f"[그룹 {group_index}] SimulationGroup DB 저장 완료 (ID: {group_id})")
        
    # 시뮬레이션 설정 정보 구성
    simulation_config = {
        'bag_file_path': template.bag_file_path,
        'repeat_count': getattr(group, 'repeat_count', 1),
        'max_execution_time': f"{group.execution_time}s" if group.execution_time else "3600s",
        'communication_port': 11311,
        'data_format': 'ros-bag',
        'debug_mode': False,
        'log_level': 'INFO',
        'pattern_type': simulation.pattern_type,
        'group_id': group_id,                    
        'group_index': group_index,                # 부가 정보로 추가
        'simulation_name': simulation.name,
        'simulation_description': simulation.description,
        'mec_id': simulation.mec_id
    }
    
    instance_data_list = []
    
    # Instance 정보 준비
    async with sessionmaker() as session:
        for i in range(group.autonomous_agent_count):
            instance = Instance(
                name=f"{simulation.name}_group{group_index}_agent_{i}",
                description=f"Group {group_index} - Agent {i}",
                pod_namespace=simulation.namespace,
                simulation_id=simulation.id,
                template_id=template.template_id,
                group_id=group_id, 
            )
            session.add(instance)
            await session.flush() # ID 생성을 위해 flush
            
            # 세션 분리되어도 사용할 수 있는 데이터만 추출
            instance_data = {
                'id': instance.id,
                'name': instance.name,
                'description': instance.description,
                'pod_namespace': instance.pod_namespace,
                'simulation_id': instance.simulation_id,
                'template_id': instance.template_id,
                'group_id': instance.group_id,      
                'group_index': group_index,           
                'pattern_type': 'parallel'          
            }
            instance_data_list.append(instance_data)
        
        await session.commit()
        print(f"[그룹 {group_index}] {len(instance_data_list)}개 인스턴스 DB 저장 완료")
    
    # Pod 생성 (동시 처리로 성능 향상)
    pod_creation_tasks = []
    for instance in instance_data_list:
        task = create_single_pod_with_status(
            instance, template, status_manager, simulation_config
        )
        pod_creation_tasks.append(task)
    
    # 동시 Pod 생성 실행
    print(f"[그룹 {group_index}] {len(pod_creation_tasks)}개 Pod 동시 생성 시작...")
    results = await asyncio.gather(*pod_creation_tasks, return_exceptions=True)
    
    # 결과 집계
    for result in results:
        group_created += 1
        if isinstance(result, Exception):
            group_failed += 1
            print(f"[그룹 {group_index}] Pod 생성 실패: {result}")
        else:
            group_successful += 1
            print(f"[그룹 {group_index}] Pod 생성 성공: {result}")
    
    # 그룹 완료 상태 업데이트
    await status_manager.update_group_status(
        simulation.id,
        group_id,
        successful_agents=group_successful,
        failed_agents=group_failed,
        created_pods_count=group_created
    )
    
    return {
        'successful': group_successful,
        'failed': group_failed,
        'created': group_created,
        'group_id': group_id
    }
