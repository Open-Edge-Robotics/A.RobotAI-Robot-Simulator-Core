import asyncio
from datetime import timedelta, datetime, timezone

from sqlalchemy import select
from .status_update_manager import get_status_manager
from models.enums import GroupStatus, PodCreationStatus
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
    """단일 단계 처리 (반복 로직 제거됨)"""
    
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
    
    # Pod 생성 (반복 없이 지정된 수만큼만 생성)
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

# async def handle_sequential_pattern_background(
#     sessionmaker,
#     simulation_id: int,
#     steps_data: list[SequentialStep],
#     api: str
# ):
#     print(f"\n--- [Sequential 패턴 MVP] 백그라운드 작업 시작 (Simulation ID: {simulation_id}) ---")
    
#     try:
#         status_manager = get_status_manager()
#         # 🆕 MVP: 초기 상태 설정
#         await status_manager.update_simulation_status(
#             simulation_id,
#             pod_creation_status="IN_PROGRESS",
#             current_step_order=1,
#             current_step_repeat=1
#         )
        
#         async with sessionmaker() as session:
#             print(f"Session 열림. Simulation {simulation_id} 조회 중...")
#             templates_service = TemplateService(session)
#             pod_service = PodService()
            
#             simulation = await session.get(Simulation, simulation_id)
#             if not simulation:
#                 print(f"ERROR: Simulation {simulation_id}를 찾을 수 없습니다.")
#                 raise ValueError(f"Simulation {simulation_id} not found")
            
#             print(f"시뮬레이션 정보: 이름='{simulation.name}', 예상 Pod 수={simulation.total_expected_pods}")
            
#             current_time = datetime.now(timezone.utc)
#             overall_successful = 0
#             overall_failed = 0
#             overall_created = 0

#             print(f"총 {len(steps_data)}개의 단계 처리 시작.")
#             for step in sorted(steps_data, key=lambda s: s.step_order):
#                 print(f"\n- [단계 {step.step_order}] 처리 시작 -")
                
#                 # 🆕 MVP: 단계 시작 상태 업데이트
#                 await status_manager.update_simulation_status(
#                     simulation_id,
#                     current_step_order=step.step_order,
#                     current_step_repeat=1
#                 )
                
#                 template = await templates_service.find_template_by_id(step.template_id, api)
#                 print(f"템플릿 조회 완료: ID={template.template_id}, 타입='{template.type}'")

#                 step_start_time = current_time
#                 step_end_time = step_start_time + timedelta(seconds=step.execution_time)

#                 # 🆕 MVP: repeat_count 포함하여 SimulationStep 생성
#                 simulation_step = SimulationStep(
#                     simulation_id=simulation.id,
#                     step_order=step.step_order,
#                     template_id=template.template_id,
#                     autonomous_agent_count=step.autonomous_agent_count,
#                     execution_time=step.execution_time,
#                     delay_after_completion=step.delay_after_completion,
#                     repeat_count=step.repeat_count,  # 🆕 MVP
#                     current_repeat=0,  # 🆕 MVP
#                     expected_pods_count=step.autonomous_agent_count ,  # 🆕 MVP
#                     actual_start_time=step_start_time,
#                     actual_end_time=step_end_time,
#                     status="RUNNING"  # 🆕 MVP
#                 )
#                 session.add(simulation_step)
#                 await session.flush()
#                 print(f"시뮬레이션 단계 (StepOrder={step.step_order}) DB에 추가 완료 (repeat_count={step.repeat_count})")

#                 # 🆕 MVP: 반복 실행 처리
#                 step_successful = 0
#                 step_failed = 0
#                 step_created = 0
                
#                 for repeat in range(1, step.repeat_count + 1):
#                     print(f"\n  === 단계 {step.step_order} - {repeat}/{step.repeat_count}번째 반복 ===")
                    
#                     # 🆕 MVP: 반복 상태 업데이트
#                     await status_manager.update_step_status(
#                         simulation_id, step.step_order, current_repeat=repeat
#                     )
#                     await status_manager.update_simulation_status(
#                         simulation_id, current_step_repeat=repeat
#                     )

#                     # 이 반복에서의 Pod 생성
#                     for i in range(step.autonomous_agent_count):
#                         # 🆕 MVP: step_order 포함하여 Instance 생성
#                         instance = Instance(
#                             name=f"{simulation.name}_step{step.step_order}_repeat{repeat}_agent_{i}",
#                             description=f"Step {step.step_order} Repeat {repeat} - Agent {i}",
#                             pod_namespace=simulation.namespace,
#                             simulation_id=simulation.id,
#                             simulation=simulation,
#                             template_id=template.template_id,
#                             template=template,
#                             status="PENDING",  # 🆕 MVP
#                             step_order=step.step_order,  # 🆕 MVP
#                         )
#                         session.add(instance)
#                         await session.flush()
#                         print(f"    -> 인스턴스 {instance.name} DB에 추가 완료.")

