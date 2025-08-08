import asyncio
from collections import namedtuple
from datetime import timedelta, datetime, timezone

from sqlalchemy import select
from .status_update_manager import get_status_manager
from models.enums import GroupStatus, PodCreationStatus, SimulationStatus
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
        
        # 초기 상태 설정
        await status_manager.update_simulation_status(
            simulation_id,
            pod_creation_status="IN_PROGRESS"
        )
        
        # 시뮬레이션 기본 정보 조회
        simulation = None
        async with sessionmaker() as session:
            simulation = await session.get(Simulation, simulation_id)
            if not simulation:
                raise ValueError(f"Simulation {simulation_id} not found")
            print(f"시뮬레이션 정보: 이름='{simulation.name}', 예상 Pod 수={simulation.total_expected_pods}")
        
        current_time = datetime.now(timezone.utc)
        
        # 단계별 처리
        sorted_steps = sorted(steps_data, key=lambda s: s.step_order)
        print(f"총 {len(sorted_steps)}개의 단계 처리 시작")
        
        for step_index, step in enumerate(sorted_steps, 1):
            print(f"\n=== [단계 {step.step_order}] 처리 시작 ({step_index}/{len(sorted_steps)}) ===")
            
            # 단계별 상태 업데이트
            await status_manager.update_simulation_status(
                simulation_id,
                current_step_order=step.step_order
            )
            
            # 단계별 처리 결과
            step_results = await process_single_step(
                sessionmaker, 
                simulation, 
                step, 
                api, 
                status_manager,
                current_time
            )
            
            # 전체 결과 업데이트
            overall_successful += step_results['successful']
            overall_failed += step_results['failed'] 
            overall_created += step_results['created']
            
            # 진행 상황 업데이트
            await status_manager.update_simulation_status(
                simulation_id,
                total_created_pods=overall_created,
                total_successful_pods=overall_successful,
                total_failed_pods=overall_failed
            )
            
            print(f"단계 {step.step_order} 완료: 생성={step_results['created']}, 성공={step_results['successful']}, 실패={step_results['failed']}")
            
            # 과도한 실패 검사 (선택적)
            if step_results['created'] > 0:
                failure_rate = step_results['failed'] / step_results['created']
                if failure_rate > 0.8:  # 80% 이상 실패
                    print(f"단계 {step.step_order} 과도한 실패 감지 (실패율: {failure_rate:.1%})")
                    await status_manager.update_simulation_status(
                        simulation_id,
                        pod_creation_status="PARTIAL_SUCCESS"
                    )
                    # 계속 진행하지만 경고 로그 남김
            
            # 단계 간 대기 시간
            if step_index < len(sorted_steps):
                await asyncio.sleep(step.delay_after_completion)
                current_time += timedelta(seconds=step.delay_after_completion)
        
        # 전체 완료 처리
        await finalize_simulation_success(
            sessionmaker, 
            simulation_id, 
            status_manager,
            overall_successful,
            overall_failed,
            overall_created
        )
        
        print(f"=== 전체 완료: 생성={overall_created}, 성공={overall_successful}, 실패={overall_failed} ===")
        
    except Exception as e:
        print(f"ERROR: Sequential 패턴 백그라운드 작업 실패: {e}")
        await handle_simulation_failure(sessionmaker, simulation_id, status_manager, str(e))
        raise
        
    print(f"--- [Sequential 패턴] 백그라운드 작업 종료 (Simulation ID: {simulation_id}) ---")


async def process_single_step(
    sessionmaker,
    simulation,
    step: SequentialStep,
    api: str,
    status_manager,
    current_time
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
        step_start_time = current_time
        step_end_time = step_start_time + timedelta(seconds=step.execution_time)
        
        simulation_step = SimulationStep(
            simulation_id=simulation.id,
            step_order=step.step_order,
            template_id=template.template_id,
            autonomous_agent_count=step.autonomous_agent_count,
            execution_time=step.execution_time,
            delay_after_completion=step.delay_after_completion,
            repeat_count=step.repeat_count,  # 저장은 하되 Pod 생성에는 미사용
            current_repeat=1,  # 반복 없으므로 항상 1
            expected_pods_count=step.autonomous_agent_count,
            actual_start_time=step_start_time,
            actual_end_time=step_end_time,
            status="RUNNING"
        )
        session.add(simulation_step)
        await session.commit()
        print(f"시뮬레이션 단계 DB 저장 완료 (StepOrder={step.step_order})")
    
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
                status="PENDING",
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
            instance, template, status_manager
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
        status="COMPLETED",
        successful_agents=step_successful,
        failed_agents=step_failed,
        created_pods_count=step_created
    )
    
    return {
        'successful': step_successful,
        'failed': step_failed,
        'created': step_created
    }


