from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession


from src.crud.simulation import SimulationService
from src.database.connection import get_db
from src.schemas.simulation import SimulationCreateModel, SimulationListModel

simulation_router = APIRouter(prefix="/api/simulation", tags=["simulation"])

@simulation_router.post("/")
async def create_simulation(
        simulation_create_data: SimulationCreateModel, session: AsyncSession = Depends(get_db)
):
    """새로운 시뮬레이션 생성"""
    new_simulation = await SimulationService(session).create_simulation(simulation_create_data)

    return new_simulation


@simulation_router.get("/", response_model= List[SimulationListModel])
async def get_all_simulations(
    session: AsyncSession = Depends(get_db)
):
    """시뮬레이션 목록 조회"""
    simulation_list = await SimulationService(session).get_all_simulations()

    return simulation_list
