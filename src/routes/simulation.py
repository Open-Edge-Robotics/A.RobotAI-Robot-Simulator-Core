from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession


from src.crud.simulation import SimulationService
from src.database.connection import get_db
from src.schemas.simulation import SimulationCreateModel, SimulationListResponseModel, SimulationCreateResponseModel

simulation_router = APIRouter(prefix="/api/simulation", tags=["simulation"])

@simulation_router.post("/", response_model= SimulationCreateResponseModel, status_code=201)
async def create_simulation(
        simulation_create_data: SimulationCreateModel, session: AsyncSession = Depends(get_db)
):
    """새로운 시뮬레이션 생성"""
    new_simulation = await SimulationService(session).create_simulation(simulation_create_data)

    return new_simulation


@simulation_router.get("/", response_model= SimulationListResponseModel, status_code=200)
async def get_all_simulations(
    session: AsyncSession = Depends(get_db)
):
    """시뮬레이션 목록 조회"""
    simulation_list = await SimulationService(session).get_all_simulations()

    return simulation_list
