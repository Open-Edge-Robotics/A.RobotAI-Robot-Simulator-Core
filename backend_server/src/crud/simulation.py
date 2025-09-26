import asyncio
import contextlib
from datetime import datetime, timezone
import json
import traceback
from typing import Any, Dict, Tuple, List, Optional
from fastapi import HTTPException, status
from sqlalchemy import select, exists, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload
from starlette.status import HTTP_409_CONFLICT

from exception.simulation_exceptions import SimulationNotFoundError
from models.simulation_execution import SimulationExecution
from schemas.context import SimulationContext, StepContext
from repositories.instance_repository import InstanceRepository
from repositories.template_repository import TemplateRepository
from database.redis_simulation_client import RedisSimulationClient
from schemas.simulation_status import CurrentStatus, CurrentTimestamps, GroupDetail, ParallelProgress, SequentialProgress, StepDetail
from crud.metrics_collector import MetricsCollector
from schemas.dashboard import DashboardData
from utils.simulation_utils import extract_simulation_dashboard_data
from state import SimulationState
from utils.debug_print import debug_print
from utils.rosbag_executor import RosbagExecutor
from schemas.simulation_detail import CurrentStatusInitiating, CurrentStatusPENDING, ExecutionPlanParallel, ExecutionPlanSequential, GroupModel, ProgressModel, SimulationData, StepModel, TimestampModel
from schemas.pod import GroupIdFilter, StepOrderFilter
from repositories.simulation_repository import SimulationRepository
from schemas.pagination import PaginationMeta, PaginationParams
from models.enums import ExecutionStatus, GroupStatus, PatternType, SimulationExecutionStatus, SimulationStatus, StepStatus
from utils.simulation_background_2 import (
    handle_parallel_pattern_background,
    handle_sequential_pattern_background,
)
from .template import TemplateService

from .pod import PodService
from .rosbag import RosService
from models.instance import Instance
from models.simulation import Simulation
from models.simulation_steps import SimulationStep
from models.simulation_groups import SimulationGroup
from schemas.simulation import (
    GroupSummary,
    RedisGroupStatus,
    RedisStepStatus,
    SimulationCreateRequest,
    SimulationListItem,
    SimulationListResponse,
    SimulationCreateResponse,
    SimulationDeleteResponse,
    SimulationControlResponse,
    SimulationParams,
    SimulationPatternUpdateRequest,
    SimulationPatternUpdateResponse,
    SimulationOverview,
    SimulationSummaryItem,
    StepSummary
)
from utils.my_enum import PodStatus, API
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import async_sessionmaker
import logging

logger = logging.getLogger(__name__)

# ----------------------------
# 데코레이터: 안전한 핸들러
# ----------------------------
def safe_handler(func):
    async def wrapper(self, *args, **kwargs):
        try:
            return await func(self, *args, **kwargs)
        except Exception as e:
            self.logger.error(f"Error in {func.__name__}: {e}", exc_info=True)
    return wrapper