#                         # 🆕 MVP: Pod 생성 with 상태 추적
#                         try:
#                             # 생성 시작 상태
#                             await status_manager.update_instance_status(
#                                 instance.id, status="CREATING"
#                             )
                            
#                             # 실제 Pod 생성
#                             print(f"    -> Pod 생성 요청 중... (인스턴스 ID: {instance.id})")
#                             pod_name = await pod_service.create_pod(instance, template)
                            
#                             # 성공 상태 업데이트
#                             await status_manager.update_instance_status(
#                                 instance.id, 
#                                 status="RUNNING",
#                                 pod_name=pod_name
#                             )
                            
#                             step_successful += 1
#                             step_created += 1
#                             print(f"    -> Pod 생성 성공: {pod_name}")
                            
#                         except Exception as e:
#                             # 🆕 MVP: 실패 상태 업데이트
#                             print(f"    -> Pod 생성 실패: {e}")
#                             await status_manager.update_instance_status(
#                                 instance.id,
#                                 status="FAILED",
#                                 error_message=str(e),
#                                 error_code="POD_CREATION_FAILED"
#                             )
                            
#                             step_failed += 1
#                             step_created += 1

#                     # 반복 간 지연 (마지막 반복 제외)
#                     if repeat < step.repeat_count:
#                         print(f"    -> 반복 간 대기: {step.delay_after_completion}초")
#                         await asyncio.sleep(step.delay_after_completion)

#                 # 🆕 MVP: 단계 완료 후 상태 업데이트
#                 overall_successful += step_successful
#                 overall_failed += step_failed
#                 overall_created += step_created
                
#                 await status_manager.update_step_status(
#                     simulation_id,
#                     step.step_order,
#                     status="COMPLETED",
#                     successful_agents=step_successful,
#                     failed_agents=step_failed,
#                     created_pods_count=step_created
#                 )
                
#                 await status_manager.update_simulation_status(
#                     simulation_id,
#                     total_created_pods=overall_created,
#                     total_successful_pods=overall_successful,
#                     total_failed_pods=overall_failed
#                 )

#                 # 🆕 MVP: 부분 실패 체크
#                 if step_failed > 0:
#                     failure_rate = step_failed / step_created
#                     print(f"    -> 단계 {step.step_order} 실패율: {failure_rate:.1%} ({step_failed}/{step_created})")
                    
#                     if failure_rate > 0.5:  # 50% 이상 실패
#                         print(f"    -> 단계 {step.step_order} 과도한 실패 감지, 사용자 액션 필요")
#                         await status_manager.update_simulation_status(
#                             simulation_id,
#                             pod_creation_status="PARTIAL_SUCCESS",
#                             user_action_required=True,
#                             partial_failure_step_order=step.step_order
#                         )
#                         # 여기서 백그라운드 작업을 일시 중단하고 사용자 선택 대기
#                         print("    -> 백그라운드 작업 일시 중단 (사용자 액션 대기)")
#                         return  # 함수 종료

#                 current_time = step_end_time + timedelta(seconds=step.delay_after_completion)
#                 print(f"단계 {step.step_order} 완료. 성공: {step_successful}, 실패: {step_failed}")

#             # 🆕 MVP: 전체 완료 상태 업데이트
#             print("\n모든 단계 처리 완료. 최종 상태 업데이트.")
#             await status_manager.update_simulation_status(
#                 simulation_id,
#                 pod_creation_status="COMPLETED"
#             )
            
#             # 기존 로직 유지
#             simulation.status = "PAUSED"
#             simulation.total_agents = overall_successful  # 성공한 Pod 수만 카운트
#             await session.commit()
#             print("DB 커밋 완료. 시뮬레이션 상태 'PAUSED'로 변경.")
            
#     except Exception as e:
#         print(f"ERROR: Sequential 패턴 백그라운드 작업 실패: {e}")
        
#         # 🆕 MVP: 실패 상태 업데이트
#         await status_manager.update_simulation_status(
#             simulation_id,
#             pod_creation_status="FAILED"
#         )
        
#         # 🆕 MVP: 생성된 Pod들 정리 (리소스 누수 방지)
#         await cleanup_failed_simulation_pods(simulation_id)
        
