from fastapi import APIRouter, Depends, Path, Query, HTTPException

from crud.simulation_execution import SimulationExecutionService
from di.simulation_execution import get_simulation_execution_service
from schemas.pagination import PaginationMeta, PaginationParams
from schemas.simulation_execution import ExecutionDetailResponse, ExecutionDetailResponseFactory, ExecutionItem, ExecutionListResponse, ExecutionListResponseFactory


router = APIRouter(
    prefix="/simulation/{simulation_id}/execution",
    tags=["Simulation Execution History"],
)

@router.get(
    "", 
    response_model=ExecutionListResponse,
    summary="시뮬레이션 실행 히스토리 조회",
    description=(
        "지정한 시뮬레이션 ID에 해당하는 실행 히스토리 목록을 조회합니다. "
        "각 Execution의 패턴별 진행 상태와 진행률, 타임스탬프 정보를 포함합니다. "
        "페이지네이션을 지원하며, 생성일 기준 최신 순으로 정렬됩니다."
    ),    
)
async def list_simulation_executions(
    simulation_id: int = Path(..., description = "조회할 시뮬레이션 ID"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    service: SimulationExecutionService = Depends(get_simulation_execution_service)
):
    pagination = PaginationParams(page=page, size=size)

    executions, total_items = await service.list_executions(simulation_id, pagination)

    pagination_meta = PaginationMeta.create(page, size, total_items)

    return ExecutionListResponseFactory.create(
        executions=executions,
        pagination=pagination_meta,
        status_code=200
    )
    
@router.get(
    "/{execution_id}",
    response_model=ExecutionDetailResponse,
    summary="단일 시뮬레이션 실행 상세 조회",
    description=(
        "지정한 시뮬레이션 ID와 실행 ID에 해당하는 Execution 상세 정보를 조회합니다. "
        "RUNNING 상태인 경우 Redis에서 최신 progress와 timestamps를 반영합니다. "
        "stepDetails 또는 groupDetails 중 하나는 반드시 존재해야 합니다."
    ),
)
async def get_simulation_execution_detail(
    simulation_id: int = Path(..., description="조회할 시뮬레이션 ID"),
    execution_id: int = Path(..., description="조회할 Execution ID"),
    service: SimulationExecutionService = Depends(get_simulation_execution_service)
):
    # 1️⃣ 단일 Execution 조회
    execution_detail: ExecutionItem = await service.get_execution_detail(simulation_id, execution_id)
    
    if not execution_detail:
        raise HTTPException(status_code=404, detail="해당 execution_id에 해당하는 실행 기록을 찾을 수 없습니다.")
    
    # 3️⃣ Factory를 통해 Response 생성
    response = ExecutionDetailResponseFactory.create(execution=execution_detail, status_code=200)

    return response