async def create_single_pod_with_status(instance, template, status_manager):
    """단일 Pod 생성 및 상태 업데이트"""
    instance_id = instance['id']
    
    try:
        # 생성 시작 상태
        await status_manager.update_instance_status(
            instance_id, status="CREATING"
        )
        
        # 실제 Pod 생성
        pod_name = await PodService.create_pod(instance, template)
        
        # 성공 상태 업데이트
        await status_manager.update_instance_status(
            instance_id, 
            status="RUNNING",
            pod_name=pod_name
        )
        
        return pod_name
        
    except Exception as e:
        # 실패 상태 업데이트
        await status_manager.update_instance_status(
            instance_id,
            status="FAILED",
            error_message=str(e),
            error_code="POD_CREATION_FAILED"
        )
        raise e

async def finalize_simulation_success(
    sessionmaker, 
    simulation_id: int, 
    status_manager,
    successful_count: int,
    failed_count: int,
    created_count: int
):
    """시뮬레이션 성공 완료 처리"""
    
    # 상태 업데이트
    await status_manager.update_simulation_status(
        simulation_id,
        pod_creation_status="COMPLETED"
    )
    
    # DB 최종 업데이트
    async with sessionmaker() as session:
        simulation = await session.get(Simulation, simulation_id)
        if simulation:
            simulation.status = "PAUSED"
            simulation.total_agents = successful_count  # 성공한 Pod 수만 카운트
            await session.commit()
            print("DB 최종 업데이트 완료: 상태='PAUSED'")


async def handle_simulation_failure(sessionmaker, simulation_id: int, status_manager, error_message: str):
    """시뮬레이션 실패 처리"""
    
    # 상태 관리자를 통한 실패 상태 업데이트
    if status_manager:
        try:
            await status_manager.update_simulation_status(
                simulation_id,
                pod_creation_status="FAILED"
            )
        except Exception as e:
            print(f"상태 업데이트 실패: {e}")
    
    # DB 상태 업데이트
    try:
        async with sessionmaker() as session:
            simulation = await session.get(Simulation, simulation_id)
            if simulation:
                simulation.status = "ERROR"
                await session.commit()
                print(f"Simulation {simulation_id} 상태를 'ERROR'로 업데이트")
    except Exception as e:
        print(f"DB 상태 업데이트 실패: {e}")
    
    # 실패한 Pod들 정리 (옵션)
    try:
        # cleanup_failed_simulation_pods 함수가 있다면 호출
        if 'cleanup_failed_simulation_pods' in globals():
            await cleanup_failed_simulation_pods(simulation_id)
    except Exception as e:
        print(f"Pod 정리 실패: {e}")
    
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
                
                # Instance 상태 업데이트
                await status_manager.update_instance_status(
                    instance_id=instance.id, status="CANCELLED"
                )
                
                cleaned_count += 1
                print(f"[CLEANUP] Pod 삭제 성공: {instance.pod_name}")
                
            except Exception as e:
                print(f"[CLEANUP] Pod 삭제 실패: {instance.pod_name}, 오류: {e}")
                # 삭제 실패해도 계속 진행
        
        print(f"[CLEANUP] 정리 완료: {cleaned_count}/{len(instances)} Pod 삭제 성공")
    
