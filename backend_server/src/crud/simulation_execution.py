import json
from typing import List, Optional, Tuple
from models.enums import PatternType
from models.simulation_execution import SimulationExecution
from database.redis_simulation_client import RedisSimulationClient
from schemas.pagination import PaginationParams
from schemas.simulation_status import CurrentStatus, SequentialProgress, ParallelProgress, CurrentTimestamps
from schemas.simulation_execution import ExecutionItem
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
            current_status=current_status
        )

    
    async def list_executions(
        self,
        simulation_id: int,
        pagination: PaginationParams
    ) -> Tuple[List[ExecutionItem], int]:
        """ExecutionList 조회 + PaginationMeta 생성용 total_count 반환"""
        # 총 개수
        total_count = await self.repository.count_by_simulation_id(simulation_id)

        # 조회
        execution_models = await self.repository.find_all_with_pagination(simulation_id, pagination)

        executions: List[ExecutionItem] = []

        for exe in execution_models:
            result_json = exe.result_summary or {}
            print("DEBUG: result_summary =", result_json)

            status = result_json.get("status", "UNKNOWN")
            message = result_json.get("message")
            progress_data = result_json.get("progress")
            timestamps_data = result_json.get("timestamps", {})
            
            
            # 실행 중인 경우 Redis에서 최신 progress/timestamps 가져오기
            if status == "RUNNING":
                redis_key = f"simulation:{simulation_id}:execution:{exe.id}"
                redis_data_raw = await self.redis_client.client.get(redis_key)
                if redis_data_raw:
                    try:
                        redis_data = json.loads(redis_data_raw)
                        progress_data = redis_data.get("progress", progress_data)
                        timestamps_data = redis_data.get("timestamps", timestamps_data)
                    except json.JSONDecodeError:
                        # Redis 데이터가 깨졌거나 비정상일 경우 DB 데이터 사용
                        pass

            # Progress 선택
            progress = None
            pattern_type = exe.pattern_type
            if progress_data:
                if pattern_type == "sequential":
                    progress = SequentialProgress(
                        overall_progress=progress_data.get("overallProgress", 0),
                        current_step=progress_data.get("currentStep"),
                        completed_steps=progress_data.get("completedSteps"),
                        total_steps=progress_data.get("totalSteps")
                    )
                elif pattern_type == "parallel":
                    progress = ParallelProgress(
                        overall_progress=progress_data.get("overallProgress", 0),
                        completed_groups=progress_data.get("completedGroups"),
                        running_groups=progress_data.get("runningGroups"),
                        total_groups=progress_data.get("totalGroups")
                    )

            timestamps = CurrentTimestamps(
                created_at=timestamps_data.get("createdAt"),
                last_updated=timestamps_data.get("lastUpdated"),
                started_at=timestamps_data.get("startedAt"),
                completed_at=timestamps_data.get("completedAt"),
                failed_at=timestamps_data.get("failedAt"),
                stopped_at=timestamps_data.get("stoppedAt"),
            )

            current_status = CurrentStatus(
                status=status,
                progress=progress,
                timestamps=timestamps,
                message=message
            )

            executions.append(
                ExecutionItem(
                    execution_id=exe.id,
                    simulation_id=exe.simulation_id,
                    pattern_type=pattern_type,
                    current_status=current_status
                )
            )

        return executions, total_count

