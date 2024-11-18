from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from src.models.simulation import Simulation
from src.schemas.simulation import SimulationCreateModel


class SimulationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_simulation(self, simulation_create_data: SimulationCreateModel):
        try:
            new_simulation = Simulation(
                name=simulation_create_data.name,
                description=simulation_create_data.description
            )

            self.session.add(new_simulation)
            await self.session.commit()
            await self.session.refresh(new_simulation)

        except IntegrityError as e:
            await self.session.rollback()
            raise e

        return new_simulation