async def handle_parallel_pattern_background(
    sessionmaker,
    simulation_id: int,
    agents_data: list[ParallelAgent],
    api: str
):
    """
    병렬 패턴 백그라운드 처리
    - 상태 업데이트를 최소화하여 세션 충돌 방지
    - 배치 처리 방식으로 안정성 확보
    """
    print(f"\n--- [Parallel 패턴] 백그라운드 작업 시작 (Simulation ID: {simulation_id}) ---")
    
    status_manager = None
    overall_successful = 0
    overall_failed = 0
    overall_created = 0
    
    try:
        # 상태 관리자 초기화
        status_manager = get_status_manager()
        
        # 초기 상태 설정
        await status_manager.update_simulation_status(
            simulation_id,
            pod_creation_status=PodCreationStatus.IN_PROGRESS
        )
        
        # 1. 시뮬레이션 정보 조회
        print("📋 [단계1] 시뮬레이션 정보 조회 중...")
        simulation_data = None
        async with sessionmaker() as session:
            simulation = await session.get(Simulation, simulation_id)
            if not simulation:
                raise ValueError(f"❌ Simulation {simulation_id}를 찾을 수 없습니다!")
            
            simulation_data = {
                'id': simulation.id,
                'name': simulation.name,
                'namespace': simulation.namespace,
                'total_expected_pods': simulation.total_expected_pods
            }
            
            print(f"시뮬레이션 정보: 이름='{simulation_data['name']}', 예상 Pod 수={simulation_data['total_expected_pods']}")
            
        # 2. 모든 그룹 확인 및 준비
        if not agents_data:
            raise ValueError("에이전트 데이터가 없습니다!")
        
        print(f"총 {len(agents_data)}개 그룹을 처리할 예정!")
        total_expected_pods = 0
        
        for i, agent in enumerate(agents_data):
            total_expected_pods += agent.autonomous_agent_count
            print(f"   - [그룹 {i}] 템플릿 ID: {agent.template_id}, Pod 수: {agent.autonomous_agent_count}")
        
        print(f"🎯 총 예상 Pod 수: {total_expected_pods}개")
        
        # 3. 그룹 메타데이터 순차 생성
        print(f"\n⚙️ [단계2] 그룹 메타데이터 순차 생성...")
        group_metadata_list = []
        
        for group_index, agent in enumerate(agents_data):
            print(f"   📝 [그룹 {group_index}] 메타데이터 생성 중...")
            
            try:
                # ✅ 하나의 세션에서 템플릿 조회 + SimulationGroup 생성
                async with sessionmaker() as session:
                    # 1. 템플릿 조회
                    templates_service = TemplateService(session)
                    template = await templates_service.find_template_by_id(agent.template_id, api)
                    print(f"      템플릿 조회 완료: {template.type}")
                    
                    # 2. SimulationGroup 생성
                    current_time = datetime.now(timezone.utc)
                    group_end_time = current_time + timedelta(seconds=agent.execution_time)
                    
                    simulation_group = SimulationGroup(
                        simulation_id=simulation_data['id'],
                        group_name=f"{simulation_data['name']}_parallel_group_{agent.template_id}_{group_index}",
                        template_id=agent.template_id,
                        autonomous_agent_count=agent.autonomous_agent_count,
                        execution_time=agent.execution_time,
                        assigned_area=simulation_data['namespace'],
                        expected_pods_count=agent.autonomous_agent_count,
                        actual_start_time=current_time,
                        actual_end_time=group_end_time,
                        status=GroupStatus.RUNNING
                    )
                    session.add(simulation_group)
                    await session.flush()
                    
                    group_id = simulation_group.id
                    print(f"      [그룹 {group_index}] SimulationGroup 생성 완료 (ID: {group_id})")
                    
                    # 3. 세션 내에서 필요한 데이터만 추출
                    template_data = {
                        'template_id': template.template_id,
                        'type': template.type,
                        'description': template.description,
                        'bag_file_path': template.bag_file_path,
                        'topics': template.topics
                    }
                    
                    agent_data = {
                        'template_id': agent.template_id,
                        'autonomous_agent_count': agent.autonomous_agent_count,
                        'execution_time': agent.execution_time
                    }
                    
                    # 4. 메타데이터 구성 (세션 내에서)
                    group_metadata = {
                        'group_index': group_index,
                        'group_id': group_id,
                        'agent_data': agent_data,
                        'template_data': template_data,
                        'simulation_data': simulation_data
                    }
                    
                # 세션 종료 후 안전하게 추가
                group_metadata_list.append(group_metadata)
                
                print(f"      [그룹 {group_index}] 메타데이터 수집 완료")
                
            except Exception as e:
                print(f"      ❌ [그룹 {group_index}] 메타데이터 생성 실패: {e}")
                raise
        
        print(f"✅ 모든 그룹 메타데이터 생성 완료!")
        
        # 4. 모든 그룹 동시 Pod 생성!
        print(f"\n⚡ [단계3] {len(group_metadata_list)}개 그룹 동시 Pod 생성 시작!")
        print("💫 모든 그룹이 동시에 시작됩니다...")
        
        # 그룹별 Pod 생성 태스크 생성
        group_tasks = []
        for group_metadata in group_metadata_list:
            print(f"   🎯 [그룹 {group_metadata['group_index']}] 태스크 준비 완료")
            task = process_group_pod_creation_safe(
                sessionmaker,
                group_metadata,
                status_manager
            )
            group_tasks.append(task)
        
        print(f"\n🚀 {len(group_tasks)}개 그룹 동시 실행 시작!")
        print("=" * 60)
        
        overall_start_time = datetime.now()
        
        # 🎯 핵심! 모든 그룹의 Pod 생성을 동시에 처리
        group_results = await asyncio.gather(*group_tasks, return_exceptions=True)
        
        overall_end_time = datetime.now()
        total_elapsed_time = (overall_end_time - overall_start_time).total_seconds()
        
        print("=" * 60)
        print(f"🎉 모든 그룹 동시 처리 완료!")
        print(f"⏱️ 전체 소요시간: {total_elapsed_time:.2f}초")
        
        # 5. 전체 결과 집계 및 분석
        print(f"\n📊 [단계4] 전체 결과 분석...")
        successful_groups = []
        failed_groups = []
        
        for group_index, result in enumerate(group_results):
            if isinstance(result, Exception):
                failed_groups.append({
                    'group_index': group_index,
                    'template_id': agents_data[group_index].template_id,
                    'error': str(result)
                })
                print(f"   ❌ [그룹 {group_index}] 실패: {result}")
            else:
                successful_groups.append({
                    'group_index': group_index,
                    'template_id': agents_data[group_index].template_id,
                    **result
                })
                overall_successful += result['successful']
                overall_failed += result['failed']
                overall_created += result['created']
                print(f"   ✅ [그룹 {group_index}] 성공: "
                      f"생성 {result['created']}, 성공 {result['successful']}, 실패 {result['failed']}")
        
        # 6. 최종 상태 일괄 업데이트
        print(f"\n📝 [단계5] 최종 상태 일괄 업데이트...")
        
        # 시뮬레이션 진행 상황 업데이트
        await status_manager.update_simulation_status(
            simulation_id,
            total_created_pods=overall_created,
            total_successful_pods=overall_successful,
            total_failed_pods=overall_failed
        )
        
        # 그룹별 최종 상태 일괄 업데이트
        await update_all_groups_final_status(
            sessionmaker, 
            group_metadata_list, 
            group_results
        )
        
        print(f"📈 최종 결과: 생성={overall_created}, 성공={overall_successful}, 실패={overall_failed}")
        
        # 과도한 실패 검사
        if overall_created > 0:
            failure_rate = overall_failed / overall_created
            if failure_rate > 0.8:  # 80% 이상 실패
                print(f"과도한 실패 감지 (실패율: {failure_rate:.1%})")
                await status_manager.update_simulation_status(
                    simulation_id,
                    pod_creation_status="PARTIAL_SUCCESS"
                )
            else:
                # 전체 완료 처리
                await finalize_simulation_success(
                    sessionmaker, 
                    simulation_id, 
                    status_manager,
                    overall_successful,
                    overall_failed,
                    overall_created
                )
        else:
            # Pod가 하나도 생성되지 않은 경우
            await status_manager.update_simulation_status(
                simulation_id,
                pod_creation_status="FAILED"
            )
        
        print(f"=== 병렬 패턴 완료: 생성={overall_created}, 성공={overall_successful}, 실패={overall_failed} ===")
        
    except Exception as e:
        print(f"ERROR: Parallel 패턴 백그라운드 작업 실패: {e}")
        await handle_simulation_failure(sessionmaker, simulation_id, status_manager, str(e))
        raise

    print(f"--- [Parallel 패턴] 백그라운드 작업 종료 (Simulation ID: {simulation_id}) ---")

