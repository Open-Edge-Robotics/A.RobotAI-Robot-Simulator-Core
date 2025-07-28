from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from crud.simulation import SimulationService
from database.db_conn import get_db
from schemas.simulation import *
from utils.my_enum import API

router = APIRouter(prefix="/simulation", tags=["Simulation"])


@router.post("", response_model=SimulationCreateResponseModel, status_code=status.HTTP_201_CREATED)
async def create_simulation(
        simulation_create_data: SimulationCreateRequest, session: AsyncSession = Depends(get_db)
):
    """새로운 시뮬레이션 생성 (고도화된 패턴 설정 포함)"""
    new_simulation = await SimulationService(session).create_simulation(simulation_create_data)

    return SimulationCreateResponseModel(
        status_code=status.HTTP_201_CREATED,
        data=new_simulation,
        message=API.CREATE_SIMULATION.value
    )


@router.get("", response_model=SimulationListResponseModel, status_code=status.HTTP_200_OK)
async def get_simulations(
        session: AsyncSession = Depends(get_db)
):
    """시뮬레이션 목록 조회 (패턴 설정 정보 포함)"""
    simulation_list = await SimulationService(session).get_all_simulations()

    return SimulationListResponseModel(
        status_code=status.HTTP_200_OK,
        data=simulation_list,
        message=API.GET_SIMULATIONS.value
    )


@router.put("/{simulation_id}/pattern", response_model=SimulationPatternUpdateResponseModel,
            status_code=status.HTTP_200_OK)
async def update_simulation_pattern(
        simulation_id: int,
        pattern_data: SimulationPatternUpdateRequest,
        session: AsyncSession = Depends(get_db)
):
    """시뮬레이션 패턴 설정 업데이트"""
    result = await SimulationService(session).update_simulation_pattern(simulation_id, pattern_data)

    return SimulationPatternUpdateResponseModel(
        status_code=status.HTTP_200_OK,
        data=result,
        message="UPDATE_SIMULATION_PATTERN"
    )


@router.get("/{simulation_id}/status")
async def get_simulation_detailed_status(
        simulation_id: int,
        session: AsyncSession = Depends(get_db)
):
    """시뮬레이션 상세 상태 조회"""
    detailed_status = await SimulationService(session).get_simulation_detailed_status(simulation_id)

    return {
        "status_code": status.HTTP_200_OK,
        "data": detailed_status,
        "message": "GET_SIMULATION_DETAILED_STATUS"
    }


@router.post("/action", response_model=SimulationControlResponseModel, status_code=status.HTTP_200_OK)
async def control_simulation(
        request: SimulationControlRequest, session: AsyncSession = Depends(get_db)
):
    """시뮬레이션 실행/중지 (고도화된 기능 포함)"""

    if request.action == "start":
        result = await SimulationService(session).start_simulation(request.simulation_id)
        message = API.RUN_SIMULATION.value
    elif request.action == "stop":
        result = await SimulationService(session).stop_simulation(request.simulation_id)
        message = API.STOP_SIMULATION.value
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f'{API.STOP_SIMULATION.value}: action 요청 값을 확인해주세요')

    return SimulationControlResponseModel(
        status_code=status.HTTP_200_OK,
        data=result,
        message=message
    )


@router.delete("/{simulation_id}", response_model=SimulationDeleteResponseModel, status_code=status.HTTP_200_OK)
async def delete_simulation(
        simulation_id: int, session: AsyncSession = Depends(get_db)
):
    """시뮬레이션 삭제"""
    data = await SimulationService(session).delete_simulation(simulation_id)

    return SimulationDeleteResponseModel(
        status_code=status.HTTP_200_OK,
        data=data,
        message=API.DELETE_SIMULATION.value
    )