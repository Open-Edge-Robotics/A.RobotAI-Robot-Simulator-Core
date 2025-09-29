from typing import Annotated
from fastapi import Depends

from crud.simulation_pattern import SimulationPatternService
from database.db_conn import AsyncSession, async_sessionmaker, get_async_sessionmaker
from repositories.template_repository import TemplateRepository
from repositories.instance_repository import InstanceRepository
from repositories.simulation_repository import SimulationRepository

async def get_simulation_pattern_service(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_async_sessionmaker)]
) -> SimulationPatternService:
    simulation_repository = SimulationRepository(session_factory)
    instance_repository = InstanceRepository(session_factory)
    template_repository = TemplateRepository(session_factory)
    return SimulationPatternService(
        sessionmaker=session_factory,
        simulation_repository=simulation_repository,
        instance_repository=instance_repository,
        template_repository=template_repository
    )