async def process_group_pod_creation_safe(
    sessionmaker,
    group_metadata: dict,
    status_manager
) -> dict:
    """
    그룹별 Pod 생성 처리
    """
    
    group_index = group_metadata['group_index']
    group_id = group_metadata['group_id']
    agent_data = group_metadata['agent_data']
    template_data = group_metadata['template_data']
    simulation_data = group_metadata['simulation_data']
    
    group_successful = 0
    group_failed = 0
    group_created = 0
    
    print(f"🎯 [그룹 {group_index}] Pod 생성 시작! (ID: {group_id})")
    
    try:
        # 1. 그룹 내 인스턴스들 생성
        print(f"   [그룹 {group_index}] 인스턴스 {agent_data['autonomous_agent_count']}개 생성 중...")
        instance_data_list = []
        
        async with sessionmaker() as session:
            instances = []
            
            for i in range(agent_data['autonomous_agent_count']):
                instance = Instance(
                    name=f"{simulation_data['name']}_group{group_index}_pod_{i}",
                    description=f"그룹 {group_index} Pod {i} (병렬 처리)",
                    pod_namespace=simulation_data['namespace'],
                    simulation_id=simulation_data['id'],
                    template_id=template_data['template_id'],
                    status="PENDING",
                    group_id=group_id,  # 그룹과 연결
                )
                session.add(instance)
                instances.append(instance)
            
            await session.flush()
            
            # 인스턴스 데이터 추출
            for instance in instances:
                instance_data = {
                    'id': instance.id,
                    'name': instance.name,
                    'description': instance.description,
                    'pod_namespace': instance.pod_namespace,
                    'simulation_id': instance.simulation_id,
                    'template_id': instance.template_id,
                    'status': instance.status,
                    'group_id': instance.group_id
                }
                instance_data_list.append(instance_data)
            
            await session.commit()
            print(f"   [그룹 {group_index}] 인스턴스 {len(instance_data_list)}개 DB 저장 완료!")
        
        # 2. 그룹 내 Pod들 동시 생성
        print(f"   [그룹 {group_index}] Pod {len(instance_data_list)}개 동시 생성 시작!")
        
        # ✅ template_data에서 Pod 생성용 객체 재구성
        Template = namedtuple('Template', ['template_id', 'type', 'description', 'bag_file_path', 'topics'])
        template_obj = Template(**template_data)
        
        pod_creation_tasks = []
        for i, instance_data in enumerate(instance_data_list):
            task = create_single_pod_with_status(instance_data, template_obj, status_manager)
            pod_creation_tasks.append(task)
        
        group_start_time = datetime.now()
        results = await asyncio.gather(*pod_creation_tasks, return_exceptions=True)
        group_end_time = datetime.now()
        group_elapsed_time = (group_end_time - group_start_time).total_seconds()
        
        print(f"   [그룹 {group_index}] Pod 동시 생성 완료! 소요시간: {group_elapsed_time:.2f}초")
        
        # 3. 결과 집계
        successful_pods = []
        failed_pods = []
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                failed_pods.append({'index': i, 'error': str(result)})
                group_failed += 1
                print(f"      [그룹 {group_index}] Pod {i} 실패: {result}")
            else:
                successful_pods.append({'index': i, 'pod_name': result})
                group_successful += 1
                print(f"      [그룹 {group_index}] Pod {i} 성공: {result}")
        
        group_created = len(results)
        
        print(f"✅ [그룹 {group_index}] Pod 생성 완료! 생성: {group_created}, 성공: {group_successful}, 실패: {group_failed}")
        
        return {
            'successful': group_successful,
            'failed': group_failed,
            'created': group_created,
            'elapsed_time': group_elapsed_time,
            'group_id': group_id
        }
        
    except Exception as e:
        print(f"❌ [그룹 {group_index}] 전체 실패: {e}")
        raise e

