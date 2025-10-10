import traceback
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query
from starlette import status
from typing import Optional

from di.simulation import get_simulation_service
from schemas.simulation_status import CurrentStatus, SimulationStatusResponse
from schemas.simulation_status import CurrentStatus, SimulationDeletionStatusData, SimulationDeletionStatusResponse, SimulationStatusResponse
from models.enums import ViewType
from schemas.dashboard import SimulationDashboardResponseModel
from schemas.simulation_detail import SimulationResponseModel
from crud.simulation import SimulationService
from schemas.simulation import *
from utils.my_enum import API

router = APIRouter(prefix="/simulation", tags=["Simulation"])

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
            data=detail_data.model_dump(by_alias=True),
            message=f"{simulation_id}번 시뮬레이션 상세정보 조회 성공"
        )

@router.get(
    "/{simulation_id}/status",
    response_model=SimulationStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="시뮬레이션 상태 조회",
    description="""
    특정 시뮬레이션의 **현재 상태**를 조회합니다.
    실시간 진행 상황 확인용으로 5초 단위 폴링에 최적화되어 있습니다.

    **주요 특징:**
    - 현재 상태, 진행률, step/group 세부 정보 포함
    - 실행 패턴에 따라 Sequential 또는 Parallel 구조 반환
    - COMPLETED, FAILED, STOPPED 상태에 따른 추가 정보 제공

    **사용 예시:**
    - 실시간 대시보드 모니터링
    - 시뮬레이션 진행 상황 확인
    - 오류 발생 시 상세 정보 확인
    """,
    operation_id="getSimulationCurrentStatus"
)
async def get_simulation_status(
    simulation_id: int,
    service: SimulationService = Depends(get_simulation_service)
):
    simulation = await service.find_simulation_by_id(simulation_id, "status")
    # 현재 상태 조회
    current_status: CurrentStatus = await service.get_current_status(simulation_id)
    
    # 응답 DTO 변환
    response = SimulationStatusResponse(
        status_code = 200,
        message=f"{simulation_id}번 시뮬레이션 상태 조회 성공",
        data={
            "simulationId": simulation_id,
            "patternType": simulation.pattern_type,
            "currentStatus": current_status.model_dump()
        }
    )
    
    return response

@router.get(
    "",
    response_model=SimulationListResponse,
    status_code=status.HTTP_200_OK,
    summary="시뮬레이션 목록 조회",
    description="""
        시뮬레이션 목록을 조회합니다.

        - 다양한 필터링 지원:
        - `pattern_type`: 특정 패턴 타입 기준 필터링 (선택)
        - `status`: 시뮬레이션 상태값 기준 필터링 (선택)
        - `start_date`, `end_date`: 조회 기간 기준 필터링 (선택, YYYY-MM-DD)
        - 페이징 지원:
        - PaginationParams 상속으로 `page`, `size` 파라미터 사용 가능
        - 반환값: SimulationListResponse
    """
)
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

@router.post(
    "/{simulation_id}/start",
    response_model=SimulationControlResponseModel,
    status_code=status.HTTP_200_OK,
    summary="시뮬레이션 시작",
    description="""
        해당 시뮬레이션을 시작합니다.

        - UI에서 '시작' 버튼 클릭 시 호출됩니다.
        - simulation_id를 경로 파라미터로 전달해야 합니다.
        - 호출 성공 시 시뮬레이션 실행 상태가 'RUNNING'으로 변경됩니다.
        - 반환값: SimulationControlResponseModel
    """
)
async def start_simulation(
    simulation_id: int = Path(..., description = "실행할 시뮬레이션 ID"),
    service: SimulationService = Depends(get_simulation_service)
):
    """시뮬레이션 실행"""
    result = await service.start_simulation_async(simulation_id)
    message = API.RUN_SIMULATION.value

    return SimulationControlResponseModel(
        status_code=status.HTTP_200_OK,
        data=result,
        message=message
    )

@router.delete(
    "/{simulation_id}", 
    response_model=SimulationDeleteResponseModel, 
    status_code=status.HTTP_202_ACCEPTED,
    summary="시뮬레이션 삭제 요청",
    description=(
        "특정 시뮬레이션 삭제 요청을 접수합니다.\n"
        "- 삭제 단계: namespace → Redis → DB\n"
        "- 요청 즉시 202 Accepted 반환\n"
        "- 진행 상태는 monitor_key를 통해 확인 가능"
    ),
)
async def delete_simulation(
    background_tasks: BackgroundTasks,
    simulation_id: int, service: SimulationService = Depends(get_simulation_service)
):
    """시뮬레이션 삭제 요청 접수"""
    api = API.DELETE_SIMULATION.value
    simulation = await service.find_simulation_by_id(simulation_id, api)
    
    allowed_statuses = [SimulationStatus.PENDING, SimulationStatus.COMPLETED, SimulationStatus.FAILED, SimulationStatus.STOPPED]
    
    if simulation.status not in allowed_statuses:
        raise HTTPException(status_code=400, detail="삭제 불가 상태")
    
    # ✅ 상태를 먼저 DELETING 으로 전환
    await service.repository.update_simulation_status(simulation_id, SimulationStatus.DELETING)
    
    print(f"Delete request received for simulation {simulation_id}")
    # 백그라운드 작업 등록
    background_tasks.add_task(service.delete_simulation, simulation_id)

    return SimulationDeleteResponseModel(
        status_code=status.HTTP_202_ACCEPTED,
        data= {"simulation_id": simulation_id, "status": SimulationStatus.DELETING },
        message="시뮬레이션 삭제 요청 접수됨."
    )
    
@router.get(
    "/{simulation_id}/deletion", 
    response_model=SimulationDeletionStatusResponse, 
    status_code=status.HTTP_200_OK,
    summary="시뮬레이션 삭제 진행 상태 조회",
    description=(
        "삭제 요청 후 특정 시뮬레이션의 삭제 진행 상태를 조회합니다.\n"
        "- 단계별 상태: namespace, Redis, DB\n"
        "- 상태: PENDING, RUNNING, COMPLETED, FAILED\n"
        "- 진행률(progress), 시작/완료 시간, 오류 메시지 포함\n"
        "- 프론트엔드에서 polling 방식으로 UI 업데이트 가능"
    ),
)
async def get_delete_status(simulation_id: int, service: SimulationService = Depends(get_simulation_service)):
    """삭제 진행 상태 조회"""
    deletion_status = await service.get_deletion_status(simulation_id)
    if not deletion_status:
        raise HTTPException(status_code=404, detail="삭제 상태 정보 없음")
    
    steps = deletion_status.get("steps", {})
    completed_steps = sum(1 for v in steps.values() if v == "COMPLETED")
    total_steps = len(steps)
    progress = int((completed_steps / total_steps) * 100) if total_steps else 0

    overall_status = (
        "FAILED" if "FAILED" in steps.values() else
        "PENDING" if "PENDING" in steps.values() else
        "COMPLETED"
    )
    
    return SimulationDeletionStatusResponse(
        data=SimulationDeletionStatusData(
            simulation_id=simulation_id,
            status=overall_status,
            progress=progress,
            steps=steps,
            started_at=deletion_status.get("started_at"),
            completed_at=deletion_status.get("completed_at"),
            error_message=deletion_status.get("error_message")
        ).model_dump(),
        message="시뮬레이션 삭제 진행 상태 조회 성공",
        statusCode="200"
    )