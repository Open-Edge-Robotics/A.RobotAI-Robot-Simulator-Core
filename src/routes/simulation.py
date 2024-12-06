from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.crud.simulation import SimulationService
from src.database.db_conn import get_db
from src.schemas.simulation import SimulationCreateRequest, SimulationListResponseModel, SimulationCreateResponseModel, \
    SimulationControlRequest, SimulationControlResponseModel, SimulationDeleteResponseModel, SimulationControlResponse
from src.utils.my_enum import API

router = APIRouter(prefix="/simulation", tags=["Simulation"])


@router.post("", response_model=SimulationCreateResponseModel, status_code=status.HTTP_201_CREATED)
async def create_simulation(
        simulation_create_data: SimulationCreateRequest, session: AsyncSession = Depends(get_db)
):
    """새로운 시뮬레이션 생성"""
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
    """시뮬레이션 목록 조회"""
    simulation_list = await SimulationService(session).get_all_simulations()

    return SimulationListResponseModel(
        status_code=status.HTTP_200_OK,
        data=simulation_list,
        message=API.GET_SIMULATIONS.value
    )


@router.post("/action", response_model=SimulationControlResponseModel, status_code=status.HTTP_200_OK)
async def control_simulation(
        request: SimulationControlRequest, session: AsyncSession = Depends(get_db)
):
    """시뮬레이션 실행/중지"""

    if request.action == "start":
        result = await SimulationService(session).control_simulation(request.simulation_id)
        message = API.RUN_SIMULATION.value
    elif request.action == "stop":
        # TODO: 추후 개발 시 수정
        result = SimulationControlResponse(status="STOP").model_dump()
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