class SimulationService:
    def __init__(self, session: AsyncSession, sessionmaker: async_sessionmaker, repository: SimulationRepository, template_service: TemplateService , template_repository: TemplateRepository, instance_repository: InstanceRepository, state: SimulationState):
        self.session = session
        self.sessionmaker = sessionmaker
        self.repository = repository
        self.template_repository = template_repository
        self.instance_repository = instance_repository
        self.state = state
        self.pod_service = PodService()
        self.template_service = template_service
        self.rosbag_executor = RosbagExecutor(self.pod_service)
        self.collector = MetricsCollector()   
        self.redis_client = None
        
    async def connect_redis(self):
        if self.redis_client is None:
            self.redis_client = RedisSimulationClient()
            await self.redis_client.connect()

    async def close_redis(self):
        if self.redis_client:
            await self.redis_client.client.close()
            self.redis_client = None
    
    # ============================
    # 실행 단위 상태 핸들러 (DB + Redis 동시 업데이트)
    # ============================
    async def _handle_entity_status(
        self,
        entity_type: str,
        entity_identifier: int,  # step_order / group_id / simulation_id
        simulation_id: int,
        execution_id: int,
        status: str,
        reason: str = None,
        redis_client: Optional[RedisSimulationClient] =None,
        current_repeat: Optional[int] = None,
        total_repeats: Optional[int] = None,
        autonomous_agent_count: Optional[int] = None,
        progress: Optional[float] = None,
        update_db: bool = True
    ):
        """
        Step / Group / Simulation 상태 기록
        - StepExecution / GroupExecution / SimulationExecution 테이블 기록
        - Redis 상태 동시에 갱신
        - current_repeat, total_repeats, autonomous_agent_count, progress 반영
        """
        now = datetime.now(timezone.utc)


        # 1️⃣ DB 업데이트
        if update_db:
            try:
                async with self.sessionmaker() as db_session:
                    if entity_type == "step":
                        await self.repository.create_or_update_step_execution(
                            execution_id=execution_id,
                            step_order=entity_identifier,
                            status=status,
                            error=reason if status == "FAILED" else None,
                            stopped_at=now if status == "STOPPED" else None,
                            failed_at=now if status == "FAILED" else None,
                            completed_at=now if status == "COMPLETED" else None,
                            current_repeat=current_repeat,
                            total_repeats=total_repeats,
                            autonomous_agent_count=autonomous_agent_count,
                            session=db_session
                        )

                    elif entity_type == "group":
                        await self.repository.create_or_update_group_execution(
                            execution_id=execution_id,
                            group_id=entity_identifier,
                            status=status,
                            reason=reason if status == "FAILED" or status == "STOPPED" else None,
                            stopped_at=now if status == "STOPPED" else None,
                            failed_at=now if status == "FAILED" else None,
                            completed_at=now if status == "COMPLETED" else None,
                            current_repeat=current_repeat,
                            total_repeats=total_repeats,
                            autonomous_agent_count=autonomous_agent_count,
                            session=db_session
                        )

                    elif entity_type == "simulation":
                        # execution_id가 없는 경우 Simulation 테이블만 업데이트
                        if execution_id is None:
                            await self.repository.update_simulation_status(
                                simulation_id=simulation_id,
                                status=status,
                                session=db_session
                            )
                        else:
                            # SimulationExecution이 존재하는 경우
                            await self.repository.update_execution_status(
                                execution_id=execution_id,
                                status=status,
                                reason=reason if status == "FAILED" else None,
                                stopped_at=now if status == "STOPPED" else None,
                                failed_at=now if status == "FAILED" else None,
                                completed_at=now if status == "COMPLETED" else None,
                                session=db_session
                            )
                            # Simulation 테이블도 상태 반영
                            await self.repository.update_simulation_status(
                                simulation_id=simulation_id,
                                status=status,
                                session=db_session
                            )
                    else:
                        raise ValueError(f"Unknown entity_type: {entity_type}")
                    
                    # ✅ 모든 DB 작업 성공 시 커밋
                    await db_session.commit()
                
            except SQLAlchemyError as e:
                # ❌ 문제 발생 시 롤백
                await db_session.rollback()
                # 필요시 로깅
                print(f"DB 트랜잭션 실패, 롤백 수행: {e}")
                raise
            
        # ----------------------------
        # 2️⃣ Redis 업데이트
        # ----------------------------
        if redis_client is None:
            return
        debug_print("Redis 업데이트 중이야")
        redis_key = f"simulation:{simulation_id}:execution:{execution_id}"
        raw_status = await redis_client.client.get(redis_key)
        if not raw_status:
            return
        debug_print(f"raw_status: {raw_status}")

        # Redis에서 bytes → str → dict
        if isinstance(raw_status, bytes):
            raw_status = raw_status.decode("utf-8")
        try:
            current_status = json.loads(raw_status)
        except json.JSONDecodeError:
            current_status = {}
        debug_print(f"current_status: {current_status}")

        # Step / Group 세부 상태 업데이트
        items = []
        key_name = None
        list_key = None

        if entity_type == "step" and current_status.get("stepDetails"):
            key_name = "stepOrder"
            list_key = "stepDetails"
            items = current_status.get(list_key, [])
        elif entity_type == "group" and current_status.get("groupDetails"):
            key_name = "groupId"
            list_key = "groupDetails"
            items = current_status.get(list_key, [])
        else:
            items = []
            key_name = None
            list_key = None
            
        debug_print(f"key_name: {key_name}, list_key: {list_key}, item: {current_status.get(list_key, [])}")
        
        for item in items:
            if item[key_name] == entity_identifier:
                item.update({
                    "status": status,
                    "error": reason if status == "FAILED" or status == "STOPPED" else None,
                    "stoppedAt": now.isoformat() if status == "STOPPED" else None,
                    "failedAt": now.isoformat() if status == "FAILED" else None,
                    "completedAt": now.isoformat() if status == "COMPLETED" else None,
                })
                if current_repeat is not None:
                    item["currentRepeat"] = current_repeat
                if total_repeats is not None:
                    item["totalRepeats"] = total_repeats
                if autonomous_agent_count is not None:
                    item["autonomousAgents"] = autonomous_agent_count
                if progress is not None:
                    item["progress"] = progress
                break
                
        # 전체 진행률 계산
        total_items = len(items)
        completed_items = sum(1 for s in items if s["status"] == "COMPLETED")
        running_item_number = sum((1 for s in items if s["status"] == "RUNNING"), 0)
        
        # progress 값이 들어온 경우에만 overall_progress 업데이트
        if progress is not None and total_items > 0:
            effective_progress = progress if progress < 1.0 else 0  # 완료 Step이면 progress 무시
            overall_progress = (completed_items + effective_progress) / total_items
            current_status["progress"] = {
                "overallProgress": round(overall_progress, 1),
                "currentStep" if entity_type == "step" else "runningGroups": running_item_number,
                "completedSteps" if entity_type == "step" else "completedGroups": completed_items,
                "totalSteps" if entity_type == "step" else "totalGroups": total_items
            }
            
        # ----------------------------
        # status_message 자동 생성 (병렬/순차)
        # ----------------------------
        status_message = None
        
        # ✅ 패턴 판별
        is_sequential = "stepDetails" in current_status
        is_parallel = "groupDetails" in current_status
        
        if status in ("FAILED", "STOPPED") and reason:
            status_message = reason
        elif status == "RUNNING":
            if is_parallel:
                total_groups = current_status.get("progress", {}).get("totalGroups", 0)
                completed_groups = current_status.get("progress", {}).get("completedGroups", 0)
                running_groups = current_status.get("progress", {}).get("runningGroups", 0)
                status_message = (
                    f"병렬 시뮬레이션 실행 중 - 총 {total_groups}개 그룹 중 "
                    f"{completed_groups}개 완료, {running_groups}개 진행 중"
                )
            elif is_sequential:
                total_steps = current_status.get("progress", {}).get("totalSteps", 0)
                completed_steps = current_status.get("progress", {}).get("completedSteps", 0)
                current_step = completed_steps + 1 if completed_steps < total_steps else total_steps
                status_message = f"순차 시뮬레이션 실행 중 - 현재 Step {current_step}/{total_steps}"
        elif status == "COMPLETED":
            if is_parallel:
                total_groups = current_status.get("progress", {}).get("totalGroups", 0)
                status_message = f"병렬 시뮬레이션 실행 완료 - 모든 {total_groups}개 그룹 성공"
            elif is_sequential:
                total_steps = current_status.get("progress", {}).get("totalSteps", 0)
                status_message = f"순차 시뮬레이션 실행 완료 - 모든 {total_steps}개 Step 성공"
        
            
        # ----------------------------
        # 공통 상태 및 타임스탬프
        # ----------------------------
        if "timestamps" not in current_status:
            current_status["timestamps"] = {}
        current_status["timestamps"]["lastUpdated"] = now.isoformat()
        current_status["status"] = status
        current_status["message"] = status_message

        await redis_client.client.set(redis_key, json.dumps(current_status))
        
    async def _run_parallel_simulation_with_progress_v2(
        self,
        simulation_id: int,
        stop_event: asyncio.Event
    ):
        debug_print("🚀 병렬 시뮬레이션 실행 시작", simulation_id=simulation_id)
        redis_client = RedisSimulationClient()
        await redis_client.connect()

        simulation_data: SimulationParams = {}
        group_progress_tracker = {}
        
        total_execution_summary = {
            "simulation_status": "RUNNING",
            "completed_groups": 0,
            "total_groups": 0,
            "group_results": [],
            "total_pods_executed": 0,
            "total_success_pods": 0,
            "total_failed_pods": 0
        }

        try:
            # ----------------------------
            # 1️⃣ DB 조회 + SimulationExecution 생성
            # ----------------------------
            async with self.sessionmaker() as db_session:
                simulation = await self.repository.find_by_id(simulation_id, db_session)
                if not simulation:
                    raise SimulationNotFoundError(simulation_id)

                # Simulation RUNNING 상태 업데이트
                await self.repository.update_simulation_status(simulation_id, SimulationStatus.RUNNING)
                
                simulation_data["id"] = simulation.id
                simulation_data["namespace"] = simulation.namespace
                simulation_data["created_at"] = simulation.created_at

                # RUNNING 상태인 SimulationExecution 생성
                execution = SimulationExecution(simulation_id=simulation.id)
                execution.start_execution()
                db_session.add(execution)
                await db_session.flush()  # execution.id 확보

                # 그룹 조회 및 GroupExecution 초기화
                groups = await self.repository.find_simulation_groups(simulation_id, db_session)
                total_execution_summary["total_groups"] = len(groups)
                
                redis_group_list: list[RedisGroupStatus] = []
                db_group_list: list[GroupSummary] = []

                for group in groups:
                    debug_print(f"{group.group_name} 의 최대 재생횟수: {group.repeat_count}")
                    await self.repository.create_or_update_group_execution(
                        execution_id=execution.id,
                        group_id=group.id,
                        status="PENDING",
                        autonomous_agent_count=group.autonomous_agent_count,
                        current_repeat=0,
                        total_repeats=group.repeat_count or 1,
                        session=db_session
                    )

                    db_group_list.append(
                        GroupSummary(
                            id=group.id,
                            total_repeats=group.repeat_count or 1,
                            autonomous_agent_count=group.autonomous_agent_count
                        )
                    )

                await db_session.commit()
                await db_session.refresh(execution)
                execution_id = execution.id

            debug_print("✅ SimulationExecution 및 GroupExecution 초기화 완료",
                        execution_id=execution_id, group_count=len(db_group_list))

            # 그룹별 progress 초기화
            now = datetime.now(timezone.utc)
            group_progress_tracker = {
                group.id: {"last_recorded_repeat": 0, "current_progress": 0.0, "start_time": now}
                for group in db_group_list
            }

            # ----------------------------
            # 2️⃣ Redis 초기 상태 세팅
            # ----------------------------
            primary_redis_key = f"simulation:{simulation_id}"  
            execution_redis_key = f"simulation:{simulation_id}:execution:{execution_id}"  

            redis_group_list: list[RedisGroupStatus] = [
                RedisGroupStatus(
                    group_id=g.id,
                    status="PENDING",
                    progress=0.0,
                    started_at=None,
                    current_repeat=0,
                    total_repeats=g.total_repeats or 1,
                    autonomous_agents=g.autonomous_agent_count,
                    completed_at=None,
                    failed_at=None,
                    stopped_at=None,
                    error=None
                ) for g in db_group_list
            ]
            
            initial_status = {
                "executionId": execution_id,
                "simulationId": simulation_id,
                "status": "RUNNING",
                "progress": {
                    "overallProgress": 0.0,
                    "completedGroups": 0,
                    "runningGroups": len(redis_group_list),
                    "totalGroups": len(redis_group_list)
                },
                "timestamps": {
                    "createdAt": simulation_data["created_at"].isoformat() if simulation_data["created_at"] else None,
                    "startedAt": now.isoformat(),
                    "lastUpdated": now.isoformat()
                },
                "message": f"병렬 시뮬레이션 시작 - 총 {len(redis_group_list)}개 그룹",
                "groupDetails": [g.model_dump() for g in redis_group_list]
            }
            
            await redis_client.client.set(primary_redis_key, json.dumps(initial_status))
            await redis_client.client.set(execution_redis_key, json.dumps(initial_status))

            # ----------------------------
            # 3️⃣ 그룹별 병렬 실행
            # ----------------------------
            pending_tasks = {
                asyncio.create_task(
                    self._execute_single_group_with_memory_tracking_v2(
                        simulation_data, execution_id, group, redis_client, group_progress_tracker, stop_event
                    )
                ): group.id for group in db_group_list
            }

            while pending_tasks:
                done, _ = await asyncio.wait(pending_tasks.keys(), timeout=1, return_when=asyncio.FIRST_COMPLETED)

                for task in done:
                    group_id = pending_tasks.pop(task)
                    try:
                        result = task.result()
                        total_execution_summary["group_results"].append(result)
                        total_execution_summary["completed_groups"] += 1
                        total_execution_summary["total_pods_executed"] += result.get("total_pod_count", 0)
                        total_execution_summary["total_success_pods"] += result.get("success_pod_count", 0)
                        total_execution_summary["total_failed_pods"] += result.get("failed_pod_count", 0)
                        debug_print(f"📊 그룹 {group_id} 완료 요약", result=result)
                    except Exception as e:
                        debug_print(f"💥 그룹 {group_id} 실행 중 오류: {e}")
                        total_execution_summary["group_results"].append({
                            "group_id": group_id,
                            "status": "failed",
                            "failure_reason": str(e)
                        })

                # stop_event 체크
                if stop_event.is_set():
                    debug_print(f"🛑 시뮬레이션 {simulation_id} 중지 감지 - stop_event 활성")
                    # 남은 그룹 Task 취소
                    for t in pending_tasks.keys():
                        t.cancel()
                    await asyncio.gather(*pending_tasks.keys(), return_exceptions=True)
                    
                    # 남은 그룹 상태 STOPPED 처리
                    for remaining_task, gid in pending_tasks.items():
                        progress = group_progress_tracker[gid]["current_progress"]
                        current_repeat = group_progress_tracker[gid]["last_recorded_repeat"]
                        
                        await self._handle_entity_status(
                            entity_type="group",
                            simulation_id=simulation_id,
                            execution_id=execution_id,
                            entity_identifier=gid,
                            status="STOPPED",
                            reason="사용자 요청에 의한 중지",
                            redis_client=redis_client,
                            progress=progress,
                            current_repeat=current_repeat,
                            update_db=True
                        )
                        total_execution_summary["group_results"].append({
                            "group_id": gid,
                            "status": "stopped",
                            "failure_reason": "사용자 요청에 의한 중지"
                        })
                    
                    # 시뮬레이션 전체 STOPPED 처리
                    await self._handle_entity_status(
                        entity_type="simulation",
                        simulation_id=simulation_id,
                        execution_id=execution_id,
                        entity_identifier=simulation_id,
                        status="STOPPED",
                        redis_client=redis_client,
                        update_db=True
                    )    
                    
                    break
                
                await asyncio.sleep(1)

            # ----------------------------
            # 4️⃣ 시뮬레이션 종료 처리
            # ----------------------------
            if not stop_event.is_set():
                await self._handle_entity_status(
                    entity_type="simulation",
                    simulation_id=simulation_id,
                    execution_id=execution_id,
                    entity_identifier=simulation_id,
                    status="COMPLETED",
                    redis_client=redis_client,
                    update_db=True
                )
                total_execution_summary["simulation_status"] = "COMPLETED"
                debug_print(f"🎉 시뮬레이션 {simulation_id} 완료")

            return total_execution_summary
        
        except Exception as e:
            traceback.print_exc()
            debug_print(f"❌ 시뮬레이션 {simulation_id} 실행 중 예외: {e}")
            try:
                await self._handle_entity_status(
                    entity_type="simulation",
                    simulation_id=simulation_id,
                    execution_id=execution_id,
                    entity_identifier=simulation_id,
                    status="FAILED",
                    reason=str(e),
                    redis_client=redis_client,
                    update_db=True
                )
            except Exception as cleanup_error:
                traceback.print_exc()
                debug_print(f"💥 시뮬레이션 정리 작업 중 추가 오류: {cleanup_error}")
            raise

        finally:
            await self._cleanup_resources_safe(
                redis_client=redis_client,
                pod_tasks={t: gid for t, gid in pending_tasks.items()},
                simulation_id=simulation_id
            )
        
    async def _execute_single_group_with_memory_tracking_v2(
        self,
        simulation: SimulationParams,
        execution_id: int,
        group,
        redis_client,
        group_progress_tracker,
        stop_event: asyncio.Event
    ):
        simulation_id = simulation["id"]
        namespace = simulation["namespace"]
        
        debug_print("🔸 그룹 실행 시작", group_id=group.id, simulation_id=simulation_id)
        debug_print(f"Redis 키 정보: simulation:{simulation_id}:execution:{execution_id}")
        start_time = datetime.now(timezone.utc)

        try:
            # 1️⃣ 그룹 Pod 조회
            pod_list = PodService.get_pods_by_filter(
                namespace=namespace,
                filter_params=GroupIdFilter(group_id=group.id)
            )
            if not pod_list:
                return {
                    "group_id": group.id,
                    "status": "failed",
                    "execution_time": (datetime.now(timezone.utc) - start_time).total_seconds(),
                    "total_pod_count": 0,
                    "success_pod_count": 0,
                    "failed_pod_count": 0,
                    "failure_reason": f"그룹 {group.id}에서 Pod를 찾을 수 없음"
                }

            total_pod_count = len(pod_list)
            debug_print(f"📋 그룹 {group.id} Pod 목록", pod_names=[pod.metadata.name for pod in pod_list], total_count=total_pod_count)

            # 2️⃣ Pod Task 실행 시작
            pod_tasks = {
                asyncio.create_task(self.rosbag_executor.execute_single_pod(pod, group_id=group.id)): pod.metadata.name
                for pod in pod_list
            }

            completed_pods = set()
            failed_pods = {}
            poll_interval = 1
            last_recorded_repeat = group_progress_tracker[group.id]["last_recorded_repeat"]

            # 3️⃣ Pod 진행상황 루프
            while len(completed_pods) < total_pod_count:
                # 🔹 stop_event 체크
                if stop_event.is_set():
                    debug_print(f"🛑 그룹 {group.id} 중지 감지 - stop_event 활성")
                    for t in pod_tasks.keys():
                        t.cancel()
                    await asyncio.gather(*pod_tasks.keys(), return_exceptions=True)

                    # STOPPED 상태 업데이트
                    await self._handle_entity_status(
                        entity_type="group",
                        simulation_id=simulation_id,
                        execution_id=execution_id,
                        entity_identifier=group.id,
                        status="STOPPED",
                        reason="사용자 요청에 의한 중지",
                        redis_client=redis_client,
                        current_repeat=last_recorded_repeat,
                        update_db=True
                    )
                    return {
                        "group_id": group.id,
                        "status": "stopped",
                        "execution_time": (datetime.now(timezone.utc) - start_time).total_seconds(),
                        "total_pod_count": total_pod_count,
                        "success_pod_count": len(completed_pods),
                        "failed_pod_count": len(failed_pods),
                        "failure_reason": "사용자 요청에 의한 중지"
                    }

                # 🔹 완료된 Pod 확인
                done_tasks = [t for t in pod_tasks if t.done()]
                for task in done_tasks:
                    pod_name = pod_tasks.pop(task)
                    try:
                        _ = task.result()
                        completed_pods.add(pod_name)
                        debug_print(f"✅ Pod 완료: {pod_name} ({len(completed_pods)}/{total_pod_count})")
                    except Exception as e:
                        debug_print(f"💥 Pod 실패: {pod_name}: {e}")
                        # 실패 Pod 진행률 조회
                        try:
                            failed_pod = next(pod for pod in pod_list if pod.metadata.name == pod_name)
                            status_info = await self.rosbag_executor._check_pod_rosbag_status(failed_pod)
                            current_loop = status_info.get("current_loop", 0) if isinstance(status_info, dict) else 0
                            max_loops = max(status_info.get("max_loops") or 1, 1) if isinstance(status_info, dict) else 1
                            failed_pods[pod_name] = min(current_loop / max_loops, 1.0)
                            debug_print(f"💥 Pod {pod_name} 실패 시점 진행: {current_loop}/{max_loops} ({failed_pods[pod_name]:.1%})")
                        except Exception:
                            failed_pods[pod_name] = 0.0
                            debug_print(f"⚠️ Pod {pod_name} 실패 진행률 조회 실패 -> 0%")

                        # 그룹 전체 Task 취소
                        for t in pod_tasks.keys():
                            t.cancel()
                        await asyncio.gather(*pod_tasks.keys(), return_exceptions=True)

                        # 그룹 상태 FAILED 업데이트
                        await self._handle_entity_status(
                            entity_type="group",
                            simulation_id=simulation_id,
                            execution_id=execution_id,
                            entity_identifier=group.id,
                            status="FAILED",
                            reason=str(e),
                            redis_client=redis_client,
                            current_repeat=last_recorded_repeat,
                            update_db=True
                        )

                        return {
                            "group_id": group.id,
                            "status": "failed",
                            "execution_time": (datetime.now(timezone.utc) - start_time).total_seconds(),
                            "total_pod_count": total_pod_count,
                            "success_pod_count": len(completed_pods),
                            "failed_pod_count": len(failed_pods),
                            "failure_reason": str(e)
                        }

                # 🔹 진행률 계산 (가중치)
                total_progress = 0.0
                for pod in pod_list:
                    pod_name = pod.metadata.name
                    try:
                        status_info = await self.rosbag_executor._check_pod_rosbag_status(pod)
                        current_loop = status_info.get("current_loop", 0) if isinstance(status_info, dict) else 0
                        max_loops = max(status_info.get("max_loops") or 1, 1) if isinstance(status_info, dict) else 1
                        effective_loop = min(current_loop + 1, max_loops)
                        pod_progress = effective_loop / max_loops
                    except Exception:
                        pod_progress = 0.0
                        effective_loop = 0
                    total_progress += pod_progress
                    last_recorded_repeat = max(last_recorded_repeat, effective_loop)
                
                group_progress = total_progress / total_pod_count
                group_progress_tracker[group.id]["current_progress"] = group_progress
                group_progress_tracker[group.id]["last_recorded_repeat"] = last_recorded_repeat
                debug_print(f"📊 그룹 {group.id} 현재 진행률: {group_progress:.1%} (완료 {len(completed_pods)}/{total_pod_count})")

                # 🟢 Redis 상태 업데이트 (DB는 완료 시에만)
                await self._handle_entity_status(
                    entity_type="group",
                    simulation_id=simulation_id,
                    execution_id=execution_id,
                    entity_identifier=group.id,
                    status="RUNNING",
                    redis_client=redis_client,
                    current_repeat=last_recorded_repeat,
                    progress=group_progress,
                    update_db=False
                )

                await asyncio.sleep(poll_interval)

            # ✅ 그룹 완료 처리
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            success_count = len(completed_pods)
            failed_count = len(failed_pods)
            final_repeat_count = last_recorded_repeat
            status = "success" if failed_count == 0 else "failed"
            
            await self._handle_entity_status(
                entity_type="group",
                simulation_id=simulation_id,
                execution_id=execution_id,
                entity_identifier=group.id,
                status="COMPLETED" if status == "success" else "FAILED",
                redis_client=redis_client,
                progress=group_progress,
                current_repeat=last_recorded_repeat,
                update_db=True
            )

            debug_print(f"✅ 그룹 {group.id} 완료 처리: status={status}, 반복횟수={final_repeat_count}")
            return {
                "group_id": group.id,
                "status": status,
                "execution_time": execution_time,
                "total_pod_count": total_pod_count,
                "success_pod_count": success_count,
                "failed_pod_count": failed_count,
                "failure_reason": f"{failed_count}개 Pod 실패" if failed_count > 0 else None
            }
        except Exception as e:
            debug_print(f"💥 그룹 {group.id} 실행 중 예외: {e}")
            # 그룹 상태 FAILED 업데이트
            await self._handle_entity_status(
                entity_type="group",
                simulation_id=simulation_id,
                execution_id=execution_id,
                entity_identifier=group.id,
                status="FAILED",
                reason=str(e),
                redis_client=redis_client,
                current_repeat=group_progress_tracker[group.id]["last_recorded_repeat"],
                progress=group_progress,
                update_db=True
            )
            return {
                "group_id": group.id,
                "status": "failed",
                "execution_time": (datetime.now(timezone.utc) - start_time).total_seconds(),
                "total_pod_count": len(pod_list) if 'pod_list' in locals() else 0,
                "success_pod_count": len(completed_pods),
                "failed_pod_count": len(failed_pods),
                "failure_reason": str(e)
            }

        finally:
            # ✅ 남은 Task 취소 및 리소스 정리
            for t in pod_tasks.keys():
                t.cancel()
            await asyncio.gather(*pod_tasks.keys(), return_exceptions=True)
        
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
            
            for template_id in template_ids:
                try:
                    template = await self.template_service.find_template_by_id(template_id)
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
        status: Optional[SimulationStatus] = None,
        start_date: Optional[str] = None,  # YYYY-MM-DD
        end_date: Optional[str] = None     # YYYY-MM-DD
    ) -> Tuple[List[SimulationListItem], PaginationMeta]:
        """페이지네이션된 시뮬레이션 목록 조회 (선택적 필터링 지원 + 기간 지원)"""
        # 1. 전체 데이터 개수 조회 (페이지 범위 검증용)
        total_count = await self.repository.count_all(
            pattern_type=pattern_type,
            status=status,
            start_date=start_date,
            end_date=end_date
        )
        
        # 2. 페이지 범위 검증
        self._validate_pagination_range(pagination, total_count)
        
        # 3. 실제 데이터 조회 (필터 + 페이지 적용)
        simulations = await self.repository.find_all_with_pagination(
            pagination,
            pattern_type=pattern_type,
            status=status,
            start_date=start_date,
            end_date=end_date
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
        elif sim.status == SimulationStatus.PENDING:
            current_status = CurrentStatusPENDING(
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
                group_id=g.id,
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
            latest_exec  = await self.repository.find_latest_simulation_execution(simulation_id)

            if latest_exec and latest_exec.status == SimulationStatus.RUNNING:
                raise HTTPException(
                    status_code=409,
                    detail=f"이미 실행 중인 시뮬레이션 실행이 존재합니다 (Execution ID: {latest_exec.id})"
                )
                
            # 리소스(Pod) 생성
            await self._create_pods_for_simulation(simulation)
            
            # 모든 Pod Running 상태 될 때까지 대기
            await PodService.wait_for_pods_running(simulation.namespace)
            debug_print(f"✅ 네임스페이스 '{simulation.namespace}'의 모든 Pod가 Running 상태임 확인 완료")

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
                background_task = self._run_sequential_simulation_with_progress_v2(simulation_id, stop_event)
                debug_print("🔄 순차 패턴 선택", simulation_id=simulation_id)
            elif simulation_data["pattern_type"] == "parallel":
                pattern_name = "병렬"
                background_task = self._run_parallel_simulation_with_progress_v2(simulation_id, stop_event)
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
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))
        except Exception as e:
            failure_reason = f"시뮬레이션 시작 중 예상치 못한 오류: {str(e)}"
            print(f"❌ {failure_reason}")
            
            # 네임스페이스 리소스 정리
            try:
                await PodService.delete_all_pods_in_namespace(namespace=f"simulation-{simulation.id}")
                print(f"♻️ 네임스페이스 'simulation-{simulation.id}' 리소스 모두 삭제 완료")
            except Exception as cleanup_err:
                print(f"⚠️ 네임스페이스 정리 중 오류 발생: {cleanup_err}")
                
            raise HTTPException(
                status_code=500,
                detail="시뮬레이션 시작 중 내부 오류가 발생했습니다"
            )
            
    async def _create_pods_for_simulation(self, simulation: "Simulation"):
        """
        시뮬레이션 실행 전 필요한 Pod 리소스 생성
        하나라도 실패하면 예외 발생
        """
        failed_pods = []
        
        if simulation.pattern_type == PatternType.SEQUENTIAL:
            steps = await self.repository.find_simulation_steps(simulation.id)
            
            for step in steps:
                instances = await self.instance_repository.find_instances(simulation.id, step_order=step.step_order)
                for instance in instances:
                    pod_name = instance.name
                    try:
                        debug_print(f"🚀 [Pod Creation] 시작 - {pod_name}")
                        await PodService.create_pod_if_not_exists(
                            instance=instance,
                            simulation=simulation,
                            step=step,
                            template=step.template
                        )
                        debug_print(f"✅ [Pod Creation] 성공 - {pod_name}")
                    except Exception as e:
                        failed_pods.append((instance.id, str(e)))
                
        elif simulation.pattern_type == PatternType.PARALLEL:
            groups = await self.repository.find_simulation_groups(simulation.id)
            
            for group in groups:
                instances = await self.instance_repository.find_instances(simulation.id, group_id=group.id)
                for instance in instances:
                    pod_name = instance.name
                    try:
                        debug_print(f"🚀 [Pod Creation] 시작 - {pod_name}")
                        await PodService.create_pod_if_not_exists(
                            instance=instance,
                            simulation=simulation,
                            group=group,
                            template=group.template
                        )
                        debug_print(f"✅ [Pod Creation] 성공 - {pod_name}")
                    except Exception as e:
                        failed_pods.append((instance.id, str(e)))
                        
        if failed_pods:
            error_messages = "; ".join([f"instance_id={iid}, step/group={sg}, error={msg}" for iid, sg, msg in failed_pods])
            raise RuntimeError(f"Pod 생성 실패: {error_messages}")

    async def _cleanup_simulation(self, simulation_id: int):
        """시뮬레이션 완료/취소 후 정리"""
        if simulation_id in self.state.running_simulations:
            print(f"시뮬레이션 {simulation_id} 정리 완료")
            del self.state.running_simulations[simulation_id]
            
        # simulation-{simulation_id} 네임스페이스의 모든 Pod 삭제
        namespace = f"simulation-{simulation_id}"
        await PodService.delete_all_pods_in_namespace(namespace)

    async def _run_sequential_simulation_with_progress_v2(
        self, simulation_id: int, stop_event: asyncio.Event
    ):
        """
        순차 패턴 시뮬레이션 실행 (반복 실행 지원) - StepExecution/EntityStatus 패턴 적용
        """
        redis_client = RedisSimulationClient()
        await redis_client.connect()
        
        current_step = None
        current_step_progress = 0.0
        current_step_repeat = 0
        execution_id = None
        pod_tasks = {}  # 전역 스코프
        
        try:
            debug_print(f"🎬 시뮬레이션 실행 시작: {simulation_id}")
            
            # ----------------------------
            # 1️⃣ DB 조회 + SimulationExecution 생성
            # ----------------------------
            async with self.sessionmaker() as db_session:
                simulation = await self.repository.find_by_id(simulation_id, db_session)
                if not simulation:
                    raise SimulationNotFoundError(simulation_id)
                
                # Simulation RUNNING 상태 업데이트
                await self.repository.update_simulation_status(simulation_id, SimulationStatus.RUNNING, db_session)
                
                namespace = simulation.namespace
                created_at = simulation.created_at
                steps = await self.repository.find_simulation_steps(simulation_id, db_session)
                
                # StepSummary 먼저 생성 (ORM 접근 즉시 복사)
                step_summaries = []
                for step in steps:
                    step_summaries.append(StepSummary(
                        id=step.id,
                        step_order=step.step_order,
                        autonomous_agent_count=step.autonomous_agent_count,
                        total_repeats=step.repeat_count or 1,
                        # 나머지 기본값
                        status=SimulationExecutionStatus.PENDING,
                        progress=0.0,
                        started_at=None,
                        completed_at=None,
                        failed_at=None,
                        stopped_at=None,
                        current_repeat=0,
                        error=None,
                        delay_after_completion=step.delay_after_completion or 0,
                        execution_time=step.execution_time or 0
                    ))
                
                # status 가 RUNNING 인 SimulationExecution 생성
                execution = SimulationExecution(simulation_id=simulation.id)
                execution.start_execution()
                db_session.add(execution)

                # 💡 flush 호출하여 execution.id 접근 가능
                await db_session.flush()

                # StepExecution 생성
                for step in step_summaries:
                    await self.repository.create_or_update_step_execution(
                        execution_id=execution.id,
                        step_order=step.step_order,
                        status=SimulationExecutionStatus.PENDING.value,
                        total_repeats=step.total_repeats or 1,
                        autonomous_agent_count=step.autonomous_agent_count,
                        session=db_session 
                    )
                
                # 모든 StepExecution 추가 후 commit
                await db_session.commit()
                await db_session.refresh(execution)
                
                execution_id = execution.id

            
            # ----------------------------
            # 2️⃣ Redis 초기 상태 설정
            # ----------------------------
            primary_redis_key = f"simulation:{simulation_id}"  
            execution_redis_key = f"simulation:{simulation_id}:execution:{execution_id}"  

            redis_step_statuses = [
                RedisStepStatus(
                    step_order=s.step_order,
                    status=s.status,
                    progress=0.0,
                    autonomous_agents=s.autonomous_agent_count,
                    started_at=s.started_at,
                    completed_at=s.completed_at,
                    failed_at=s.failed_at,
                    current_repeat=s.current_repeat,
                    total_repeats=s.total_repeats,
                    error=s.error
                ) for s in step_summaries
            ]

            initial_status = {
                "executionId": execution_id,
                "simulationId": simulation_id,
                "status": "RUNNING",
                "progress": {
                    "overallProgress": 0.0,
                    "currentStep": 0,
                    "completedSteps": 0,
                    "totalSteps": len(step_summaries),
                },
                "timestamps": {
                    "createdAt": created_at.isoformat() if created_at else None,
                    "lastUpdated": datetime.now(timezone.utc).isoformat(),
                    "startedAt": datetime.now(timezone.utc).isoformat(),
                    "completedAt": None,
                    "failedAt": None,
                    "stoppedAt": None,
                },
                "message": f"시뮬레이션 시작 - 총 {len(step_summaries)}개 스텝",
                "stepDetails": [s.model_dump() for s in redis_step_statuses]
            }

            # Redis 초기화
            await redis_client.client.set(primary_redis_key, json.dumps(initial_status))
            await redis_client.client.set(execution_redis_key, json.dumps(initial_status))
            
            total_execution_summary = {
                "execution_id": execution_id,
                "total_steps": len(step_summaries),
                "completed_steps": 0,
                "failed_steps": 0,
                "total_pods_executed": 0,
                "total_success_pods": 0,
                "total_failed_pods": 0,
                "step_results": [],
                "simulation_status": "RUNNING",
                "failure_reason": None
            }

            # ----------------------------
            # 3️⃣ 스텝 단위 실행
            # ----------------------------
            for i, step in enumerate(step_summaries, 1):
                current_step = step
                step_start_time = datetime.now(timezone.utc)
                current_step_progress = 0.0
                current_step_repeat = 0
                
                # stop_event 조기 체크
                if stop_event.is_set():
                    await self._handle_entity_status(
                        entity_type="step",
                        simulation_id=simulation_id,
                        execution_id=execution_id,
                        entity_identifier=step.step_order,
                        status="STOPPED",
                        reason="사용자 요청에 의한 중지",
                        redis_client=redis_client,
                        current_repeat=current_step_repeat,
                        update_db=True
                    )
                    total_execution_summary["simulation_status"] = "STOPPED"
                    return total_execution_summary
                
                # Pod 조회
                try:
                    pod_list = PodService.get_pods_by_filter(
                        namespace=namespace,
                        filter_params=StepOrderFilter(step_order=step.step_order)
                    )
                    if not pod_list:
                        raise ValueError(f"스텝 {step.step_order}에서 Pod를 찾을 수 없음")
                except Exception as e:
                    failure_reason = f"스텝 {step.step_order} Pod 조회 실패: {str(e)}"
                    debug_print(f"❌ {failure_reason}")
                    await self._handle_entity_status(
                        entity_type="step",
                        simulation_id=simulation_id,
                        execution_id=execution_id,
                        entity_identifier=step.step_order,
                        status="FAILED",
                        reason=failure_reason,
                        redis_client=redis_client,
                        current_repeat=current_step_repeat,
                        update_db=True
                    )
                    total_execution_summary.update({
                        "failed_steps": total_execution_summary["failed_steps"] + 1,
                        "simulation_status": "FAILED",
                        "failure_reason": failure_reason
                    })
                    return total_execution_summary
                
                # Pod Task 병렬 실행
                pod_tasks = {
                    asyncio.create_task(
                        self.rosbag_executor.execute_single_pod(
                            pod, step_order=step.step_order
                        )
                    ): pod.metadata.name
                    for pod in pod_list
                }
                
                completed_pods = set()
                poll_interval = 1
                last_recorded_repeat = 0
                
                # Pod 진행률 모니터링 루프
                while len(completed_pods) < len(pod_list):
                    # 🔹 사용자 중단 체크
                    if stop_event.is_set():
                        debug_print(f"🛑 스텝 {step.step_order} 중지 감지 - stop_event 활성")
                        await self._handle_entity_status(
                            entity_type="step",
                            simulation_id=simulation_id,
                            execution_id=execution_id,
                            entity_identifier=step.step_order,
                            status="STOPPED",
                            redis_client=redis_client,
                            current_repeat=current_step_repeat,
                            update_db=True
                        )
                        total_execution_summary["simulation_status"] = "STOPPED"
                        return total_execution_summary
                    
                    # 🔹 완료된 Pod 확인
                    done_tasks = [t for t in pod_tasks if t.done()]
                    for task in done_tasks:
                        pod_name = pod_tasks.pop(task)
                        try:
                            _ = task.result()
                            completed_pods.add(pod_name)
                            debug_print(f"✅ Pod 완료: {pod_name} ({len(completed_pods)}/{len(pod_list)})")
                        except Exception as e:
                            debug_print(f"💥 Pod 실패: {pod_name}: {e}")
                            completed_pods.add(pod_name)

                    try:
                        # 🔹 진행률 계산
                        total_progress = await self._calculate_step_progress(pod_list, completed_pods)
                        step_progress = total_progress / len(pod_list)
                        current_step_progress = step_progress

                        # 🔹 각 Pod 별 현재 반복 조회
                        pod_repeats = await self._get_current_repeat(pod_list, completed_pods, step.total_repeats)
                        current_step_repeat = pod_repeats
                        if current_step_repeat > last_recorded_repeat:
                            last_recorded_repeat = current_step_repeat

                        # 🔹 각 Pod 상태 디버깅 출력
                        for pod in pod_list:
                            status = await self.rosbag_executor._check_pod_rosbag_status(pod)
                            if isinstance(status, dict):
                                pod_loop = status.get("current_loop", 1)
                                max_loops = max(status.get("max_loops") or 1, 1)
                                debug_print(f"🎮 Pod {pod.metadata.name}: 현재 반복 {pod_loop}/{max_loops}")
                            else:
                                debug_print(f"⚠️ Pod {pod.metadata.name}: 상태 확인 실패")

                        # 🟢 Redis 상태 업데이트 (DB 업데이트 없음)
                        await self._handle_entity_status(
                            entity_type="step",
                            simulation_id=simulation_id,
                            execution_id=execution_id,
                            entity_identifier=step.step_order,
                            status="RUNNING",
                            redis_client=redis_client,
                            current_repeat=current_step_repeat,
                            progress=current_step_progress,
                            update_db=False
                        )

                    except Exception as e:
                        debug_print(f"⚠️ 진행률 업데이트 오류: {e}")

                    await asyncio.sleep(poll_interval)

                
                # 스텝 완료 처리
                step_end_time = datetime.now(timezone.utc)
                step.execution_time = (step_end_time - step_start_time).total_seconds()
                try:
                    execution_summary = self.rosbag_executor.get_execution_summary([
                        task.result() for task in done_tasks if not isinstance(task.result(), Exception)
                    ])
                except Exception as e:
                    debug_print(f"⚠️ 실행 요약 생성 실패: {e}")
                    execution_summary = {
                        "total_pods": len(pod_list),
                        "success_count": len(completed_pods),
                        "failure_count": 0,
                        "details": []
                    }
                
                await self._handle_entity_status(
                    entity_type="step",
                    simulation_id=simulation_id,
                    execution_id=execution_id,
                    entity_identifier=step.step_order,
                    status="COMPLETED",
                    redis_client=redis_client,
                    current_repeat=current_step_repeat,
                    progress=1.0,
                    update_db=True
                )
                
                # total_execution_summary 업데이트
                current_step = None
                current_step_progress = 0.0
                current_step_repeat = 0
                pod_tasks = {}
                
                total_execution_summary["completed_steps"] += 1
                total_execution_summary["total_pods_executed"] += execution_summary['total_pods']
                total_execution_summary["total_success_pods"] += execution_summary['success_count']
                total_execution_summary["step_results"].append({
                    "step_id": step.id,
                    "step_order": step.step_order,
                    "status": "success",
                    "execution_summary": execution_summary,
                    "execution_time": step.execution_time,
                    "pod_count": len(pod_list)
                })
                
                if i < len(step_summaries) and step.delay_after_completion:
                    await asyncio.sleep(step.delay_after_completion)
            
            # ----------------------------
            # 4️⃣ 모든 스텝 완료 처리
            # ----------------------------
            await self._handle_entity_status(
                entity_type="simulation",
                simulation_id=simulation_id,
                execution_id=execution_id,
                entity_identifier=simulation_id,
                status="COMPLETED",
                redis_client=redis_client,
                update_db=True
            )
            total_execution_summary["simulation_status"] = "COMPLETED"
            debug_print(f"🎉 시뮬레이션 {simulation_id} 완료")
            return total_execution_summary
        
        except Exception as e:
            debug_print(f"❌ 시뮬레이션 {simulation_id} 실행 중 예외: {e}")
            try:
                if current_step:
                    await self._handle_entity_status(
                        entity_type="step",
                        simulation_id=simulation_id,
                        execution_id=execution_id,
                        entity_identifier=current_step.step_order,
                        status="FAILED",
                        reason=str(e),
                        redis_client=redis_client,
                        current_repeat=current_step_repeat,
                        update_db=True
                    )
                else:
                    await self._handle_entity_status(
                        entity_type="simulation",
                        simulation_id=simulation_id,
                        execution_id=execution_id,
                        entity_identifier=simulation_id,
                        status="FAILED",
                        reason=str(e),
                        redis_client=redis_client,
                        update_db=True
                    )
            except Exception as cleanup_error:
                debug_print(f"💥 정리 작업 중 추가 오류: {cleanup_error}")
            raise
        
        finally:
            await self._cleanup_resources_safe(
                redis_client=redis_client,
                pod_tasks=pod_tasks,
                simulation_id=simulation_id
            )


    async def _calculate_step_progress(
        self,
        pod_list: list,
        completed_pods: set
    ) -> float:
        """
        각 Pod의 current_loop / max_loops를 기반으로 Step 진행률 계산
        완료된 Pod는 100% 처리
        """
        total_progress = 0.0

        for pod in pod_list:
            pod_name = pod.metadata.name
            if pod_name in completed_pods:
                total_progress += 1.0
                continue

            try:
                status = await self.rosbag_executor._check_pod_rosbag_status(pod)
            except Exception:
                total_progress += 0.0
                continue

            if isinstance(status, dict):
                current_loop = status.get("current_loop", 1)
                max_loops = max(status.get("max_loops") or 1, 1)
                pod_progress = min(current_loop / max_loops, 1.0)
                total_progress += pod_progress
            else:
                total_progress += 0.0

        return total_progress / max(1, len(pod_list))

    
    async def _get_current_repeat(
        self,
        pod_list: list,
        completed_pods: set,
        total_repeats: int
    ) -> int:
        """
        각 Pod의 current_loop를 기준으로 현재 Step 반복 횟수 계산
        - total_repeats를 최대값으로 제한
        """
        if total_repeats <= 1:
            return 1

        loops = []
        for pod in pod_list:
            pod_name = pod.metadata.name
            if pod_name in completed_pods:
                loops.append(total_repeats)
                continue

            try:
                status = await self.rosbag_executor._check_pod_rosbag_status(pod)
            except Exception:
                loops.append(0)
                continue

            if isinstance(status, dict):
                current_loop = status.get("current_loop", 1)
                max_loops = max(status.get("max_loops") or 1, 1)
                loops.append(min(current_loop, max_loops))
            else:
                loops.append(0)

        # 전체 Step 반복 횟수는 Pod별 최소 반복 횟수 기준
        current_repeat = min(loops) if loops else 0
        return min(current_repeat, total_repeats)

    
    # ===== 안전한 헬퍼 메서드들 =====

    async def _update_redis_status_safe(
        self, redis_client, primary_key: str, execution_key: str,
        step_order: int = None, step_status: str = None, step_progress: float = None,
        overall_progress: float = None, current_repeat: int = None,
        message: str = None, started_at: datetime = None
    ):
        """Redis 상태 업데이트 (안전한 버전)"""
        try:
            # 기본 키로 상태 가져오기
            current_status = await redis_client.get(primary_key)
            if not current_status:
                return
            
            # 공통 업데이트
            update_data = {
                "timestamps": {
                    **current_status.get("timestamps", {}),
                    "lastUpdated": datetime.now(timezone.utc).isoformat()
                }
            }
            
            if message:
                update_data["message"] = message
            if overall_progress is not None:
                update_data["progress"] = {
                    **current_status.get("progress", {}),
                    "overallProgress": overall_progress
                }
            
            # 스텝별 업데이트
            if step_order and current_status.get("stepDetails"):
                for redis_step in current_status["stepDetails"]:
                    if redis_step["step_order"] == step_order:
                        step_update = {}
                        if step_status:
                            step_update["status"] = step_status
                        if step_progress is not None:
                            step_update["progress"] = step_progress
                        if current_repeat is not None:
                            step_update["current_repeat"] = current_repeat
                        if started_at:
                            step_update["started_at"] = started_at.isoformat()
                        
                        redis_step.update(step_update)
                        break
            
            current_status.update(update_data)
            
            # 두 키 모두 업데이트
            await redis_client.client.set(primary_key, current_status)
            await redis_client.client.set(execution_key, current_status)
            
        except Exception as e:
            debug_print(f"⚠️ Redis 상태 업데이트 실패: {e}")



    async def _handle_step_failure_safe(
        self, simulation_id: int, execution_id: int, step, current_repeat: int,
        failure_reason: str, redis_client, error_time: datetime
    ):
        """스텝 실패 처리 (안전한 버전)"""
        try:
            # ✅ DB 업데이트: Simulation + SimulationExecution + Step 모두
            async with self.sessionmaker() as db_session:
                # Simulation 상태 업데이트
                await self.repository.update_simulation_status(
                    simulation_id=simulation_id,
                    status=SimulationStatus.FAILED,
                    reason=failure_reason,
                    session=db_session
                )
                
                # SimulationExecution 상태 업데이트
                if execution_id:
                    await self.repository.update_execution_status(
                        execution_id=execution_id,
                        status=SimulationExecutionStatus.FAILED,
                        reason=failure_reason,
                        failed_at=error_time,
                        session=db_session
                    )
                
                # Step 상태 업데이트
                if step:
                    await self.repository.update_simulation_step_status(
                        step_id=step.id,
                        status=StepStatus.FAILED,
                        failed_at=error_time,
                        session=db_session
                    )
                    await self.repository.update_simulation_step_current_repeat(
                        step_id=step.id,
                        current_repeat=current_repeat,
                        session=db_session
                    )
        
        except Exception as e:
            debug_print(f"💥 DB 상태 업데이트 실패: {e}")

    async def _cleanup_resources_safe(
        self, redis_client, pod_tasks: dict, simulation_id: int
    ):
        """리소스 정리 (안전한 버전)"""
        try:
            # Pod tasks 정리
            if pod_tasks:
                for task in pod_tasks.keys():
                    if not task.done():
                        task.cancel()
                
                # 취소된 태스크들 완료 대기
                if pod_tasks:
                    await asyncio.gather(*pod_tasks.keys(), return_exceptions=True)
        
        except Exception as e:
            debug_print(f"⚠️ Pod tasks 정리 중 오류: {e}")
        
        try:
            # Redis 연결 정리
            if redis_client and redis_client.client:
                await redis_client.client.close()
        except Exception as e:
            debug_print(f"⚠️ Redis 정리 중 오류: {e}")
        
        try:
            # 시뮬레이션 정리
            await self._cleanup_simulation(simulation_id)
        except Exception as e:
            debug_print(f"⚠️ 시뮬레이션 정리 중 오류: {e}")

      
    async def _run_sequential_simulation_with_progress(self, simulation_id: int, stop_event: asyncio.Event):
        """
        순차 패턴 시뮬레이션 실행
        - 각 스텝을 순차적으로 처리
        - 1초 단위 Pod 진행상황 모니터링
        - Redis를 통한 실시간 진행상황 업데이트 (DB 업데이트 최소화)
        - 실패/중단 시 해당 스텝만 정확히 DB에 기록
        """
        redis_client = RedisSimulationClient()
        await redis_client.connect()
        
        # 현재 실행 중인 스텝 정보 추적용
        current_step = None
        current_step_progress = 0.0
        current_step_repeat = 0
    
        try:
            debug_print(f"백그라운드에서 시뮬레이션 실행 시작: {simulation_id}")

            # ----------------------------
            # 1️⃣ 시작 트랜잭션: SimulationExecution + RUNNING 상태
            # ----------------------------
            async with self.sessionmaker() as db_session:
                # 스텝 조회
                simulation = await self.repository.find_by_id(simulation_id, db_session)
                if not simulation:
                    raise SimulationNotFoundError(simulation.id)
                
                # 🔹 Detached 방지: 필요한 필드만 추출
                namespace = simulation.namespace
                created_at = simulation.created_at
                
                steps = await self.repository.find_simulation_steps(simulation_id, db_session)
                debug_print(f"📊 스텝 조회 완료: {len(steps)}개")
                
                # DetachedInstanceError 방지용 StepSummary 리스트
                step_summaries = [
                    StepSummary(
                        id=step.id,
                        step_order=step.step_order,
                        status="PENDING",
                        autonomous_agent_count=step.autonomous_agent_count,
                        current_repeat=0,
                        total_repeats=step.repeat_count or 1,
                        delay_after_completion=getattr(step, "delay_after_completion", 0),
                    )
                    for step in steps
                ]

                # ✅ DB 업데이트 - 시뮬레이션 시작 시에만
                await self._update_simulation_status_and_log(simulation_id, "RUNNING", "시뮬레이션 실행 시작", session=db_session)
                
                # SimulationExecution 생성
                execution = SimulationExecution(simulation_id=simulation.id)
                execution.start_execution() # 상태 RUNNING + started_at 기록
                db_session.add(execution)
                await db_session.commit()

            # ----------------------------
            # 2️⃣ Redis 초기 상태 설정
            # ----------------------------
            redis_step_statuses = [
                RedisStepStatus(
                    stepOrder=s.step_order,
                    totalRepeats=s.total_repeats,
                )
                for s in step_summaries
            ]
            
            current_time = datetime.now(timezone.utc)
            initial_status = {
                "status": "RUNNING",
                "progress": {
                    "overallProgress": 0.0,
                    "currentStep": 0,
                    "completedSteps": 0,
                    "totalSteps": len(step_summaries)
                },
                "timestamps": {
                    "createdAt": created_at.isoformat() if created_at else None,
                    "lastUpdated": current_time.isoformat(),
                    "startedAt": current_time.isoformat(),
                    "completedAt": None,
                    "failedAt": None,
                    "stoppedAt": None
                },
                "message": f"시뮬레이션 시작 - 총 {len(steps)}개 스텝",
                "stepDetails":  [s.__dict__ for s in redis_step_statuses]
            }
            await redis_client.set_simulation_status(simulation_id, initial_status)

            total_execution_summary = {
                "total_steps": len(step_summaries),
                "completed_steps": 0,
                "failed_steps": 0,
                "total_pods_executed": 0,
                "total_success_pods": 0,
                "total_failed_pods": 0,
                "step_results": [],
                "simulation_status": "RUNNING",
                "failure_reason": None
            }

            # ----------------------------
            # 3️⃣ 스텝 단위 실행
            # ----------------------------
            for i, step in enumerate(step_summaries, 1):
                debug_print(f"\n🔄 스텝 {i}/{len(step_summaries)} 처리 시작 - Step Order: {step.step_order}")
                
                
                # 현재 실행 중인 스텝 추적 정보 업데이트
                step_start_time = datetime.now(timezone.utc)
                current_step = step
                current_step_progress = 0.0
                current_step_repeat = 0
                
                debug_print(f"📝 Step {step.step_order} 실행 시작 - Redis에만 상태 업데이트")

                # Redis 상태 업데이트 (스텝 RUNNING)
                current_status = await redis_client.get_simulation_status(simulation_id)
                if current_status:
                    # 현재 스텝 정보 업데이트
                    current_status["progress"]["currentStep"] = step.step_order
                    current_status["message"] = f"스텝 {step.step_order}/{len(step_summaries)} 실행 중"
                    current_status["timestamps"]["lastUpdated"] = step_start_time.isoformat()
                    
                    # 스텝 디테일 업데이트
                    for step_detail in current_status["stepDetails"]:
                        if step_detail["stepOrder"] == step.step_order:
                            step_detail.update({
                                "status": "RUNNING",
                                "startedAt": step_start_time.isoformat(),
                                "progress": 0.0
                            })
                            break
                            
                    await redis_client.set_simulation_status(simulation_id, current_status)

                # Pod 조회
                pod_list = PodService.get_pods_by_filter(
                    namespace=namespace,
                    filter_params=StepOrderFilter(step_order=step.step_order)
                )

                if not pod_list:
                    failure_reason = f"스텝 {step.step_order}에서 Pod를 찾을 수 없음"
                    debug_print(f"❌ {failure_reason}")
                    
                    failed_time = datetime.now(timezone.utc)
                    
                    # ✅ DB 업데이트 - 실패한 스텝만 정확히 기록
                    await self.repository.update_simulation_step_status(
                        step_id=step.id,
                        status=StepStatus.FAILED,
                        failed_at=failed_time
                    )
                    # 추가 실패 정보 업데이트 (current_repeat, progress 등)
                    await self.repository.update_simulation_step_current_repeat(
                        step_id=step.id, 
                        current_repeat=current_step_repeat
                    )
                    debug_print(f"✅ DB 업데이트 완료 - Step {step.step_order} FAILED 상태 기록")
                    
                    # ⚡ Redis 실패 상태 업데이트
                    current_status = await redis_client.get_simulation_status(simulation_id)
                    if current_status:
                        current_status["status"] = "FAILED"
                        current_status["message"] = failure_reason
                        current_status["timestamps"].update({
                            "lastUpdated": failed_time.isoformat(),
                            "failedAt": failed_time.isoformat()
                        })
                        
                        # 스텝 디테일 업데이트
                        for step_detail in current_status["stepDetails"]:
                            if step_detail["stepOrder"] == step.step_order:
                                step_detail.update({
                                    "status": "FAILED",
                                    "failedAt": failed_time.isoformat(),
                                    "error": failure_reason,
                                    "currentRepeat": current_step_repeat,
                                    "progress": current_step_progress
                                })
                                break
                        
                        await redis_client.set_simulation_status(simulation_id, current_status)
                    
                    total_execution_summary.update({
                        "failed_steps": total_execution_summary["failed_steps"] + 1,
                        "simulation_status": "FAILED",
                        "failure_reason": failure_reason
                    })
                    
                    # ✅ DB 업데이트 - 최종 시뮬레이션 실패 상태
                    await self._update_simulation_status_and_log(simulation_id, "FAILED", failure_reason)
                    return total_execution_summary

                # Pod Task 생성
                pod_tasks = {
                    asyncio.create_task(
                        self.rosbag_executor.execute_single_pod(
                            pod,
                            step_order=step.step_order
                        )
                    ): pod.metadata.name
                    for pod in pod_list
                }

                completed_pods = set()
                poll_interval = 1  # 1초 단위 진행상황
                
                debug_print(f"📋 Step {step.step_order} Pod Task 생성 완료: {len(pod_tasks)}개 Pod 병렬 실행 시작")

                # 3️⃣ Pod 진행상황 루프 - Redis Only 실시간 업데이트
                last_recorded_repeat = 0  # 메모리 기반 반복 횟수 관리
                
                while len(completed_pods) < len(pod_list):
                    done_tasks = [t for t in pod_tasks if t.done()]

                    # 완료된 Pod 처리
                    for task in done_tasks:
                        pod_name = pod_tasks.pop(task)
                        try:
                            result = task.result()
                            debug_print(f"✅ Pod 완료: {result.pod_name} ({len(completed_pods)}/{len(pod_list)})")
                        except asyncio.CancelledError:
                            debug_print(f"🛑 Pod CancelledError 감지: {pod_name}")
                        except Exception as e:
                            debug_print(f"💥 Pod 실행 실패: {pod_name}: {e}")

                    # 진행 중 Pod 상태 확인 및 로그
                    total_progress = 0.0
                    running_info = []
                    
                    debug_print(f"🔍 Pod 상태 체크 시작 - completed_pods: {completed_pods}")

                    status_tasks = {
                        pod.metadata.name: asyncio.create_task(self.rosbag_executor._check_pod_rosbag_status(pod))
                        for pod in pod_list
                    }

                    pod_statuses = await asyncio.gather(*status_tasks.values(), return_exceptions=True)
                    
                    current_total_loops = 0
                    max_total_loops = 0
                    
                    # 🔍 각 Pod별 상세 디버깅
                    debug_print(f"📊 === Pod별 진행률 상세 분석 (Step {step.step_order}) ===")

                    loops = []  # (current_loop, max_loops) 집계용

                    for pod_name, status in zip(status_tasks.keys(), pod_statuses):
                        debug_print(f"🔍 === Pod [{pod_name}] 상태 체크 시작 ===")
                        debug_print(f"Pod status: {status}")
                        # 이미 완료된 Pod는 바로 100% 처리
                        if pod_name in completed_pods:
                            pod_progress = 1.0
                            running_info.append(f"{pod_name}(완료)")
                        elif isinstance(status, dict):           
                            is_playing = status.get("is_playing", False)
                            current_loop = status.get("current_loop", 0)
                            max_loops = max(status.get("max_loops") or 1, 1)
                            
                            # 집계용 리스트에 기록
                            loops.append((current_loop, max_loops)) 
                            
                            debug_print(f"  🎮 {pod_name}: is_playing = {is_playing} (기본값: False)")
                            debug_print(f"  🔄 {pod_name}: current_loop = {current_loop} (기본값: 0)")
                            debug_print(f"  🎯 {pod_name}: max_loops = {max_loops} (기본값: 1, 원본값: {status.get('max_loops')})")
                            
                            # 전체 진행률 계산을 위한 루프 수 집계
                            current_total_loops += current_loop
                            max_total_loops += max_loops
                            
                            pod_progress = min(current_loop / max_loops, 1.0)
                            
                            if current_loop >= max_loops and not is_playing:
                                # 실제로 완료된 경우
                                completed_pods.add(pod_name)
                                pod_progress = 1.0
                                running_info.append(f"{pod_name}(완료)")
                                debug_print(f"  ✅ {pod_name}: 상태체크로 완료 감지 -> completed_pods에 추가")
                            elif is_playing:
                                running_info.append(f"{pod_name}({current_loop}/{max_loops})")
                                debug_print(f"  ⏳ {pod_name}: 실행중 -> 진행률 {pod_progress:.1%}")
                            else:
                                # is_playing이 False이지만 아직 완료되지 않은 경우
                                # 무조건 1.0이 아닌 실제 진행률 사용
                                running_info.append(f"{pod_name}({current_loop}/{max_loops}-중지됨)")
                                debug_print(f"  ⏸️ {pod_name}: 중지됨 -> 진행률 {pod_progress:.1%}")
                        else:
                            pod_progress = 0.0
                            running_info.append(f"{pod_name}(상태체크실패)")
                            debug_print(f"  ❌ {pod_name}: 상태체크 실패 -> 0%")

                        total_progress += pod_progress
                        debug_print(f"  📊 {pod_name}: pod_progress={pod_progress:.2f}, 누적 total_progress={total_progress:.2f}")

                    # 그룹 반복 갱신 (Redis Only)
                    if loops:
                        group_current_loop = min(cl for cl, _ in loops)
                        group_max_loops = min(ml for _, ml in loops)
                        target_cap = step.total_repeats or group_max_loops
                        new_repeat = min(group_current_loop, target_cap)

                        if new_repeat > last_recorded_repeat:
                            last_recorded_repeat = new_repeat
                            current_step_repeat = new_repeat  # 추적 정보 업데이트
                            debug_print(f"🔁 Step {step.step_order} 반복 갱신: {new_repeat}/{target_cap} (Redis Only)")

                    group_progress = (total_progress / len(pod_list)) * 100
                    step_progress = total_progress / len(pod_list)
                    current_step_progress = step_progress  # 추적 정보 업데이트
                    
                    # ⚡ Redis Only - 실시간 진행률 업데이트
                    current_status = await redis_client.get_simulation_status(simulation_id)
                    if current_status:
                        # 전체 진행률 계산 (완료된 스텝 + 현재 스텝 진행률)
                        overall_progress = (total_execution_summary["completed_steps"] + step_progress) / len(steps)
                        
                        current_status["progress"]["overallProgress"] = overall_progress
                        current_status["message"] = f"스텝 {step.step_order}/{len(steps)} - {group_progress:.1f}% ({len(completed_pods)}/{len(pod_list)} pods)"
                        current_status["timestamps"]["lastUpdated"] = datetime.now(timezone.utc).isoformat()
                        
                        # 스텝 디테일 업데이트
                        for step_detail in current_status["stepDetails"]:
                            if step_detail["stepOrder"] == step.step_order:
                                step_detail.update({
                                    "progress": step_progress,
                                    "autonomousAgents": len(pod_list),
                                    "currentRepeat": new_repeat if loops else 0,
                                    "totalRepeats": step.total_repeats or 1
                                })
                                break
                        
                        await redis_client.set_simulation_status(simulation_id, current_status)
                    
                    debug_print(f"⏳ Step {step.step_order} 진행률: {group_progress:.1f}% ({len(completed_pods)}/{len(pod_list)}) | 진행중: {', '.join(running_info)}")

                    # stop_event 감지
                    if stop_event.is_set():
                        debug_print(f"⏹️ 중지 이벤트 감지 - 스텝 {step.step_order} 즉시 종료")
                        
                        stopped_time = datetime.now(timezone.utc)
                        await self._handle_step_stopped(
                            simulation_id=simulation_id,
                            step=current_step,
                            current_repeat=current_step_repeat,
                            execution=execution,
                            stopped_time=stopped_time,
                            redis_client=redis_client,
                            pod_tasks=pod_tasks  # 현재 진행 중 Pod tasks
                        )
                        total_execution_summary["simulation_status"] = "STOPPED"
                        
                        return total_execution_summary

                    await asyncio.sleep(poll_interval)

                # 스텝 완료 처리
                step_end_time = datetime.now(timezone.utc)
                step_execution_time = (step_end_time - step_start_time).total_seconds()
                
                # 실행 결과 요약 생성
                execution_summary = self.rosbag_executor.get_execution_summary([
                    task.result() for task in done_tasks if not isinstance(task.result(), Exception)
                ])
                
                await self._handle_step_completed(
                    simulation_id=simulation_id,
                    step=current_step,
                    step_end_time=step_end_time,
                    execution_summary=execution_summary,
                    redis_client=redis_client
                )
                
                # 추적 정보 초기화 (스텝 완료됨)
                current_step = None
                current_step_progress = 0.0
                current_step_repeat = 0
                
                debug_print(f"✅ Step {step.step_order} 완료 (실행시간: {step_execution_time:.1f}초)")
                
                # 전체 실행 요약 업데이트
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

            # ----------------------------
            # 4️⃣ 모든 스텝 완료 처리
            # ----------------------------
            completed_time = datetime.now(timezone.utc)
            await self._handle_simulation_completed(
                simulation_id=simulation_id,
                completed_time=completed_time,
                execution=execution,
                execution_summary=total_execution_summary,
                redis_client=redis_client
            )
            
            total_execution_summary["simulation_status"] = "COMPLETED"
            
            debug_print(f"🎉 시뮬레이션 {simulation_id} 완료")
            return total_execution_summary
        except Exception as e:
            debug_print(f"❌ 시뮬레이션 {simulation_id} 실행 중 예외: {e}")
            
            error_time = datetime.now(timezone.utc)
            
            # ✅ DB 업데이트 - 현재 실행 중인 스텝만 정확히 실패 기록
            if current_step:
                await self._handle_step_failure(
                    simulation_id=simulation_id,
                    step=current_step,
                    current_repeat=current_step_repeat,
                    failure_reason=str(e),
                    execution=execution,
                    redis_client=redis_client,
                    error_time=error_time
                )
            raise
        finally:
            # Redis 정리는 TTL에 맡기고, 연결만 정리
            if redis_client.client:
                await redis_client.client.close()
            await self._cleanup_simulation(simulation_id)

    async def _handle_step_failure(
        self,
        simulation_id: int,
        step: SimulationStep,
        current_repeat: int,
        failure_reason: str,
        execution: SimulationExecution,
        redis_client: RedisSimulationClient,
        error_time: Optional[datetime] = None
    ):
        error_time = error_time or datetime.now(timezone.utc)
        
        # DB 트랜잭션
        async with self.sessionmaker() as db_session:
            await self.repository.update_simulation_step_status(
                step_id=step.id,
                status=StepStatus.FAILED,
                failed_at=error_time,
                session=db_session
            )
            await self.repository.update_simulation_step_current_repeat(
                step_id=step.id,
                current_repeat=current_repeat,
                session=db_session
            )

            # SimulationExecution 실패 처리
            if execution:
                execution.fail_execution(message=failure_reason)
                db_session.add(execution)
            await db_session.commit()

            # 전체 시뮬레이션 상태 FAILED
            await self._update_simulation_status_and_log(simulation_id, "FAILED", failure_reason, session=db_session)

        # Redis 업데이트
        current_status = await redis_client.get_simulation_status(simulation_id)
        if current_status:
            current_status["status"] = "FAILED"
            current_status["message"] = failure_reason
            current_status["timestamps"].update({
                "lastUpdated": error_time.isoformat(),
                "failedAt": error_time.isoformat()
            })
            for step_detail in current_status["stepDetails"]:
                if step_detail["stepOrder"] == step.step_order:
                    step_detail.update({
                        "status": "FAILED",
                        "failedAt": error_time.isoformat(),
                        "error": failure_reason,
                        "currentRepeat": current_repeat
                    })
                    break
            await redis_client.set_simulation_status(simulation_id, current_status)

    async def _handle_step_stopped(
        self,
        simulation_id: int,
        step: SimulationStep,
        current_repeat: int,
        execution: SimulationExecution,
        stopped_time: datetime,
        redis_client: RedisSimulationClient,
        pod_tasks: dict
    ):
        # DB 트랜잭션
        async with self.sessionmaker() as db_session:
            await self.repository.update_simulation_step_status(
                step_id=step.id,
                status=StepStatus.STOPPED,
                stopped_at=stopped_time,
                session=db_session
            )
            await self.repository.update_simulation_step_current_repeat(
                step_id=step.id,
                current_repeat=current_repeat,
                session=db_session
            )

            # SimulationExecution STOPPED 처리
            if execution:
                execution.mark_stopped(stopped_time)
                db_session.add(execution)
            await db_session.commit()

            # 전체 시뮬레이션 상태 STOPPED
            await self._update_simulation_status_and_log(
                simulation_id, "STOPPED", "사용자 중지", session=db_session
            )

        # Redis 업데이트
        current_status = await redis_client.get_simulation_status(simulation_id)
        if current_status:
            current_status["status"] = "STOPPED"
            current_status["message"] = "시뮬레이션이 사용자에 의해 중지되었습니다"
            current_status["timestamps"].update({
                "lastUpdated": stopped_time.isoformat(),
                "stoppedAt": stopped_time.isoformat()
            })
            for step_detail in current_status["stepDetails"]:
                if step_detail["stepOrder"] == step.step_order:
                    step_detail.update({
                        "status": "STOPPED",
                        "stoppedAt": stopped_time.isoformat(),
                        "currentRepeat": current_repeat
                    })
                    break
            await redis_client.set_simulation_status(simulation_id, current_status)

        # Pod Task 취소
        for t in pod_tasks.keys():
            t.cancel()
        await asyncio.gather(*pod_tasks.keys(), return_exceptions=True)

    async def _handle_step_completed(
        self,
        simulation_id: int,
        step: StepSummary,
        step_end_time: datetime,
        execution_summary: dict,
        redis_client: RedisSimulationClient
    ):
        async with self.sessionmaker() as db_session:
            # 스텝 완료 DB 업데이트
            await self.repository.update_simulation_step_status(
                step_id=step.id,
                status=StepStatus.COMPLETED,
                completed_at=step_end_time,
                session=db_session
            )
            await self.repository.update_simulation_step_current_repeat(
                step_id=step.id,
                current_repeat=step.total_repeats or 1,
                session=db_session
            )
            await db_session.commit()

        # Redis 업데이트
        current_status = await redis_client.get_simulation_status(simulation_id)
        if current_status:
            current_status["progress"]["completedSteps"] += 1
            overall_progress = current_status["progress"]["completedSteps"] / len(current_status["stepDetails"])
            current_status["progress"]["overallProgress"] = overall_progress
            current_status["message"] = f"스텝 {step.step_order} 완료 ({current_status['progress']['completedSteps']}/{len(current_status['stepDetails'])})"
            current_status["timestamps"]["lastUpdated"] = step_end_time.isoformat()

            for step_detail in current_status["stepDetails"]:
                if step_detail["stepOrder"] == step.step_order:
                    step_detail.update({
                        "status": "COMPLETED",
                        "progress": 1.0,
                        "completedAt": step_end_time.isoformat(),
                        "currentRepeat": step.total_repeats or 1
                    })
                    break
            await redis_client.set_simulation_status(simulation_id, current_status)

    async def _handle_simulation_completed(
        self,
        simulation_id: int,
        completed_time: datetime,
        execution: SimulationExecution,
        execution_summary: dict,
        redis_client: RedisSimulationClient
    ):
        async with self.sessionmaker() as db_session:
            # SimulationExecution 완료 처리
            if execution:
                execution.complete_execution(result_summary=execution_summary)
                db_session.add(execution)
            await db_session.commit()

            # 시뮬레이션 상태 완료
            await self._update_simulation_status_and_log(simulation_id, "COMPLETED", "모든 스텝 성공", session=db_session)

        # Redis 상태 업데이트
        current_status = await redis_client.get_simulation_status(simulation_id)
        if current_status:
            current_status["status"] = "COMPLETED"
            current_status["progress"]["overallProgress"] = 1.0
            current_status["message"] = f"시뮬레이션 완료 - 모든 {len(current_status['stepDetails'])}개 스텝 성공"
            current_status["timestamps"].update({
                "lastUpdated": completed_time.isoformat(),
                "completedAt": completed_time.isoformat()
            })
            await redis_client.set_simulation_status(simulation_id, current_status)


    async def _run_parallel_simulation_with_progress(self, simulation_id: int, stop_event: asyncio.Event):
        """
        순차 시뮬레이션 패턴 적용한 병렬 시뮬레이션 실행
        - 메모리 기반 current_repeat 추적 (순차 시뮬레이션과 동일 패턴)
        - Redis + DB 동시 업데이트 (Redis 1차 조회 대응)
        - 단순하고 효율적인 진행률 관리
        """
        debug_print("🚀 병렬 시뮬레이션 실행 시작", simulation_id=simulation_id)
        
        redis_client = RedisSimulationClient()
        await redis_client.connect()
        
        # 📊 각 그룹별 메모리 기반 진행률 추적 (순차 시뮬레이션 패턴)
        group_progress_tracker = {}  # {group_id: {"last_recorded_repeat": int, "current_progress": float}}
        
        try:
            # 1️⃣ 시뮬레이션 조회 및 초기화
            simulation = await self.find_simulation_by_id(simulation_id, "background parallel run")
            groups = await self.repository.find_simulation_groups(simulation_id)
            debug_print("✅ 시뮬레이션 조회 완료", simulation_id=simulation.id, group_count=len(groups))

            # 그룹별 추적 정보 초기화
            for group in groups:
                group_progress_tracker[group.id] = {
                    "last_recorded_repeat": 0,
                    "current_progress": 0.0,
                    "start_time": datetime.now(timezone.utc)
                }

            # ✅ DB 업데이트 - 시뮬레이션 시작 시에만
            await self._update_simulation_status_and_log(simulation_id, "RUNNING", "병렬 시뮬레이션 실행 시작")

            # Redis 초기 상태 설정
            current_time = datetime.now(timezone.utc)
            initial_status = {
                "status": "RUNNING",
                "progress": {
                    "overallProgress": 0.0,
                    "completedGroups": 0,
                    "runningGroups": len(groups),
                    "totalGroups": len(groups)
                },
                "timestamps": {
                    "createdAt": simulation.created_at.isoformat() if simulation.created_at else None,
                    "lastUpdated": current_time.isoformat(),
                    "startedAt": current_time.isoformat(),
                    "completedAt": None,
                    "failedAt": None,
                    "stoppedAt": None
                },
                "message": f"병렬 시뮬레이션 시작 - 총 {len(groups)}개 그룹",
                "groupDetails": [
                    {
                        "groupId": group.id,
                        "status": "RUNNING",
                        "progress": 0.0,
                        "startedAt": current_time.isoformat(),
                        "completedAt": None,
                        "failedAt": None,
                        "stoppedAt": None,
                        "autonomousAgents": group.autonomous_agent_count,
                        "currentRepeat": 0,
                        "totalRepeats": group.repeat_count or 1,
                        "error": None
                    } for group in groups
                ]
            }
            await redis_client.set_simulation_status(simulation_id, initial_status)

            # 2️⃣ 모든 그룹 병렬 실행
            group_tasks = []
            
            # 각 그룹을 비동기 태스크로 생성
            for group in groups:
                task = asyncio.create_task(
                    self._execute_single_group_with_memory_tracking(
                        simulation, group, redis_client, simulation_id, group_progress_tracker
                    )
                )
                task.group_id = group.id # 태스크에 그룹 ID 할당
                group_tasks.append(task)

            debug_print("🎯 모든 그룹 병렬 실행 시작", simulation_id=simulation_id, total_groups=len(groups))

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

            # 3️⃣ 실행 중 진행상황 감시 + 즉시 취소 처리
            poll_interval = 1.0
            while group_tasks:
                # 루프 시작 시 상태 체크
                debug_print(f"🔍 루프 시작: {len(group_tasks)}개 태스크 대기")
                for i, t in enumerate(group_tasks):
                    gid = getattr(t, 'group_id', None)
                    debug_print(f"  태스크 {i}: 그룹{gid} done={t.done()}")
                
                try:
                    done, pending = await asyncio.wait(
                        group_tasks, 
                        timeout=poll_interval, 
                        return_when=asyncio.FIRST_COMPLETED
                    )

                    debug_print(f"📊 wait 결과: done={len(done)}, pending={len(pending)}")
        
                    # 완료된 그룹 처리
                    for t in done:
                        group_id = getattr(t, 'group_id', None)
                        debug_print(f"🎯 그룹 {group_id} 결과 처리 시작")
                        try:
                            group_result = t.result()
                            debug_print(f"✅ 그룹 {group_id} 정상 완료: {group_result}")
                        except asyncio.CancelledError:
                            debug_print("🛑 그룹 CancelledError 감지", group_id=group_id)
                            group_result = {
                                "group_id": group_id,
                                "status": "stopped",
                                "total_pod_count": 0,
                                "success_pod_count": 0,
                                "failed_pod_count": 0,
                                "failure_reason": "사용자 요청으로 중지"
                            }
                        except Exception as e:
                            debug_print(f"그룹 {group_id} 실행 실패", error=str(e))
                            traceback.print_exception(type(e), e, e.__traceback__)

                        debug_print(f"그룹 {group_id} 최종 결과: {group_result}")
                        total_summary["group_results"].append(group_result)
                        
                        # 태스크 제거
                        if t in group_tasks:
                            group_tasks.remove(t)

                        # 그룹 완료/실패 처리 - ✅ DB + Redis 동시 업데이트
                        if group_result["status"] == "success":
                            debug_print(f"그룹 {group.id} 완료 처리됨")
                            total_summary["completed_groups"] += 1
                            total_summary["total_success_pods"] += group_result["success_pod_count"]
                            
                            # 📊 최종 current_repeat를 DB + Redis에 동시 기록
                            final_repeat = group_progress_tracker.get(group_id, {}).get("last_recorded_repeat", 0)
                            await self._update_group_final_status_with_redis(
                                group_id, GroupStatus.COMPLETED, final_repeat, 
                                redis_client, simulation_id,
                                group_result["total_pod_count"]
                            )
                            
                        elif group_result["status"] == "failed":
                            total_summary["failed_groups"] += 1
                            total_summary["total_failed_pods"] += group_result.get("failed_pod_count", 0)
                            if not total_summary["failure_reason"]:
                                total_summary["failure_reason"] = group_result.get("failure_reason")
                            
                            # 📊 최종 current_repeat를 DB + Redis에 동시 기록
                            final_repeat = group_progress_tracker.get(group_id, {}).get("last_recorded_repeat", 0)
                            await self._update_group_final_status_with_redis(
                                group_id, GroupStatus.FAILED, final_repeat,
                                redis_client, simulation_id,
                                group_result["total_pod_count"],
                                failure_reason=group_result.get("failure_reason")
                            )
                            
                        elif group_result["status"] == "stopped":
                            debug_print("🛑 그룹 취소 감지", group_id=group_result["group_id"])
                            
                            # 📊 최종 current_repeat를 DB + Redis에 동시 기록
                            final_repeat = group_progress_tracker.get(group_id, {}).get("last_recorded_repeat", 0)
                            await self._update_group_final_status_with_redis(
                                group_id, GroupStatus.STOPPED, final_repeat,
                                redis_client, simulation_id,
                                group_result["total_pod_count"]
                            )

                        total_summary["total_pods_executed"] += group_result["total_pod_count"]

                    # ⚡ Redis 전체 진행률 업데이트 (순차 시뮬레이션 패턴)
                    await self._update_overall_progress_redis(redis_client, simulation_id, total_summary, len(groups), group_tasks)

                    # stop_event 감지 시 남은 모든 그룹 취소
                    if stop_event.is_set():
                        debug_print("🛑 시뮬레이션 중지 감지, 남은 그룹 취소 시작", pending_groups=len(pending))
                        
                        stopped_time = datetime.now(timezone.utc)
                        
                        # ✅ DB + Redis 동시 업데이트 - 현재 실행 중인 그룹들의 최종 상태만 기록
                        for task in pending:
                            group_id = getattr(task, 'group_id', None)
                            if group_id and group_id in group_progress_tracker:
                                final_repeat = group_progress_tracker[group_id]["last_recorded_repeat"]
                                await self._update_group_final_status_with_redis(
                                    group_id, GroupStatus.STOPPED, final_repeat,
                                    redis_client, simulation_id, 0  # Pod 수는 알 수 없으므로 0
                                )
                        
                        # 그룹 태스크 취소
                        for t in pending:
                            t.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)

                        # ⚡ Redis 전체 시뮬레이션 중지 상태 업데이트
                        await self._update_simulation_stopped_redis(redis_client, simulation_id, stopped_time, group_progress_tracker)

                        total_summary["simulation_status"] = "STOPPED"
                        
                        break
                except Exception as loop_error:
                    debug_print(f"❌ 루프 실행 중 치명적 오류: {loop_error}")
                    traceback.print_exc()
                    break
                
                debug_print(f"🔄 루프 종료, 남은 태스크: {len(group_tasks)}개")

            # 4️⃣ 최종 상태 결정
            debug_print("최종 상태 결정")
            if total_summary["simulation_status"] != "STOPPED":
                completed_time = datetime.now(timezone.utc)
                
                if total_summary["failed_groups"] > 0:
                    total_summary["simulation_status"] = "FAILED"
                    reason = total_summary["failure_reason"] or f"{total_summary['failed_groups']}개 그룹 실패"
                    
                    # ⚡ Redis 최종 실패 상태 업데이트
                    current_status = await redis_client.get_simulation_status(simulation_id)
                    if current_status:
                        current_status["status"] = "FAILED"
                        current_status["message"] = reason
                        current_status["timestamps"].update({
                            "lastUpdated": completed_time.isoformat(),
                            "failedAt": completed_time.isoformat()
                        })
                        await redis_client.set_simulation_status(simulation_id, current_status)
                    
                    await self._update_simulation_status_and_log(simulation_id, "FAILED", reason)
                else:
                    total_summary["simulation_status"] = "COMPLETED"
                    
                    # ⚡ Redis 최종 완료 상태 업데이트
                    current_status = await redis_client.get_simulation_status(simulation_id)
                    if current_status:
                        current_status["status"] = "COMPLETED"
                        current_status["progress"]["overallProgress"] = 1.0
                        current_status["message"] = f"병렬 시뮬레이션 완료 - 모든 {len(groups)}개 그룹 성공"
                        current_status["timestamps"].update({
                            "lastUpdated": completed_time.isoformat(),
                            "completedAt": completed_time.isoformat()
                        })
                        await redis_client.set_simulation_status(simulation_id, current_status)
                    
                    await self._update_simulation_status_and_log(simulation_id, "COMPLETED", "모든 그룹 완료")

            debug_print("🎉 병렬 시뮬레이션 실행 완료", simulation_id=simulation_id)
            return total_summary
            
        except Exception as e:
            traceback.print_exception()
            # 📊 예외 시에도 DB + Redis 동시 기록
            await self._handle_failed_groups_with_redis(group_progress_tracker, redis_client, simulation_id, str(e))
            raise
        finally:
            if redis_client.client:
                await redis_client.client.close()
            await self._cleanup_simulation(simulation_id)

    async def _execute_single_group_with_memory_tracking(self, simulation, group, redis_client, simulation_id, group_progress_tracker):
        debug_print("🔸 그룹 실행 시작", group_id=group.id, simulation_id=simulation.id)
        start_time = datetime.now(timezone.utc)

        try:
            # 1️⃣ 그룹 Pod 조회
            pod_list = PodService.get_pods_by_filter(
                namespace=simulation.namespace,
                filter_params=GroupIdFilter(group_id=group.id)
            )
            if not pod_list:
                return {
                    "group_id": group.id,
                    "status": "failed",
                    "execution_time": (datetime.now(timezone.utc) - start_time).total_seconds(),
                    "total_pod_count": 0,
                    "success_pod_count": 0,
                    "failed_pod_count": 0,
                    "failure_reason": f"그룹 {group.id}에서 Pod를 찾을 수 없음"
                }

            total_pod_count = len(pod_list)
            debug_print(f"📋 그룹 {group.id} Pod 목록", pod_names=[pod.metadata.name for pod in pod_list], total_count=total_pod_count)

            # 2️⃣ Pod Task 실행 시작 (rosbag 시작 요청)
            pod_tasks = {asyncio.create_task(
                self.rosbag_executor.execute_single_pod(
                    pod,
                    group_id=group.id if group else None
                )
            ): pod.metadata.name for pod in pod_list}

            completed_pods = set()
            failed_pods = {}
            poll_interval = 1  # 1초 단위 진행상황 확인
            
            # ✅ 메모리 기반 반복 횟수 관리
            last_recorded_repeat = group_progress_tracker[group.id]["last_recorded_repeat"]
                

            # 3️⃣ Pod 진행상황 루프 - Redis 실시간 업데이트 (순차 시뮬레이션 패턴 적용)
            while len(completed_pods) < total_pod_count:
                # 완료된 Pod Task 처리
                done_tasks = [t for t in pod_tasks if t.done()]
                
                for task in done_tasks:
                    pod_name = pod_tasks.pop(task)
                    try:
                        result = task.result()
                        completed_pods.add(pod_name)
                        debug_print(f"✅ Pod 완료: {pod_name} ({len(completed_pods)}/{total_pod_count})")
                    except (asyncio.CancelledError, Exception) as e:
                        debug_print(f"💥 Pod 실행 실패: {pod_name}: {e}")
                        
                        # ✅ 실패한 Pod의 current_loop 조회
                        try:
                            failed_pod = next(pod for pod in pod_list if pod.metadata.name == pod_name)
                            failure_status = await self.rosbag_executor._check_pod_rosbag_status(failed_pod)
                            
                            if isinstance(failure_status, dict):
                                current_loop = failure_status.get("current_loop", 0)
                                max_loops = max(failure_status.get("max_loops") or 1, 1)
                                failure_progress = min(current_loop / max_loops, 1.0)
                            else:
                                failure_progress = 0.0
                                
                            failed_pods[pod_name] = failure_progress
                            debug_print(f"💥 Pod {pod_name} 실패 시점 진행률: {failure_progress:.1%} ({current_loop}/{max_loops})")
                            
                        except Exception as status_error:
                            debug_print(f"⚠️ 실패한 Pod {pod_name}의 상태 조회 실패: {status_error}")
                            failed_pods[pod_name] = 0.0
                        
                        # ✅ 즉시 시뮬레이션 종료
                        debug_print(f"🛑 Pod 실패로 인한 그룹 {group.id} 즉시 종료")
                        break
                    
                # ✅ 실패 감지 시 즉시 루프 종료
                if failed_pods:
                    debug_print(f"🛑 실패 감지 - 그룹 {group.id} 실행 중단")
                    break

                # ✅ 진행 중 Pod 상태 확인 및 로그 (asyncio.gather 패턴)
                total_progress = 0.0
                running_info = []
                
                debug_print(f"🔍 Pod 상태 체크 시작 - completed_pods: {completed_pods}")

                # 모든 Pod 상태를 동시에 확인
                status_tasks = {
                    pod.metadata.name: asyncio.create_task(self.rosbag_executor._check_pod_rosbag_status(pod))
                    for pod in pod_list
                }

                pod_statuses = await asyncio.gather(*status_tasks.values(), return_exceptions=True)
                
                # 🔍 각 Pod별 상세 디버깅
                debug_print(f"📊 === 그룹 {group.id} Pod별 진행률 상세 분석 ===")

                loops = []  # (current_loop, max_loops) 집계용

                for pod_name, status in zip(status_tasks.keys(), pod_statuses):
                    debug_print(f"🔍 === Pod [{pod_name}] 상태 체크 시작 ===")
                    debug_print(f"Pod status 정보: {status}")
                    
                    # 이미 완료된 Pod는 바로 100% 처리
                    if pod_name in completed_pods:
                        pod_progress = 1.0
                        running_info.append(f"{pod_name}(완료)")
                        debug_print(f"  ✅ {pod_name}: 이미 완료됨 -> 100%")
                    elif pod_name in failed_pods:
                        pod_progress = failed_pods[pod_name]  # 실패 시점까지의 진행률
                        running_info.append(f"{pod_name}(실패-{pod_progress:.1%})")
                        debug_print(f"  💥 {pod_name}: 실패 (시점 진행률: {pod_progress:.1%})")
                    elif isinstance(status, dict):           
                        is_playing = status.get("is_playing", False)
                        current_loop = status.get("current_loop", 0)
                        max_loops = max(status.get("max_loops") or 1, 1)
                        
                        # 집계용 리스트에 기록
                        loops.append((current_loop, max_loops)) 
                        
                        debug_print(f"  🎮 {pod_name}: is_playing = {is_playing}")
                        debug_print(f"  🔄 {pod_name}: current_loop = {current_loop}")
                        debug_print(f"  🎯 {pod_name}: max_loops = {max_loops}")
                        
                        pod_progress = min(current_loop / max_loops, 1.0)
                        
                        if current_loop >= max_loops and not is_playing:
                            # 실제로 완료된 경우
                            completed_pods.add(pod_name)
                            pod_progress = 1.0
                            running_info.append(f"{pod_name}(완료)")
                            debug_print(f"  ✅ {pod_name}: 상태체크로 완료 감지 -> completed_pods에 추가")
                        elif is_playing:
                            running_info.append(f"{pod_name}({current_loop}/{max_loops})")
                            debug_print(f"  ⏳ {pod_name}: 실행중 -> 진행률 {pod_progress:.1%}")
                        else:
                            # is_playing이 False이지만 아직 완료되지 않은 경우
                            running_info.append(f"{pod_name}({current_loop}/{max_loops}-중지됨)")
                            debug_print(f"  ⏸️ {pod_name}: 중지됨 -> 진행률 {pod_progress:.1%}")
                    else:
                        pod_progress = 0.0
                        running_info.append(f"{pod_name}(상태체크실패)")
                        debug_print(f"  ❌ {pod_name}: 상태체크 실패 -> 0%")

                    total_progress += pod_progress
                    debug_print(f"  📊 {pod_name}: pod_progress={pod_progress:.2f}, 누적 total_progress={total_progress:.2f}")

                # ✅ 그룹 반복 갱신 (Redis + 메모리)
                if loops:
                    group_current_loop = min(cl for cl, _ in loops)  # 가장 느린 Pod 기준
                    new_repeat = group_current_loop

                    if new_repeat > last_recorded_repeat:
                        last_recorded_repeat = new_repeat
                        group_progress_tracker[group.id]["last_recorded_repeat"] = new_repeat
                        debug_print(f"🔁 그룹 {group.id} 반복 갱신: {new_repeat} (Redis + 메모리)")

                # ✅ 그룹 진행률 계산
                debug_print(f"그룹 ID: {group.id}, total_progress: {total_progress}, total_pod_count: {total_pod_count}")
                group_progress = total_progress / total_pod_count
                group_progress_tracker[group.id]["current_progress"] = group_progress
                
                # ⚡ Redis 실시간 업데이트 - 그룹별 상태
                await self._update_group_status_in_redis(
                    redis_client, simulation_id, group.id, "RUNNING",
                    group_progress,
                    current_repeat=last_recorded_repeat
                )
                
                debug_print(f"⏳ 그룹 {group.id} 진행률: {group_progress:.1%} ({len(completed_pods)}/{total_pod_count}) | 진행중: {', '.join(running_info)}")

                # ✅ 실패한 Pod가 너무 많으면 그룹 실패 처리
                if len(failed_pods) >= total_pod_count:
                    debug_print(f"💥 그룹 {group.id}: 모든 Pod 실패")
                    break

                await asyncio.sleep(poll_interval)

            # 4️⃣ 그룹 완료 처리 - ✅ 최종 반복횟수 확정
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            success_count = len(completed_pods)
            failed_count = len(failed_pods)
            status = "success" if failed_count == 0 else "failed"

            # ✅ 최종 반복횟수 확정 및 업데이트
            final_repeat_count = group_progress_tracker[group.id]["last_recorded_repeat"]
            debug_print(f"✅ 그룹 {group.id} 최종 반복횟수 확정: {final_repeat_count}")

            debug_print(f"{group.id} 그룹 완료 처리됨")
            return {
                "group_id": group.id,
                "status": status,
                "execution_time": execution_time,
                "total_pod_count": total_pod_count,
                "success_pod_count": success_count,
                "failed_pod_count": failed_count,
                "failure_reason": f"{failed_count}개 Pod 실패" if failed_count > 0 else None
            }

        except asyncio.CancelledError:
            # ✅ 취소 시에도 현재까지의 최종 반복횟수 업데이트
            final_repeat_count = group_progress_tracker[group.id]["last_recorded_repeat"]
            debug_print(f"🛑 그룹 {group.id} 취소 - 최종 반복횟수: {final_repeat_count}")
            
            # 남은 Pod Task 취소
            for t in pod_tasks.keys():
                t.cancel()
            await asyncio.gather(*pod_tasks.keys(), return_exceptions=True)
            return {
                "group_id": group.id,
                "status": "stopped",
                "execution_time": (datetime.now(timezone.utc) - start_time).total_seconds(),
                "total_pod_count": total_pod_count if 'total_pod_count' in locals() else 0,
                "success_pod_count": len(completed_pods) if 'completed_pods' in locals() else 0,
                "failed_pod_count": len(failed_pods) if 'failed_pods' in locals() else 0,
                "failure_reason": "사용자 요청으로 중지"
            }

        except Exception as e:
            # ✅ 예외 시에도 현재까지의 최종 반복횟수 업데이트
            final_repeat_count = group_progress_tracker[group.id]["last_recorded_repeat"]
            debug_print(f"💥 그룹 {group.id} 실패 - 최종 반복횟수: {final_repeat_count}")
            
            # 남은 Pod Task 취소
            for t in pod_tasks.keys():
                t.cancel()
            await asyncio.gather(*pod_tasks.keys(), return_exceptions=True)
            return {
                "group_id": group.id,
                "status": "failed",
                "execution_time": (datetime.now(timezone.utc) - start_time).total_seconds(),
                "total_pod_count": total_pod_count if 'total_pod_count' in locals() else 0,
                "success_pod_count": len(completed_pods) if 'completed_pods' in locals() else 0,
                "failed_pod_count": len(failed_pods) if 'failed_pods' in locals() else 0,
                "failure_reason": str(e)
            }


    # 📊 Redis + DB 동시 업데이트 메서드들
    async def _update_group_final_status_with_redis(self, group_id: int, status: GroupStatus, final_repeat: int,
                                            redis_client, simulation_id: int, pod_count: int = 0,
                                            failure_reason: str = None):
        """그룹 최종 상태를 DB + Redis에 동시 기록"""
        timestamp = datetime.now(timezone.utc)
        
        try:
            debug_print(f"🔄 DB 업데이트 시도 - Group {group_id}: {status.value}")
            
            # ✅ DB 업데이트
            if status == GroupStatus.COMPLETED:
                await self.repository.update_simulation_group_status(
                    group_id=group_id,
                    status=status,
                    completed_at=timestamp
                )
            elif status == GroupStatus.FAILED:
                await self.repository.update_simulation_group_status(
                    group_id=group_id,
                    status=status,
                    failed_at=timestamp
                )
            elif status == GroupStatus.STOPPED:
                await self.repository.update_simulation_group_status(
                    group_id=group_id,
                    status=status,
                    stopped_at=timestamp
                )
            
            # 📊 최종 current_repeat 업데이트
            await self.repository.update_simulation_group_current_repeat(
                group_id=group_id,
                current_repeat=final_repeat
            )
            
            debug_print(f"✅ DB 업데이트 완료 - Group {group_id}: {status.value}")
            
        except Exception as db_error:
            debug_print(f"❌ DB 업데이트 실패 - Group {group_id}: {db_error}")
            traceback.print_exc()  # 전체 예외 스택 출력
            # DB 실패해도 Redis는 시도
            
            # 추가 디버깅: 트랜잭션 상태 확인
            try:
                in_tx = self.repository.session.in_transaction()
                debug_print(f"ℹ️ 트랜잭션 상태 - in_transaction: {in_tx}")
            except Exception as tx_error:
                debug_print(f"⚠️ 트랜잭션 상태 확인 실패: {tx_error}")
        
        try:
            # ⚡ Redis 그룹 상태 동시 업데이트
            await self._update_group_status_in_redis_final(
                redis_client, simulation_id, group_id, status.value.upper(),
                final_repeat, pod_count, timestamp, failure_reason
            )
            debug_print(f"✅ Redis 업데이트 완료 - Group {group_id}")
            
        except Exception as redis_error:
            debug_print(f"❌ Redis 업데이트 실패 - Group {group_id}: {redis_error}")
        
        debug_print(f"✅ 상태 업데이트 시도 완료 - Group {group_id}: {status.value}, current_repeat: {final_repeat}")

    async def _update_group_status_in_redis_final(self, redis_client, simulation_id: int, group_id: int,
                                                final_status: str, final_repeat: int, pod_count: int,
                                                timestamp: datetime, error_message: str = None):
        """Redis에서 특정 그룹의 최종 상태 업데이트"""
        current_status = await redis_client.get_simulation_status(simulation_id)
        if not current_status:
            return
        
        # groupDetails에서 해당 그룹 찾아서 업데이트
        for group_detail in current_status.get("groupDetails", []):
            if group_detail.get("groupId") == group_id:
                group_detail.update({
                    "status": final_status,
                    "progress": 100.0 if final_status == "COMPLETED" else group_detail.get("progress", 0.0),
                    "autonomousAgents": pod_count,
                    "currentRepeat": final_repeat,
                    "error": error_message
                })
                
                # 상태별 타임스탬프 설정
                timestamp_iso = timestamp.isoformat()
                if final_status == "COMPLETED":
                    group_detail["completedAt"] = timestamp_iso
                elif final_status == "FAILED":
                    group_detail["failedAt"] = timestamp_iso
                elif final_status == "STOPPED":
                    group_detail["stoppedAt"] = timestamp_iso
                
                debug_print(f"⚡ Redis 그룹 {group_id} 최종 상태 업데이트: {final_status}")
                break
        
        # 전체 상태 timestamps 업데이트
        current_status["timestamps"]["lastUpdated"] = timestamp.isoformat()
        
        await redis_client.set_simulation_status(simulation_id, current_status)


    async def _handle_cancelled_groups_with_redis(self, group_progress_tracker, redis_client, simulation_id):
        """취소된 그룹들의 최종 상태를 DB + Redis에 동시 기록"""
        cancelled_time = datetime.now(timezone.utc)
        
        for group_id, progress_info in group_progress_tracker.items():
            final_repeat = progress_info["last_recorded_repeat"]
            await self._update_group_final_status_with_redis(
                group_id, GroupStatus.STOPPED, final_repeat,
                redis_client, simulation_id, 0
            )
        
        # ⚡ Redis 전체 시뮬레이션 취소 상태 업데이트
        current_status = await redis_client.get_simulation_status(simulation_id)
        if current_status:
            current_status["status"] = "STOPPED"
            current_status["message"] = "병렬 시뮬레이션 태스크가 취소되었습니다"
            current_status["timestamps"].update({
                "lastUpdated": cancelled_time.isoformat(),
                "stoppedAt": cancelled_time.isoformat()
            })
            await redis_client.set_simulation_status(simulation_id, current_status)
        


    async def _handle_failed_groups_with_redis(self, group_progress_tracker, redis_client, simulation_id, error_msg):
        debug_print("_handle_failed_groups_with_redis 호출됨")
        """실패한 그룹들의 최종 상태를 DB + Redis에 동시 기록"""
        error_time = datetime.now(timezone.utc)
        
        for group_id, progress_info in group_progress_tracker.items():
            final_repeat = progress_info["last_recorded_repeat"]
            await self._update_group_final_status_with_redis(
                group_id, GroupStatus.FAILED, final_repeat,
                redis_client, simulation_id, 0, failure_reason=error_msg
            )
        
        # ⚡ Redis 전체 시뮬레이션 실패 상태 업데이트
        current_status = await redis_client.get_simulation_status(simulation_id)
        if current_status:
            current_status["status"] = "FAILED"
            current_status["message"] = f"병렬 시뮬레이션 실행 중 오류 발생: {error_msg}"
            current_status["timestamps"].update({
                "lastUpdated": error_time.isoformat(),
                "failedAt": error_time.isoformat()
            })
            await redis_client.set_simulation_status(simulation_id, current_status)
        
        await self._update_simulation_status_and_log(simulation_id, "FAILED", error_msg)

    async def _update_simulation_stopped_redis(self, redis_client, simulation_id, stopped_time, group_progress_tracker):
        """Redis 전체 시뮬레이션 중지 상태 업데이트"""
        current_status = await redis_client.get_simulation_status(simulation_id)
        if current_status:
            current_status["status"] = "STOPPED"
            current_status["message"] = "시뮬레이션이 사용자에 의해 중지되었습니다"
            current_status["timestamps"].update({
                "lastUpdated": stopped_time.isoformat(),
                "stoppedAt": stopped_time.isoformat()
            })
            
            # 실행 중인 그룹들을 STOPPED로 업데이트 (이미 완료된 것들은 그대로 유지)
            for group_detail in current_status["groupDetails"]:
                group_id = group_detail["groupId"]
                if group_id in group_progress_tracker and group_detail["status"] == "RUNNING":
                    group_detail.update({
                        "status": "STOPPED",
                        "stoppedAt": stopped_time.isoformat(),
                        "progress": group_progress_tracker[group_id]["current_progress"],
                        "currentRepeat": group_progress_tracker[group_id]["last_recorded_repeat"]
                    })
            
            await redis_client.set_simulation_status(simulation_id, current_status)


    # 📊 헬퍼 메서드들
    async def _update_group_final_status(self, group_id: int, status: GroupStatus, final_repeat: int, 
                                    failure_reason: str = None):
        """그룹 최종 상태를 DB에 기록 (current_repeat 포함)"""
        timestamp = datetime.now(timezone.utc)
        
        try:
            if status == GroupStatus.COMPLETED:
                await self.repository.update_simulation_group_status(
                    group_id=group_id,
                    status=status,
                    completed_at=timestamp
                )
            elif status == GroupStatus.FAILED:
                await self.repository.update_simulation_group_status(
                    group_id=group_id,
                    status=status,
                    failed_at=timestamp
                )
            elif status == GroupStatus.STOPPED:
                await self.repository.update_simulation_group_status(
                    group_id=group_id,
                    status=status,
                    stopped_at=timestamp
                )
            
            # 📊 최종 current_repeat 업데이트
            await self.repository.update_simulation_group_current_repeat(
                group_id=group_id,
                current_repeat=final_repeat
            )
        
            debug_print(f"✅ DB 최종 상태 기록 완료 - Group {group_id}: {status.value}, current_repeat: {final_repeat}")
        except Exception as db_error:
            # DB 실패 시 상세 로그
            debug_print(f"❌ DB 업데이트 실패 - Group {group_id}: {db_error}")
            traceback.print_exc()  # 전체 예외 스택 출력

    async def _update_overall_progress_redis(self, redis_client, simulation_id, total_summary, 
                                        total_groups, remaining_tasks):
        """전체 진행률 Redis 업데이트 (순차 시뮬레이션 패턴)"""
        current_status = await redis_client.get_simulation_status(simulation_id)
        if current_status:
            running_count = len(remaining_tasks)
            completed_count = total_summary["completed_groups"]
            failed_count = total_summary["failed_groups"]
            
            # ✅ 각 그룹의 실제 진행률을 고려한 가중 평균 계산
            overall_progress = await self._calculate_weighted_overall_progress_from_db(
                redis_client, simulation_id
            )
            
            current_status["progress"].update({
                "overallProgress": overall_progress,
                "completedGroups": completed_count,
                "runningGroups": running_count
            })
            current_status["message"] = f"병렬 실행 중 - 완료: {completed_count}, 실행중: {running_count}, 실패: {failed_count}"
            current_status["timestamps"]["lastUpdated"] = datetime.now(timezone.utc).isoformat()
            
            await redis_client.set_simulation_status(simulation_id, current_status)

    async def _calculate_weighted_overall_progress_from_db(self, redis_client: RedisSimulationClient, simulation_id):
        """DB에서 그룹 정보를 조회하여 가중 평균 계산"""
        try:
            # DB에서 그룹 목록 조회
            groups = await self.repository.find_simulation_groups(simulation_id)
            
            if not groups:
                return 0.0
            
            total_weighted_progress = 0.0
            total_agents = 0
            
            sim_status = await redis_client.get_simulation_status(simulation_id)
            for group in groups:
                # Redis에서 현재 그룹 진행률 조회
                debug_print(f"{group.id} 그룹에 대해 Redis 에서 현재 그룹 진행률 조회")
                group_status = next(
                    (g for g in sim_status["groupDetails"] if g["groupId"] == group.id),
                    None
                )
                debug_print(f"그룹 진행률 정보: {group_status}")
                
                if group_status:
                    group_progress = group_status.get("progress", 0.0)
                    if isinstance(group_progress, (int, float)) and group_progress > 1:
                        group_progress = group_progress / 100.0
                else:
                    # Redis에 없으면 DB의 calculate_progress 사용
                    group_progress = group.calculate_progress
                
                # DB의 에이전트 수 사용 (더 신뢰성 있음)
                autonomous_agents = group.autonomous_agent_count
                
                weighted_progress = group_progress * autonomous_agents
                total_weighted_progress += weighted_progress
                total_agents += autonomous_agents
                
                debug_print(f"그룹 {group.id}: progress={group_progress:.2%}, "
                        f"agents={autonomous_agents}, weighted={weighted_progress:.2f}")
            
            if total_agents > 0:
                overall_progress = total_weighted_progress / total_agents
                debug_print(f"DB 기반 전체 진행률: {overall_progress:.1f}%")
                return overall_progress
            else:
                return 0.0
        except Exception as e:
            debug_print(f"DB 기반 전체 진행률 계산 오류: {e}")
            return 0.0

    async def _handle_cancelled_groups(self, group_progress_tracker, redis_client, simulation_id):
        """취소된 그룹들의 최종 상태를 DB에 기록"""
        cancelled_time = datetime.now(timezone.utc)
        
        for group_id, progress_info in group_progress_tracker.items():
            final_repeat = progress_info["last_recorded_repeat"]
            await self._update_group_final_status(group_id, GroupStatus.STOPPED, final_repeat)
        

    async def _handle_failed_groups(self, group_progress_tracker, redis_client, simulation_id, error_msg):
        """실패한 그룹들의 최종 상태를 DB에 기록"""
        error_time = datetime.now(timezone.utc)
        
        for group_id, progress_info in group_progress_tracker.items():
            final_repeat = progress_info["last_recorded_repeat"]
            await self._update_group_final_status(group_id, GroupStatus.FAILED, final_repeat, 
                                                failure_reason=error_msg)
        
        await self._update_simulation_status_and_log(simulation_id, "FAILED", error_msg)

    async def _update_group_status_in_redis(self, redis_client, simulation_id, group_id, group_status, group_progress, current_repeat=None, error=None):
        """
        Redis에서 특정 그룹의 상태 업데이트
        """
        try:
            current_status = await redis_client.get_simulation_status(simulation_id)
            if current_status:
                current_time = datetime.now(timezone.utc).isoformat()
                current_status["timestamps"]["lastUpdated"] = current_time
                
                # 그룹 디테일 업데이트
                for group_detail in current_status["groupDetails"]:
                    if group_detail["groupId"] == group_id:
                        group_detail.update({
                            "status": group_status,
                            "progress": round(group_progress * 100, 1),  # 0.65 -> 65.0
                        })
                        
                        if current_repeat is not None:
                            group_detail["currentRepeat"] = current_repeat
                        if error:
                            group_detail["error"] = error
                            
                        # 상태별 타임스탬프 업데이트
                        if status == "COMPLETED":
                            group_detail["completedAt"] = current_time
                        elif status == "FAILED":
                            group_detail["failedAt"] = current_time
                        elif status == "STOPPED":
                            group_detail["stoppedAt"] = current_time
                        
                        break
                
                await redis_client.set_simulation_status(simulation_id, current_status)
        except Exception as e:
            debug_print(f"❌ Redis 그룹 상태 업데이트 실패: {e}")
    
    
    async def _update_simulation_status_and_log(self, simulation_id: int, status: str, reason: str, session: Optional[AsyncSession] = None):
        """시뮬레이션 상태 업데이트 및 로깅 (Optional 세션 주입 가능)"""
        try:
            await self.repository.update_simulation_status(simulation_id, status, session=session)
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
            await RosService.send_post_request(pod_ip, "/rosbag/stop")

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
        """
        시뮬레이션 삭제
        - namespace, Redis, DB 단계별 상태를 Redis에 저장
        - Redis 연결 사용 후 반드시 close
        """
        redis_client = RedisSimulationClient()  # 싱글톤
        status = {
            "steps": {"namespace": "PENDING", "redis": "PENDING", "db": "PENDING"},
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "error_message": None
        }

        try:
            # 초기 상태 기록
            await redis_client.set_simulation_delete_status(simulation_id, status)

            # 1. 네임스페이스 삭제
            try:
                await self.pod_service.delete_namespace(simulation_id)
                status["steps"]["namespace"] = "COMPLETED"
            except Exception as e:
                status["steps"]["namespace"] = "FAILED"
                status["error_message"] = f"Namespace deletion failed: {e}"
                debug_print(f"[{simulation_id}] 네임스페이스 삭제 실패: {e}")
            finally:
                await redis_client.set_simulation_delete_status(simulation_id, status)

            # 2. Redis 삭제
            try:
                await redis_client.delete_simulation_status(simulation_id)
                status["steps"]["redis"] = "COMPLETED"
            except Exception as e:
                status["steps"]["redis"] = "FAILED"
                status["error_message"] = f"Redis deletion failed: {e}"
                
                debug_print(f"[{simulation_id}] Redis deletion failed: {e}")
            finally:
                await redis_client.set_simulation_delete_status(simulation_id, status)

            # 3. DB soft delete
            try:
                await self.repository.soft_delete_simulation(simulation_id)
                status["steps"]["db"] = "COMPLETED"
                
                await self.repository.update_simulation_status(simulation_id, SimulationStatus.DELETED)
            except Exception as e:
                status["steps"]["db"] = "FAILED"
                status["error_message"] = f"DB deletion failed: {e}"
                
                debug_print(f"[{simulation_id}] DB deletion failed: {e}")
                await redis_client.set_simulation_delete_status(simulation_id, status)
                raise RuntimeError(f"Simulation deletion failed at DB stage: {simulation_id}")
            finally:
                await redis_client.set_simulation_delete_status(simulation_id, status)

            debug_print(f"[{simulation_id}] Deletion completed: {status}")

            return SimulationDeleteResponse(simulation_id=simulation_id).model_dump()
        
        finally:
            # 모든 단계 완료 시점 기록
            status["completed_at"] = datetime.now(timezone.utc).isoformat()
            await redis_client.set_simulation_delete_status(simulation_id, status)
            
            # Redis 연결 종료
            if redis_client.client:
                await redis_client.client.close()
                redis_client.client = None
    
    async def get_deletion_status(self, simulation_id: int) -> dict:
        """
        Redis에서 삭제 상태 조회 및 안전한 연결 종료
        - 반환 구조:
        {
            "simulation_id": 123,
            "status": "PENDING",
            "steps": {"namespace": "COMPLETED", "redis": "PENDING", "db": "PENDING"},
            "started_at": "...",
            "completed_at": null,
            "error_message": null
        }
        """
        redis_client = RedisSimulationClient()
        try:
            await redis_client.connect()
            deletion_status = await redis_client.get_simulation_delete_status(simulation_id)
            if deletion_status is None:
                return None
            
            # started_at, completed_at 변환
            deletion_status["started_at"] = self._parse_datetime(deletion_status.get("started_at"))
            deletion_status["completed_at"] = self._parse_datetime(deletion_status.get("completed_at"))
            
            return {"simulation_id": simulation_id, **deletion_status}
        finally:
            # Redis 연결 종료
            if redis_client.client:
                await redis_client.client.close()
                redis_client.client = None

    async def find_simulation_by_id(self, simulation_id: int, api: str = "") -> Simulation:
        simulation = await self.repository.find_by_id(simulation_id)

        if not simulation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"존재하지 않는 시뮬레이션 ID",
            )
        return simulation

    async def get_simulation_status(self, simulation):
        instances = simulation.instances

        if not instances:
            return SimulationStatus.EMPTY.value

        for instance in instances:
            pod_ip = await self.pod_service.get_pod_ip(instance)
            pod_status = await RosService.get_pod_status(pod_ip)

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
                status_response = await RosService.get_pod_status(pod_ip)
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

    async def get_dashboard_data(self, simulation_id: int) -> DashboardData:
        # 1️⃣ 시뮬레이션 조회
        sim = await self.repository.find_by_id(simulation_id)
        if not sim:
            raise HTTPException(status_code=404, detail=f"Simulation {simulation_id} not found")

        # 2️⃣ 패턴별 ExecutionPlan 조회
        if sim.pattern_type == PatternType.SEQUENTIAL:
            execution_plan = await self.get_execution_plan_sequential(sim.id)
        elif sim.pattern_type == PatternType.PARALLEL:
            execution_plan = await self.get_execution_plan_parallel(sim.id)
        else:
            execution_plan = None  # 필요 시 기본 처리

        # 3️⃣ 상태 DTO 구성
        if sim.status == SimulationStatus.INITIATING:
            current_status = CurrentStatusInitiating(
                status=sim.status,
                timestamps=TimestampModel(
                    created_at=sim.created_at,
                    last_updated=sim.updated_at
                )
            )
        elif sim.status == SimulationStatus.PENDING:
            current_status = CurrentStatusPENDING(
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
            # 예외 상태 기본 처리
            current_status = CurrentStatusInitiating(
                status=sim.status,
                timestamps=TimestampModel(
                    created_at=sim.created_at,
                    last_updated=sim.updated_at
                )
            )

        # 4️⃣ SimulationData 생성
        simulation_data = SimulationData(
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

        try:
            # 5️⃣ 기본 데이터 추출
            base_data: Dict[str, Any] = extract_simulation_dashboard_data(simulation_data)
            
            # 6️⃣ 메트릭 수집
            metrics_data = await self.collector.collect_dashboard_metrics(simulation_data)
            
            # 7️⃣ DashboardData 구성
            dashboard_data = DashboardData(
                **base_data,
                resource_usage=metrics_data["resource_usage"],
                pod_status=metrics_data["pod_status"]
            )
            return dashboard_data

        except Exception as e:
            # 8️⃣ fallback 처리
            collector = self.collector
            return DashboardData(
                **extract_simulation_dashboard_data(simulation_data),
                resource_usage=collector._get_default_resource_usage(),
                pod_status=collector._get_default_pod_status()
            )
    

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
        namespace = f"simulation-{simulation_id}"

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
        finally:
            # -----------------------------
            # 모든 Pod 삭제
            # -----------------------------
            try:
                await PodService.delete_all_pods_in_namespace(namespace)
                print(f"🧹 네임스페이스 '{namespace}'의 모든 Pod 삭제 완료")
            except Exception as e:
                print(f"❌ 네임스페이스 '{namespace}' Pod 삭제 중 오류: {e}")
                
    async def _stop_parallel_simulation_via_polling(self, simulation_id: int) -> Dict[str, Any]:
        """
        ⚡ 병렬 시뮬레이션 중지
        """
        print(f"⚡ 병렬 시뮬레이션 중지 (polling 위임): {simulation_id}")
        namespace = f"simulation-{simulation_id}"

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
        finally:
            # -----------------------------
            # 모든 Pod 삭제
            # -----------------------------
            try:
                await PodService.delete_all_pods_in_namespace(namespace)
                print(f"🧹 네임스페이스 '{namespace}'의 모든 Pod 삭제 완료")
            except Exception as e:
                print(f"❌ 네임스페이스 '{namespace}' Pod 삭제 중 오류: {e}")

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
                pod_list = PodService.get_pods_by_filter(
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
                "stoppedAt": datetime.now(timezone.utc)
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
                pod_list = PodService.get_pods_by_filter(
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
                "stoppedAt": datetime.now(timezone.utc)
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
                    is_playing = status.get("is_playing", True)
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
    
    async def get_current_status(self, simulation_id: int) -> CurrentStatus:
        simulation = await self.find_simulation_by_id(simulation_id, "status")
        print(f"시뮬레이션 상태: {simulation.status}")
        
        now = datetime.now(timezone.utc)
        status_str = simulation.status
        
        started_at = simulation.started_at if status_str == SimulationStatus.RUNNING else None
        
        # 공통 Timestamps 
        timestamps = CurrentTimestamps(
            created_at=simulation.created_at,
            started_at=started_at,
            last_updated=now
        )

        if status_str == "INITIATING":
            return CurrentStatus(
                status=status_str,
                timestamps=timestamps,
                message="네임스페이스 및 기본 리소스 생성 중..."
            )
        elif status_str == "PENDING":
            progress = None

            if simulation.pattern_type == PatternType.SEQUENTIAL:
                total_steps = await self.repository.count_simulation_steps(simulation_id)
                
                progress = SequentialProgress(
                    overall_progress=0.0,
                    current_step=0,
                    completed_steps=0,
                    total_steps=total_steps
                )
                
                step_progress_list = await self.repository.get_simulation_step_progress(simulation_id)
                
                # 상태별 스텝 처리
                step_details = []

                for step in step_progress_list:
                    step_status = StepStatus(step["status"])

                    step_detail = StepDetail(
                        step_order=step["step_order"],
                        status=step_status,
                        progress=step["progress_percentage"],
                        started_at=step.get("started_at"),
                        completed_at=step.get("completed_at"),
                        failed_at=step.get("failed_at"),
                        stopped_at=step.get("stopped_at"),
                        autonomous_agents=step.get("autonomous_agents", 0),
                        current_repeat=step.get("current_repeat", 0),
                        total_repeats=step.get("repeat_count", 0),
                        error=step.get("error")
                    )
                    step_details.append(step_detail)
                
            elif simulation.pattern_type == PatternType.PARALLEL:
                total_groups = await self.repository.count_simulation_groups(simulation_id)
                
                progress = ParallelProgress(
                    overall_progress=0.0,
                    completed_groups=0,
                    running_groups=0,
                    total_groups=total_groups
                )
                
                # 그룹별 상세 정보 생성
                group_list = await self.repository.get_simulation_group_progress(simulation_id)
                group_details = []
                for group in group_list:
                    debug_print(f"{group}")
                    group_detail = GroupDetail(
                        group_id=group["group_id"],
                        status=GroupStatus(group["status"]),
                        progress=group.get("progress", 0.0),
                        started_at=group.get("started_at"),
                        completed_at=group.get("completed_at"),
                        failed_at=group.get("failed_at"),
                        stopped_at=group.get("stopped_at"),
                        autonomous_agents=group.get("autonomous_agents", 0),
                        current_repeat=group.get("current_repeat", 0),
                        total_repeats=group.get("total_repeats", 0),
                        error=group.get("error")
                    )
                    group_details.append(group_detail)

            
            if progress is None:
                # 알 수 없는 패턴 타입인 경우 기본값 제공
                debug_print(f"Unknown pattern type: {simulation.pattern_type}")
                progress = SequentialProgress(
                    overall_progress=0.0
                )
            
            return CurrentStatus(
                status=status_str,
                timestamps=timestamps,
                progress=progress,
                step_details=step_details if simulation.pattern_type == PatternType.SEQUENTIAL else None,
                group_details=group_details if simulation.pattern_type == PatternType.PARALLEL else None,
                message="시뮬레이션 시작 준비 완료"
            )
        elif status_str in ["RUNNING", "COMPLETED", "STOPPED", "FAILED"]:
            if simulation.pattern_type == PatternType.SEQUENTIAL:
                return await self.get_sequential_current_status(simulation_id)
            elif simulation.pattern_type == PatternType.PARALLEL:
                return await self.get_parallel_current_status(simulation_id)
                
        else:                    
            # 알 수 없는 상태 처리
            return CurrentStatus(
                status=status_str,
                timestamps=timestamps,
                message="알 수 없는 상태"
            )

    async def get_sequential_current_status(self, simulation_id: int) -> CurrentStatus:
        """
        순차(Sequential) 패턴 시뮬레이션 실행 단위(Execution)의 현재 상태 조회
        - simulation_id로 최신 SimulationExecution 조회
        - Redis 실시간 상태 우선 조회
        - Redis 없으면 DB fallback
        - StepExecution 기반 진행률/반복/상태 정확 반영
        """
        # 1️⃣ 최신 실행 조회
        latest_execution = await self.repository.find_latest_simulation_execution(simulation_id)
        if not latest_execution:
            raise ValueError(f"Simulation {simulation_id}에 실행 기록이 없습니다.")
        
        execution_id = latest_execution.id
        redis_key = f"simulation:{simulation_id}:execution:{execution_id}"
        
        # 2️⃣ Redis 조회
        redis_client = RedisSimulationClient()
        try:
            await redis_client.connect()
            redis_status = await redis_client.client.get(redis_key)
            
            if redis_status:
                redis_status = json.loads(redis_status)
                debug_print(f"📡 Redis에서 시뮬레이션 {simulation_id} 실시간 상태 조회 성공")
                return await self._convert_redis_to_current_status(redis_status)
            else:
                debug_print(f"📡 Redis에 시뮬레이션 {simulation_id} 데이터 없음 - DB fallback")
                
        except Exception as e:
            debug_print(f"❌ Redis 조회 실패: {e} - DB fallback")
        finally:
            if redis_client.client:
                await redis_client.client.close()
        
        # 🗄️ 2차 시도: DB에서 조회 (Redis 실패 시 fallback)
        debug_print(f"🗄️ DB에서 시뮬레이션 {simulation_id} 상태 조회")
        return await self._get_status_from_db(simulation_id)


    async def _convert_redis_to_current_status(self, redis_data: dict) -> CurrentStatus:
        """
        Redis 데이터를 CurrentStatus DTO로 변환
        """
        # 타임스탬프 변환
        timestamps_data = redis_data.get("timestamps", {})
        timestamps = CurrentTimestamps(
            created_at=self._parse_datetime(timestamps_data.get("createdAt")),
            last_updated=self._parse_datetime(timestamps_data.get("lastUpdated")) or datetime.now(timezone.utc),
            started_at=self._parse_datetime(timestamps_data.get("startedAt")),
            completed_at=self._parse_datetime(timestamps_data.get("completedAt")),
            failed_at=self._parse_datetime(timestamps_data.get("failedAt")),
            stopped_at=self._parse_datetime(timestamps_data.get("stoppedAt"))
        )
        
        # 진행률 변환
        progress_data = redis_data.get("progress", {})
        progress = SequentialProgress(
            overall_progress=round(progress_data.get("overallProgress", 0.0) * 100, 1),  # 0.65 -> 65.0
            current_step=progress_data.get("currentStep"),
            completed_steps=progress_data.get("completedSteps", 0),
            total_steps=progress_data.get("totalSteps", 0)
        )
        
        # 스텝 디테일 변환
        step_details = []
        for step_data in redis_data.get("stepDetails", []):
            step_detail = StepDetail(
                step_order=step_data["stepOrder"],
                status=StepStatus(step_data["status"]),
                progress=round(step_data.get("progress", 0.0) * 100, 1),  # 0.65 -> 65.0
                started_at=self._parse_datetime(step_data.get("startedAt")),
                completed_at=self._parse_datetime(step_data.get("completedAt")),
                failed_at=self._parse_datetime(step_data.get("failedAt")),
                stopped_at=self._parse_datetime(step_data.get("stoppedAt")),
                autonomous_agents=step_data.get("autonomousAgents", 0),
                current_repeat=step_data.get("currentRepeat", 0),
                total_repeats=step_data.get("totalRepeats", 0),
                error=step_data.get("error")
            )
            step_details.append(step_detail)
        
        return CurrentStatus(
            status=SimulationStatus(redis_data["status"]),
            progress=progress,
            timestamps=timestamps,
            step_details=step_details,
            message=redis_data.get("message", "상태 정보 없음")
        )


    async def _get_status_from_db(self, execution_id: int) -> CurrentStatus:
        """
        DB에서 시뮬레이션 상태 조회 (Redis fallback)
        - 기존 로직 그대로 유지
        """
        async with self.sessionmaker() as db_session:
            execution = await self.repository.find_execution_by_id(execution_id, db_session)
            if not execution:
                raise ValueError(f"Execution {execution_id} not found")
            
            step_executions = await self.repository.get_step_executions_by_execution(execution_id, db_session)

        timestamps = CurrentTimestamps(
            created_at=execution.created_at,
            started_at=execution.started_at,
            completed_at=execution.completed_at,
            failed_at=execution.failed_at,
            stopped_at=execution.stopped_at,
            last_updated=datetime.now(timezone.utc)
        )

        # 전체 진행률 요약과 스텝별 정보 조회
        total_steps = len(step_executions)
        completed_steps = sum(1 for s in step_executions if s.status == "COMPLETED")
        overall_progress = (completed_steps / total_steps * 100) if total_steps > 0 else 0.0

        # 상태별 스텝 처리
        step_details = []
        current_step_info = None
        status_str = execution.status

        for step in step_executions:
            step_status = StepStatus(step.status)
            
            if status_str == "RUNNING" and step.status == "RUNNING":
                current_step_info = step
            elif status_str == "STOPPED" and step.status in ["RUNNING", "STOPPED"]:
                step_status = StepStatus.STOPPED
                current_step_info = step
            elif status_str == "FAILED" and step.status == "FAILED":
                step_status = StepStatus.FAILED
                current_step_info = step
            elif status_str == "COMPLETED":
                step_status = StepStatus.COMPLETED

            step_detail = StepDetail(
                step_order=step.step_order,
                status=step_status,
                progress=round(step.progress * 100, 1),
                started_at=step.started_at,
                completed_at=step.completed_at,
                failed_at=step.failed_at,
                stopped_at=step.stopped_at,
                autonomous_agents=step.autonomous_agent_count,
                current_repeat=step.current_repeat,
                total_repeats=step.total_repeats,
                error=step.error
            )
            step_details.append(step_detail)

        # 메시지 생성
        if status_str == "RUNNING" and current_step_info:
            message = f"Step {current_step_info['step_order']} 실행 중 ({current_step_info['progress_percentage']:.1f}% 완료)"
            if current_step_info.get("current_repeat", 0) > 0:
                message += f" - {current_step_info['current_repeat']}/{current_step_info['repeat_count']} 반복"
        elif status_str == "COMPLETED":
            message = "모든 스텝 실행 완료"
        elif status_str == "STOPPED":
            message = f"Step {current_step_info['step_order']} 중지됨" if current_step_info else "시뮬레이션 중지됨"
        elif status_str == "FAILED":
            error_msg = current_step_info.get("error") if current_step_info else None
            message = f"Step {current_step_info['step_order']} 실패: {error_msg}" if error_msg else "시뮬레이션 실패"
        else:
            message = "상태 정보 없음"

        # 현재 진행 중 스텝 번호
        current_step_number = current_step_info["step_order"] if current_step_info else 0

        progress = SequentialProgress(
            overall_progress=round(overall_progress, 1),
            current_step=current_step_number,
            completed_steps=completed_steps,
            total_steps=total_steps
        )

        return CurrentStatus(
            status=SimulationStatus(status_str),
            progress=progress,
            timestamps=timestamps,
            step_details=step_details,
            message=message
        )


    def _parse_datetime(self, datetime_str: str | None) -> datetime | None:
        """
        ISO 포맷 문자열을 datetime 객체로 변환
        """
        if not datetime_str:
            return None
        try:
            # ISO 포맷 파싱 (2024-01-01T12:00:00.123456+00:00)
            if datetime_str.endswith('Z'):
                datetime_str = datetime_str[:-1] + '+00:00'
            return datetime.fromisoformat(datetime_str)
        except (ValueError, TypeError) as e:
            debug_print(f"❌ 날짜 파싱 실패: {datetime_str}, 오류: {e}")
            return None

    async def get_parallel_current_status(self, simulation_id: int) -> CurrentStatus:
        """
        병렬(Parallel) 패턴 시뮬레이션 실행 단위(Execution)의 현재 상태 조회
        - simulation_id로 최신 SimulationExecution 조회
        - Redis 실시간 상태 우선 조회
        - Redis 없으면 DB fallback
        - GroupExecution 기반 진행률/반복/상태 반영
        """
        # 1️⃣ 최신 실행 조회
        latest_execution = await self.repository.find_latest_simulation_execution(simulation_id)
        if not latest_execution:
            raise ValueError(f"Simulation {simulation_id}에 실행 기록이 없습니다.")

        execution_id = latest_execution.id
        redis_key = f"simulation:{simulation_id}:execution:{execution_id}"
        
        # 2️⃣ Redis 조회
        redis_client = RedisSimulationClient()
        try:
            await redis_client.connect()
            redis_status = await redis_client.client.get(redis_key)
            
            debug_print(f"Redis 에 저장된 시뮬레이션 정보: {redis_status}")
            
            if redis_status:
                redis_status = json.loads(redis_status)
                debug_print(f"📡 Redis에서 병렬 시뮬레이션 {simulation_id} Execution {execution_id} 상태 조회 성공")
                return await self._convert_redis_to_parallel_status(redis_status)
            else:
                debug_print(f"📡 Redis에 병렬 시뮬레이션 {simulation_id} 데이터 없음 - DB fallback")
                
        except Exception as e:
            debug_print(f"❌ Redis 조회 실패: {e} - DB fallback")
        finally:
            if redis_client.client:
                await redis_client.client.close()
        
        # 🗄️ 2차 시도: DB에서 조회 (Redis 실패 시 fallback)
        debug_print(f"🗄️ DB에서 병렬 시뮬레이션 {simulation_id} 상태 조회")
        return await self._get_parallel_status_from_db(execution_id)


    async def _convert_redis_to_parallel_status(self, redis_data: dict) -> CurrentStatus:
        """
        Redis 데이터를 병렬 시뮬레이션용 CurrentStatus DTO로 변환
        """
        # 타임스탬프 변환
        timestamps_data = redis_data.get("timestamps", {})
        timestamps = CurrentTimestamps(
            created_at=self._parse_datetime(timestamps_data.get("createdAt")),
            last_updated=self._parse_datetime(timestamps_data.get("lastUpdated")) or datetime.now(timezone.utc),
            started_at=self._parse_datetime(timestamps_data.get("startedAt")),
            completed_at=self._parse_datetime(timestamps_data.get("completedAt")),
            failed_at=self._parse_datetime(timestamps_data.get("failedAt")),
            stopped_at=self._parse_datetime(timestamps_data.get("stoppedAt"))
        )
        
        # 병렬 진행률 변환
        progress_data = redis_data.get("progress", {})
        progress = ParallelProgress(
            overall_progress=round(progress_data.get("overallProgress", 0.0) * 100, 1),
            completed_groups=progress_data.get("completedGroups", 0),
            running_groups=progress_data.get("runningGroups", 0),
            total_groups=progress_data.get("totalGroups", 0)
        )
        
        # 그룹 디테일 변환
        group_details = []
        for group_data in redis_data.get("groupDetails", []):
            debug_print(f"Redis 에 저장된 그룹 별 상세정보: {group_data}")
            group_detail = GroupDetail(
                group_id=group_data["groupId"],
                status=GroupStatus(group_data["status"]),
                progress=round(group_data.get("progress", 0.0) * 100, 1),
                started_at=self._parse_datetime(group_data.get("startedAt")),
                completed_at=self._parse_datetime(group_data.get("completedAt")),
                failed_at=self._parse_datetime(group_data.get("failedAt")),
                stopped_at=self._parse_datetime(group_data.get("stoppedAt")),
                autonomous_agents=group_data.get("autonomousAgents", 0),
                current_repeat=group_data.get("currentRepeat", 0),
                total_repeats=group_data.get("totalRepeats", 0),
                error=group_data.get("error")
            )
            group_details.append(group_detail)
        
        return CurrentStatus(
            status=SimulationStatus(redis_data["status"]),
            progress=progress,
            timestamps=timestamps,
            group_details=group_details,
            message=redis_data.get("message", "상태 정보 없음")
        )

    async def _get_parallel_status_from_db(self, execution_id: int) -> CurrentStatus:
        """
        DB에서 병렬 시뮬레이션 실행 상태 조회 (Redis fallback)
        - SimulationExecution, GroupExecution 중심
        """
        async with self.sessionmaker() as db_session:
            execution = await self.repository.find_execution_by_id(execution_id, db_session)
            if not execution:
                raise ValueError(f"Execution {execution_id} not found")

            group_executions = await self.repository.get_group_executions_by_execution(execution_id, db_session)

        timestamps = CurrentTimestamps(
            created_at=execution.created_at,
            started_at=execution.started_at,
            completed_at=execution.completed_at,
            failed_at=execution.failed_at,
            stopped_at=execution.stopped_at,
            last_updated=datetime.now(timezone.utc),
        )

        # 전체 진행률 요약
        total_groups = len(group_executions)
        completed_groups = sum(1 for g in group_executions if g.status == "COMPLETED")
        running_groups = sum(1 for g in group_executions if g.status == "RUNNING")
        overall_progress = (sum(g.progress for g in group_executions) / total_groups * 100) if total_groups > 0 else 0.0

        # 그룹 상세
        group_details = []
        for g in group_executions:
            group_details.append(GroupDetail(
                group_id=g.group_id,
                status=GroupStatus(g.status),
                progress=round(g.progress * 100, 1),
                started_at=g.started_at,
                completed_at=g.completed_at,
                failed_at=g.failed_at,
                stopped_at=g.stopped_at,
                autonomous_agents=g.autonomous_agent_count,
                current_repeat=g.current_repeat,
                total_repeats=g.total_repeats,
                error=g.error,
            ))

        # 메시지
        status_str = execution.status
        if status_str == "RUNNING":
            message = f"{running_groups}개 그룹 병렬 실행 중"
        elif status_str == "COMPLETED":
            message = "모든 그룹 실행 완료"
        elif status_str == "STOPPED":
            message = "실행 중단됨"
        elif status_str == "FAILED":
            failed_group = next((g for g in group_executions if g.status == "FAILED"), None)
            if failed_group:
                message = f"그룹 {failed_group.group_id} 실패: {failed_group.error or '알 수 없는 오류'}"
            else:
                message = "시뮬레이션 실패"
        else:
            message = "상태 정보 없음"

        progress = ParallelProgress(
            overall_progress=round(overall_progress, 1),
            completed_groups=completed_groups,
            running_groups=running_groups,
            total_groups=total_groups,
        )

        return CurrentStatus(
            status=SimulationStatus(status_str),
            progress=progress,
            timestamps=timestamps,
            group_details=group_details,
            message=message,
        )
                                
    async def create_step_or_group(
        self, 
        simulation_data: SimulationContext, 
        step_data: StepContext = None, 
        group_data=None,
        session: Optional[AsyncSession] = None
    ):
        manage_session = False
        if session is None:
            session = self.sessionmaker()
            manage_session = True
            
        async with session if manage_session else contextlib.nullcontext(session):
            # Template 조회
            debug_print("템플릿 조회")
            template = await self.template_repository.find_by_id(
                step_data.template_id, session=session
            )
            if not template:
                raise ValueError(f"Template {step_data.template_id} not found")
            
            if simulation_data.pattern_type == PatternType.SEQUENTIAL:
                if not step_data:
                    raise ValueError("StepContext is required for sequential pattern")
                
                # Step 생성
                debug_print("Step 생성 요청")
                db_step  = await self.repository.create_simulation_step(
                    session=session,
                    simulation_id=simulation_data.id,
                    step_order=step_data.step_order,
                    template_id=step_data.template_id,
                    autonomous_agent_count=step_data.autonomous_agent_count,
                    execution_time=step_data.execution_time,
                    delay_after_completion=step_data.delay_after_completion,
                    repeat_count=step_data.repeat_count,
                )
                debug_print("Step 생성 완료")

                # Instance batch 생성
                step_data.id = db_step.id
                
                debug_print("Instance 생성 요청")
                instances = await self.instance_repository.create_instances_batch(
                    simulation=simulation_data,
                    step=step_data,
                    session=session
                )
                debug_print("Instance 생성 완료")

                return db_step, instances, template
    
            else:
                raise ValueError(f"Unknown pattern type: {simulation_data.pattern_type}")
            
    def build_simulation_config(self, simulation, step):
        return {
            'bag_file_path': step.template.bag_file_path,
            'repeat_count': step.repeat_count,
            'max_execution_time': f"{step.execution_time}s" if step.execution_time else "3600s",
            'communication_port': 11311,
            'data_format': 'ros-bag',
            'debug_mode': False,
            'log_level': 'INFO',
            'delay_after_completion': step.delay_after_completion,
            'simulation_name': simulation.name,
            'pattern_type': simulation.pattern_type,
            'mec_id': simulation.mec_id
        }
