import traceback
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from models.enums import ViewType
from schemas.dashboard import SimulationDashboardResponseModel
from repositories.simulation_repository import SimulationRepository
from schemas.simulation_detail import SimulationResponseModel
from crud.simulation import SimulationService
from database.db_conn import get_db, async_session
from schemas.simulation import *
from utils.my_enum import API
from state import simulation_state

router = APIRouter(prefix="/simulation", tags=["Simulation"])

def get_simulation_service(db: AsyncSession = Depends(get_db)) -> SimulationService:
    """SimulationService 의존성 주입"""
    repository = SimulationRepository(async_session)
    return SimulationService(db, async_session, repository, simulation_state)


@router.post(
    "", 
    response_model=SimulationCreateResponseModel, 
    status_code=status.HTTP_201_CREATED,
    summary="새로운 시뮬레이션 생성",
    description="시뮬레이션 생성 요청을 처리하고, 백그라운드에서 시뮬레이션을 시작합니다."
)
async def create_simulation(
    simulation_create_data: SimulationCreateRequest,
    background_tasks: BackgroundTasks, 
    service: SimulationService = Depends(get_simulation_service)
):
    """새로운 시뮬레이션 생성 (고도화된 패턴 설정 포함)"""
    new_simulation = await service.create_simulation(simulation_create_data, background_tasks)

    return SimulationCreateResponseModel(
        status_code=status.HTTP_201_CREATED,
        data=new_simulation.model_dump(),
        message=API.CREATE_SIMULATION.value
    )
    
@router.get(
    "/summary",
    response_model=SimulationSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="시뮬레이션 요약 목록 조회",
    description="""
    드롭다운 메뉴에서 사용할 시뮬레이션의 ID와 이름 목록을 조회합니다.
    
    **주요 특징:**
    - 모든 시뮬레이션의 ID와 이름만 반환하여 성능 최적화
    - 최신 생성순으로 정렬하여 반환
    - 드롭다운 메뉴, 선택 리스트 등 UI 컴포넌트에서 활용
    
    **사용 예시:**
    - 대시보드 시뮬레이션 선택 드롭다운
    - 시뮬레이션 비교 화면에서 선택 옵션
    - 리포트 생성 시 대상 시뮬레이션 선택
    """,
    operation_id="getSimulationSummaryList"
)
async def get_simulations_summary_list(
    service: SimulationService = Depends(get_simulation_service)
):
    """시뮬레이션 요약 목록 조회"""
    try:
        print("시뮬레이션 요약 목록 조회 요청")
        simulation_summary_list = await service.get_simulation_summary_list()
        
        print(f"시뮬레이션 요약 목록 조회 완료: {len(simulation_summary_list)}")
        return SimulationSummaryResponse(
            status_code=status.HTTP_200_OK,
            data=simulation_summary_list,
            message=f"시뮬레이션 요약 목록 조회 성공"
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="서버 오류가 발생했습니다."
        )
    
@router.get(
    "/{simulation_id}", 
    response_model=SimulationResponseModel,
    status_code=status.HTTP_200_OK,
    summary="시뮬레이션 기본정보 조회",
    description="지정한 시뮬레이션 ID에 해당하는 시뮬레이션의 기본정보를 조회합니다. "
                "패턴별 ExecutionPlan과 현재 상태 정보를 포함합니다.",
)
async def get_simulation(
    simulation_id: int = Path(..., description = "조회할 시뮬레이션 ID"),
    view: Optional[ViewType] = Query(ViewType.DETAIL, description="응답 뷰 타입"),
    service: SimulationService = Depends(get_simulation_service)
):
    if view == ViewType.DASHBOARD:
        dashboard_data = await service.get_dashboard_data(simulation_id)
        return SimulationDashboardResponseModel(
            statusCode="200",
            data=dashboard_data.model_dump(by_alias=True),
            message="시뮬레이션 대시보드 정보 조회 성공"
        )
    else:
        detail_data = await service.get_simulation(simulation_id)
        return SimulationResponseModel(
            statusCode="200",
            data=detail_data,
            message=f"{simulation_id}번 시뮬레이션 상세정보 조회 성공"
        )

@router.get("", response_model=SimulationListResponse, status_code=status.HTTP_200_OK)
async def get_simulations(
    filter_request: SimulationFilterRequest = Depends(),
    service: SimulationService = Depends(get_simulation_service)
):
    """시뮬레이션 목록 조회 (페이지네이션)"""
    try:
        # Service에서 비즈니스 로직 처리
        simulation_items, pagination_meta = await service.get_simulations_with_pagination(
            pagination=filter_request,
            pattern_type=filter_request.pattern_type,
            status=filter_request.status
        )
        overview_data = await service.get_simulation_overview()
        
        return SimulationListResponseFactory.create(
            simulations=simulation_items,
            overview_data=overview_data,
            pagination_meta=pagination_meta
        )
    except ValueError as e:
        print(f"[ValueError] {e}")
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"[Exception] {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="시뮬레이션 목록 조회 중 오류가 발생했습니다")

@router.put("/{simulation_id}/pattern", response_model=SimulationPatternUpdateResponseModel,
            status_code=status.HTTP_200_OK)
async def update_simulation_pattern(
        simulation_id: int,
        pattern_data: SimulationPatternUpdateRequest,
        service: SimulationService = Depends(get_simulation_service)
):
    """시뮬레이션 패턴 설정 업데이트"""
    result = await service.update_simulation_pattern(simulation_id, pattern_data)

    return SimulationPatternUpdateResponseModel(
        status_code=status.HTTP_200_OK,
        data=result,
        message="UPDATE_SIMULATION_PATTERN"
    )


@router.get("/{simulation_id}/status")
async def get_simulation_detailed_status(
        simulation_id: int,
        service: SimulationService = Depends(get_simulation_service)
):
    """시뮬레이션 상세 상태 조회"""
    detailed_status = await service.get_simulation_detailed_status(simulation_id)

    return {
        "status_code": status.HTTP_200_OK,
        "data": detailed_status,
        "message": "GET_SIMULATION_DETAILED_STATUS"
    }


@router.post("/action", response_model=SimulationControlResponseModel, status_code=status.HTTP_200_OK)
async def control_simulation(
        request: SimulationControlRequest, service: SimulationService = Depends(get_simulation_service)
):
    """시뮬레이션 실행/중지 (고도화된 기능 포함)"""

    if request.action == "start":
        result = await service.start_simulation_async(request.simulation_id)
        message = API.RUN_SIMULATION.value
    elif request.action == "stop": # 정지
        result = await service.stop_simulation_async(request.simulation_id)
        message = API.STOP_SIMULATION.value
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"지원하지 않는 시뮬레이션 액션: '{request.action}'. action 값은 'start' 또는 'stop'만 허용됩니다.")

    return SimulationControlResponseModel(
        status_code=status.HTTP_200_OK,
        data=result,
        message=message
    )


@router.delete("/{simulation_id}", response_model=SimulationDeleteResponseModel, status_code=status.HTTP_200_OK)
async def delete_simulation(
        simulation_id: int, service: SimulationService = Depends(get_simulation_service)
):
    """시뮬레이션 삭제"""
    data = await service.delete_simulation(simulation_id)

    return SimulationDeleteResponseModel(
        status_code=status.HTTP_200_OK,
        data=data,
        message=API.DELETE_SIMULATION.value
    )