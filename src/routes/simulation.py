from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.crud.simulation import SimulationService
from src.database.connection import get_db
from src.schemas.simulation import SimulationCreateRequest, SimulationListResponseModel, SimulationCreateResponseModel

router = APIRouter(prefix="/simulation", tags=["Simulation"])

@router.post("/", response_model= SimulationCreateResponseModel, status_code=status.HTTP_201_CREATED)
async def create_simulation(
        simulation_create_data: SimulationCreateRequest, session: AsyncSession = Depends(get_db)
):
    """새로운 시뮬레이션 생성"""
    new_simulation = await SimulationService(session).create_simulation(simulation_create_data)

    return SimulationCreateResponseModel(
        status_code=status.HTTP_201_CREATED,
        data=new_simulation,
        message="시뮬레이션 생성 성공"
    )


@router.get("/", response_model= SimulationListResponseModel, status_code=status.HTTP_200_OK)
async def get_all_simulations(
    session: AsyncSession = Depends(get_db)
):
    """시뮬레이션 목록 조회"""
    simulation_list = await SimulationService(session).get_all_simulations()

    return SimulationListResponseModel(
        status_code=status.HTTP_200_OK,
        data=simulation_list,
        message="시뮬레이션 목록 조회 성공"
    )
