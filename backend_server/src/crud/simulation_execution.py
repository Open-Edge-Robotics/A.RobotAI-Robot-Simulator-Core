from datetime import datetime
import json
from typing import List, Optional, Tuple
from models.enums import PatternType
from models.simulation_execution import SimulationExecution, timezone
from database.redis_simulation_client import RedisSimulationClient
from schemas.pagination import PaginationParams
from schemas.simulation_status import CurrentStatus, SequentialProgress, ParallelProgress, CurrentTimestamps
from schemas.simulation_execution import ExecutionItem
from schemas.simulation_detail import ExecutionPlanSequential, ExecutionPlanParallel
from repositories.simulation_execution_repository import SimulationExecutionRepository

class SimulationExecutionService:
    """Execution 조회 서비스"""
    def __init__(self, repository: SimulationExecutionRepository, redis_client: RedisSimulationClient):
        self.repository = repository
        self.redis_client = redis_client
        
    # -----------------------------
    # 단일 Execution 상세 조회
    # -----------------------------
    async def get_execution_detail(
        self,
        simulation_id: int,
        execution_id: int
    ) -> Optional[ExecutionItem]:
        """
        simulation_id + execution_id 기준으로 단일 Execution 상세 정보 조회
        Redis에서 먼저 실행 상태 확인, 없으면 DB result_summary 사용
        """
        result_json: dict = {}
        exe: Optional[SimulationExecution] = None

        # -------------------------------
        # 1️⃣ Redis 우선 조회
        # -------------------------------
        if self.redis_client.client:
            redis_key = f"simulation:{simulation_id}:execution:{execution_id}"
            redis_raw = await self.redis_client.client.get(redis_key)
            if redis_raw:
                try:
                    redis_json = json.loads(redis_raw)
                    result_json = redis_json
                except json.JSONDecodeError:
                    result_json = {}

        # -------------------------------
        # 2️⃣ Redis 데이터 없으면 DB 조회
        # -------------------------------
        if not result_json:
            exe = await self.repository.find_by_id(simulation_id, execution_id)
            if not exe:
                return None
            result_json = exe.result_summary or {}
        else:
            # Redis 데이터가 있으면 exe 객체도 필요 (pattern_type 확인용)
            exe = await self.repository.find_by_id(simulation_id, execution_id)
            if not exe:
                return None

        status = result_json.get("status", "UNKNOWN")
        message = result_json.get("message")
        # execution_plan may be stored in Redis result or DB execution.execution_plan
        raw_plan = result_json.get("executionPlan") or (exe.execution_plan if exe else None)

        # Convert raw_plan into validated DTOs when possible
        execution_plan = None
        try:
            if raw_plan:
                # Detect sequential vs parallel by exe.pattern_type
                if exe.pattern_type == PatternType.SEQUENTIAL or str(exe.pattern_type).lower() == "sequential":
                    # Expect raw_plan to have 'steps' list
                    execution_plan = ExecutionPlanSequential.from_entities(raw_plan.get("steps", [])) if isinstance(raw_plan, dict) else None
                else:
                    execution_plan = ExecutionPlanParallel.from_entities(raw_plan.get("groups", [])) if isinstance(raw_plan, dict) else None
        except Exception:
            # If conversion fails, keep raw_plan as fallback
            execution_plan = raw_plan
        progress_data = result_json.get("progress")
        timestamps_data = result_json.get("timestamps", {})
        step_details = result_json.get("stepDetails")
        group_details = result_json.get("groupDetails")

        # -------------------------------
        # 3️⃣ simulation type에 따라 step/group details 업데이트
        # -------------------------------
        if exe.pattern_type == PatternType.SEQUENTIAL:
            step_details = result_json.get("stepDetails", step_details)
        elif exe.pattern_type == PatternType.PARALLEL:
            group_details = result_json.get("groupDetails", group_details)

        # -------------------------------
        # 4️⃣ Progress 객체 생성
        # -------------------------------
        progress = None
        pattern_type = exe.pattern_type
        if progress_data:
            if pattern_type == PatternType.SEQUENTIAL:
                progress = SequentialProgress(
                    overall_progress=progress_data.get("overallProgress", 0) * 100,
                    current_step=progress_data.get("currentStep"),
                    completed_steps=progress_data.get("completedSteps"),
                    total_steps=progress_data.get("totalSteps")
                )
                # Step 단위 progress도 0~100 변환
                if step_details:
                    for step in step_details:
                        if step.get("progress") is not None:
                            step["progress"] = step["progress"] * 100
                            
            elif pattern_type == PatternType.PARALLEL:
                progress = ParallelProgress(
                    overall_progress=progress_data.get("overallProgress", 0) * 100,
                    completed_groups=progress_data.get("completedGroups"),
                    running_groups=progress_data.get("runningGroups"),
                    total_groups=progress_data.get("totalGroups")
                )
                
                # Group 단위 progress도 0~100 변환
                if group_details:
                    for group in group_details:
                        if group.get("progress") is not None:
                            group["progress"] = group["progress"] * 100

        # -------------------------------
        # 5️⃣ Timestamps 객체 생성
        # -------------------------------
        timestamps = CurrentTimestamps(
            created_at=timestamps_data.get("createdAt"),
            last_updated=timestamps_data.get("lastUpdated"),
            started_at=timestamps_data.get("startedAt"),
            completed_at=timestamps_data.get("completedAt"),
            failed_at=timestamps_data.get("failedAt"),
            stopped_at=timestamps_data.get("stoppedAt"),
        )

        # -------------------------------
        # 6️⃣ CurrentStatus 생성
        # -------------------------------
        current_status = CurrentStatus(
            status=status,
            progress=progress,
            timestamps=timestamps,
            message=message,
            step_details=step_details,
            group_details=group_details
        )

        # -------------------------------
        # 7️⃣ ExecutionItem 반환
        # -------------------------------
        return ExecutionItem(
            execution_id=exe.id,
            simulation_id=exe.simulation_id,
            pattern_type=pattern_type,
            execution_plan=execution_plan,
            current_status=current_status
        )

    async def list_executions(
        self,
        simulation_id: int,
        pagination: PaginationParams
    ) -> Tuple[List[ExecutionItem], int]:
        """ExecutionList 조회 + PaginationMeta 생성용 total_count 반환"""

        # 1️⃣ 총 개수 조회
        total_count = await self.repository.count_by_simulation_id(simulation_id)

        # 2️⃣ 페이징 조회
        execution_models = await self.repository.find_all_with_pagination(simulation_id, pagination)

        executions: List[ExecutionItem] = []

        # 3️⃣ 각 Execution 처리
        for exe in execution_models:
            status = exe.status.value
            message = exe.message
            result_json = exe.result_summary or {}
            progress_data = result_json.get("progress")

            # If the execution is RUNNING, always prefer Redis for live progress
            if status == "RUNNING" and self.redis_client and getattr(self.redis_client, 'client', None):
                try:
                    redis_key = f"simulation:{simulation_id}:execution:{exe.id}"
                    redis_raw = await self.redis_client.client.get(redis_key)
                    if redis_raw:
                        try:
                            redis_json = json.loads(redis_raw)
                            # override progress and message from Redis if present
                            progress_data = redis_json.get("progress") or progress_data
                            message = redis_json.get("message", message)
                            # Also allow step/group details to be taken from Redis when building CurrentStatus
                            result_json = redis_json
                        except json.JSONDecodeError:
                            pass
                except Exception:
                    # Redis issues should not break listing; fallback to DB values
                    pass

            # If no progress in result_summary, build a sensible default from execution_plan
            if not progress_data:
                plan = exe.execution_plan or {}
                try:
                    # PatternType may be Enum or str; handle both
                    is_seq = exe.pattern_type == PatternType.SEQUENTIAL or str(exe.pattern_type).lower() == "sequential"
                    is_par = exe.pattern_type == PatternType.PARALLEL or str(exe.pattern_type).lower() == "parallel"
                except Exception:
                    is_seq = str(exe.pattern_type).lower() == "sequential"
                    is_par = str(exe.pattern_type).lower() == "parallel"

                if is_seq:
                    total_steps = len(plan.get("steps", [])) if isinstance(plan, dict) else 0
                    progress_data = {
                        "overallProgress": 0.0,
                        "currentStep": 0,
                        "completedSteps": 0,
                        "totalSteps": total_steps,
                    }
                elif is_par:
                    total_groups = len(plan.get("groups", [])) if isinstance(plan, dict) else 0
                    progress_data = {
                        "overallProgress": 0.0,
                        "completedGroups": 0,
                        "runningGroups": total_groups,
                        "totalGroups": total_groups,
                    }

            # 3-1️⃣ Timestamps 가져오기 (DB + Redis 통합)
            timestamps = await self._get_timestamps(exe, simulation_id, status)

            # 3-2️⃣ Progress 객체 생성
            progress = self._get_progress(progress_data, exe.pattern_type)

            # 3-3️⃣ CurrentStatus 생성
            current_status = CurrentStatus(
                status=status,
                progress=progress,
                timestamps=timestamps,
                message=message
            )

            # 3-4️⃣ ExecutionItem 추가
            executions.append(
                ExecutionItem(
                    execution_id=exe.id,
                    simulation_id=exe.simulation_id,
                    pattern_type=exe.pattern_type,
                    current_status=current_status
                )
            )

        return executions, total_count


    # ================= Helper Functions =================

    async def _get_timestamps(self, exe, simulation_id: int, status: str) -> CurrentTimestamps:
        """DB + Redis 통합 timestamps 가져오기"""
        timestamps = CurrentTimestamps(
            created_at=exe.created_at,
            last_updated=exe.updated_at or datetime.now(timezone.utc),
            started_at=exe.started_at,
            completed_at=exe.completed_at,
            failed_at=exe.failed_at,
            stopped_at=exe.stopped_at,
        )

        if status == "RUNNING":
            redis_key = f"simulation:{simulation_id}:execution:{exe.id}"
            redis_data_raw = await self.redis_client.client.get(redis_key)
            if redis_data_raw:
                try:
                    redis_data = json.loads(redis_data_raw)
                    redis_ts = redis_data.get("timestamps", {})
                    timestamps = CurrentTimestamps(
                        created_at=exe.created_at,
                        last_updated=redis_ts.get("lastUpdated") or exe.updated_at or datetime.now(timezone.utc),
                        started_at=redis_ts.get("startedAt") or exe.started_at,
                        completed_at=redis_ts.get("completedAt") or exe.completed_at,
                        failed_at=redis_ts.get("failedAt") or exe.failed_at,
                        stopped_at=redis_ts.get("stoppedAt") or exe.stopped_at,
                    )
                except json.JSONDecodeError:
                    pass

        return timestamps


    def _get_progress(self, progress_data: dict, pattern_type: str):
        """Progress 객체 생성"""
        if not progress_data:
            return None

        if pattern_type == "sequential":
            return SequentialProgress(
                overall_progress=progress_data.get("overallProgress", 0) * 100,
                current_step=progress_data.get("currentStep"),
                completed_steps=progress_data.get("completedSteps"),
                total_steps=progress_data.get("totalSteps")
            )
        elif pattern_type == "parallel":
            return ParallelProgress(
                overall_progress=progress_data.get("overallProgress", 0) * 100,
                completed_groups=progress_data.get("completedGroups"),
                running_groups=progress_data.get("runningGroups"),
                total_groups=progress_data.get("totalGroups")
            )
        return None