#         # 기존 로직 유지
#         async with sessionmaker() as session:
#             try:
#                 simulation = await session.get(Simulation, simulation_id)
#                 if simulation:
#                     simulation.status = "ERROR"
#                     await session.commit()
#                     print(f"Simulation ID {simulation_id} 상태를 'ERROR'로 업데이트.")
#             except:
#                 print("ERROR: 롤백 후 상태 업데이트 실패.")
#         raise
        
#     print(f"--- [Sequential 패턴 MVP] 백그라운드 작업 종료 (Simulation ID: {simulation_id}) ---")

    
    
    
    # async with sessionmaker() as session:
    #     try:
    #         print(f"Session 열림. Simulation {simulation_id} 조회 중...")
    #         templates_service = TemplateService(session)
    #         pod_service = PodService()
            
    #         simulation = await session.get(Simulation, simulation_id)
    #         if not simulation:
    #             print(f"ERROR: Simulation {simulation_id}를 찾을 수 없습니다.")
    #             raise ValueError(f"Simulation {simulation_id} not found")
            
    #         print(f"시뮬레이션 정보: 이름='{simulation.name}', 시작 시간='{simulation.scheduled_start_time}'")
            
    #         current_time = simulation.scheduled_start_time
    #         total_agents = 0

    #         print(f"총 {len(steps_data)}개의 단계 처리 시작.")
    #         for step in sorted(steps_data, key=lambda s: s.step_order):
    #             print(f"\n- [단계 {step.step_order}] 처리 시작 -")
    #             print(f"템플릿 ID: {step.template_id}, 에이전트 수: {step.autonomous_agent_count}, 실행 시간: {step.execution_time}초")
                
    #             template = await templates_service.find_template_by_id(step.template_id, api)
    #             print(f"템플릿 조회 완료: ID={template.template_id}, 타입='{template.type}'")

    #             step_start_time = current_time
    #             step_end_time = step_start_time + timedelta(seconds=step.execution_time)
    #             print(f"단계 시간 설정: 시작='{step_start_time}', 종료='{step_end_time}'")

    #             simulation_step = SimulationStep(
    #                 simulation_id=simulation.id,
    #                 step_order=step.step_order,
    #                 template_id=template.template_id,
    #                 autonomous_agent_count=step.autonomous_agent_count,
    #                 execution_time=step.execution_time,
    #                 delay_after_completion=step.delay_after_completion,
    #                 actual_start_time=step_start_time,
    #                 actual_end_time=step_end_time
    #             )
    #             session.add(simulation_step)
    #             await session.flush()
    #             print(f"시뮬레이션 단계 (StepOrder={step.step_order}) DB에 추가 완료.")

    #             for i in range(step.autonomous_agent_count):
    #                 instance = Instance(
    #                     name=f"{simulation.name}_step{step.step_order}_agent_{i}",
    #                     description=f"Step {step.step_order} - Agent {i}",
    #                     pod_namespace=simulation.namespace,
    #                     simulation_id=simulation.id,
    #                     simulation=simulation,
    #                     template_id=template.template_id,
    #                     template=template,
    #                 )
    #                 session.add(instance)
    #                 await session.flush()
    #                 print(f"  -> 인스턴스 {instance.name} DB에 추가 완료.")

    #                 # Pod 생성 로직 디버깅용
    #                 print(f"  -> Pod 생성 요청 중... (인스턴스 ID: {instance.id})")
    #                 instance.pod_name = await pod_service.create_pod(instance, template)
    #                 print(f"  -> Pod 생성 완료: {instance.pod_name}")
    #                 session.add(instance)

    #             current_time = step_end_time + timedelta(seconds=step.delay_after_completion)
    #             total_agents += step.autonomous_agent_count
    #             print(f"다음 단계 시작 시간 계산: {current_time}, 현재까지 총 에이전트: {total_agents}")

    #         print("\n모든 단계 처리 완료. 최종 DB 업데이트 시작.")
    #         simulation.status = "PAUSED"
    #         simulation.total_agents = total_agents
            
    #         await session.commit()
    #         print("DB 커밋 완료. 시뮬레이션 상태 'PAUSED'로 변경.")
            
    #     except Exception as e:
    #         await session.rollback()
    #         print(f"ERROR: Sequential 패턴 백그라운드 작업 실패: {e}")
    #         try:
    #             simulation = await session.get(Simulation, simulation_id)
    #             if simulation:
    #                 simulation.status = "ERROR"
    #                 await session.commit()
    #                 print(f"Simulation ID {simulation_id} 상태를 'ERROR'로 업데이트.")
    #         except:
    #             print("ERROR: 롤백 후 상태 업데이트 실패.")
    #         raise
    # print(f"--- [Sequential 패턴] 백그라운드 작업 종료 (Simulation ID: {simulation_id}) ---")


