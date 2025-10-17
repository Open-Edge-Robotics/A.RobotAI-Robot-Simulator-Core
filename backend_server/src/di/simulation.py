from typing import Annotated
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from repositories.instance_repository import InstanceRepository
from repositories.template_repository import TemplateRepository
from di.template import get_template_service
from repositories.simulation_repository import SimulationRepository
from crud.simulation import SimulationService, TemplateService
from database.db_conn import get_db, async_session
from state import simulation_state

def get_simulation_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    template_service: Annotated[TemplateService, Depends(get_template_service)]
) -> SimulationService:
    """SimulationService 의존성 주입"""
    repository = SimulationRepository(async_session)
    template_repository = TemplateRepository(async_session)
    instance_repository = InstanceRepository(async_session)
    return SimulationService(db, async_session, repository, template_service, template_repository, instance_repository, simulation_state)
