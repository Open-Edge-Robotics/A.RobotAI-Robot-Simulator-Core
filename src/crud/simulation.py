from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.simulation import Simulation
from src.schemas.simulation import SimulationCreateModel, SimulationListModel, SimulationListResponseModel, \
    SimulationCreateResponseModel


class SimulationService:
    def __init__(self, session: AsyncSession):
        self.session = session


    async def create_simulation(self, simulation_create_data: SimulationCreateModel):
        try:
            new_simulation = Simulation(
                name=simulation_create_data.simulationName,
                description=simulation_create_data.simulationDescription
            )

            self.session.add(new_simulation)
            await self.session.commit()
            await self.session.refresh(new_simulation)

            response = SimulationCreateResponseModel(
                statusCode="201",
                data=None,
                message="시뮬레이션 생성 성공"
            )

        except Exception as e:
            await self.session.rollback()

            response = SimulationCreateResponseModel(
                statusCode="500",
                data=None,
                message="시뮬레이션 생성 실패: " + str(e)
            )

        return response


    async def get_all_simulations(self):
        try:
            statement = (
                select(Simulation).
                order_by(Simulation.id.desc())
            )
            results = await self.session.scalars(statement)

            simulation_list = [
                SimulationListModel(
                    simulationId=str(simulation.id),
                    simulationName=simulation.name,
                    simulationDescription=simulation.description,
                    simulationCreatedAt=str(simulation.created_at),
                    simulationStatus="RUNNING" # TODO: status 데이터 가져오기
                )
                for simulation in results.all()
            ]

            response = SimulationListResponseModel(
                statusCode="200",
                data=simulation_list,
                message="시뮬레이션 목록 조회 성공"
            )

        except Exception as e:

            response = SimulationListResponseModel(
                statusCode="500",
                data=None,
                message="시뮬레이션 목록 조회 실패: " + str(e)
            )

        return response