async def handle_parallel_pattern_background(
    sessionmaker,
    simulation_id: int,
    agents_data: list[ParallelAgent],
    api: str
):
    print(f"\n--- [Parallel 패턴] 백그라운드 작업 시작 (Simulation ID: {simulation_id}) ---")
    async with sessionmaker() as session:
        try:
            print(f"Session 열림. Simulation {simulation_id} 조회 중...")
            templates_service = TemplateService(session)
            pod_service = PodService()
            
            simulation = await session.get(Simulation, simulation_id)
            if not simulation:
                print(f"ERROR: Simulation {simulation_id}를 찾을 수 없습니다.")
                raise ValueError(f"Simulation {simulation_id} not found")
            
            print(f"시뮬레이션 정보: 이름='{simulation.name}', 총 에이전트 그룹: {len(agents_data)}개")
            
            total_agents = 0
            
            print("에이전트 그룹 처리 시작.")
            for agent in agents_data:
                print(f"\n- [에이전트 그룹] 처리 시작 -")
                print(f"템플릿 ID: {agent.template_id}, 에이전트 수: {agent.autonomous_agent_count}, 실행 시간: {agent.execution_time}초")
                
                template = await templates_service.find_template_by_id(agent.template_id, api)
                print(f"템플릿 조회 완료: ID={template.template_id}, 타입='{template.type}'")
                
                simulation_group = SimulationGroup(
                    simulation_id=simulation.id,
                    group_name=f"{simulation.name}_parallel_group_{agent.template_id}",  # 그룹 이름 생성
                    template_id=agent.template_id,
                    autonomous_agent_count=agent.autonomous_agent_count,
                    execution_time=agent.execution_time,
                    assigned_area=simulation.namespace,  # 또는 적절한 영역 할당
                    status=GroupStatus.PENDING,  # 기본 상태
                )
                
                session.add(simulation_group)
                await session.flush()
                print(f"  -> SimulationGroup 생성 완료: ID={simulation_group.id}, 이름='{simulation_group.group_name}', 템플릿={agent.template_id}, 에이전트수={agent.autonomous_agent_count}")

                for i in range(agent.autonomous_agent_count):
                    instance = Instance(
                        name=f"{simulation.name}_parallel_{agent.template_id}_agent_{i}",
                        description=f"Parallel agent {i} for template {agent.template_id}",
                        pod_namespace=simulation.namespace,
                        simulation_id=simulation.id,
                        simulation=simulation,
                        template_id=template.template_id,
                        template=template
                    )
                    session.add(instance)
                    await session.flush()
                    print(f"  -> 인스턴스 {instance.name} DB에 추가 완료.")

                    # Pod 생성 로직 디버깅용
                    print(f"  -> Pod 생성 요청 중... (인스턴스 ID: {instance.id})")
                    instance.pod_name = await pod_service.create_pod(instance, template)
                    print(f"  -> Pod 생성 완료: {instance.pod_name}")
                    session.add(instance)
                
                total_agents += agent.autonomous_agent_count
                print(f"에이전트 그룹 처리 완료. 현재까지 총 에이전트: {total_agents}")

            print("\n모든 에이전트 그룹 처리 완료. 최종 DB 업데이트 시작.")
            simulation.status = "PAUSED"
            simulation.total_agents = total_agents
            
            await session.commit()
            print("DB 커밋 완료. 시뮬레이션 상태 'PAUSED'로 변경.")
            
        except Exception as e:
            await session.rollback()
            print(f"ERROR: Parallel 패턴 백그라운드 작업 실패: {e}")
            try:
                simulation = await session.get(Simulation, simulation_id)
                if simulation:
                    simulation.status = "ERROR"
                    await session.commit()
                    print(f"Simulation ID {simulation_id} 상태를 'ERROR'로 업데이트.")
            except:
                print("ERROR: 롤백 후 상태 업데이트 실패.")
            raise
    print(f"--- [Parallel 패턴] 백그라운드 작업 종료 (Simulation ID: {simulation_id}) ---")
    
async def cleanup_failed_simulation_pods(simulation_id: int):
    """실패한 시뮬레이션의 Pod들 정리 (MVP 목표 1: 리소스 누수 방지)"""
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