async def update_all_groups_final_status(
    sessionmaker,
    group_metadata_list: list[dict],
    group_results: list
):
    """
    모든 그룹의 최종 상태를 일괄 업데이트
    (별도 세션에서 안전하게 처리)
    """
    print("📝 그룹별 최종 상태 일괄 업데이트 시작...")
    
    try:
        async with sessionmaker() as session:
            updated_count = 0
            
            for i, group_metadata in enumerate(group_metadata_list):
                group_id = group_metadata['group_id']
                group_index = group_metadata['group_index']
                
                # 결과 확인
                if i < len(group_results) and not isinstance(group_results[i], Exception):
                    result = group_results[i]
                    
                    # 그룹 상태 결정
                    final_status = "COMPLETED"
                    if result['created'] > 0:
                        failure_rate = result['failed'] / result['created']
                        if failure_rate > 0.8:
                            final_status = "PARTIAL_SUCCESS"
                    
                    # 그룹 업데이트
                    group = await session.get(SimulationGroup, group_id)
                    if group:
                        group.status = final_status
                        group.successful_agents = result['successful']
                        group.failed_agents = result['failed']
                        group.created_pods_count = result['created']
                        group.execution_completed_at = datetime.now()
                        group.updated_at = datetime.now()
                        session.add(group)
                        updated_count += 1
                        
                        print(f"   ✅ [그룹 {group_index}] 상태 업데이트: {final_status}")
                else:
                    # 실패한 그룹
                    group = await session.get(SimulationGroup, group_id)
                    if group:
                        group.status = "FAILED"
                        group.execution_completed_at = datetime.now()
                        group.updated_at = datetime.now()
                        session.add(group)
                        updated_count += 1
                        
                        print(f"   ❌ [그룹 {group_index}] 상태 업데이트: FAILED")
            
            await session.commit()
            print(f"📝 그룹 상태 일괄 업데이트 완료: {updated_count}개 그룹")
            
    except Exception as e:
        print(f"❌ 그룹 상태 일괄 업데이트 실패 (무시하고 계속): {e}")