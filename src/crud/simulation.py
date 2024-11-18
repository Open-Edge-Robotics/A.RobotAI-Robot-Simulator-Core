from http.client import responses

from sqlalchemy import select, exists
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.simulation import Simulation
from src.schemas.simulation import SimulationCreateModel, SimulationListModel, SimulationListResponseModel, \
    SimulationCreateResponseModel


class SimulationService:
    def __init__(self, session: AsyncSession):
        self.session = session


    async def create_simulation(self, simulation_create_data: SimulationCreateModel):
        try:
            # 시뮬레이션 이름 중복 검사
            statement = select(
                exists().
                where(Simulation.name == simulation_create_data.simulationName)
            )
            is_existed = await self.session.scalar(statement)

            if is_existed:
                response = SimulationCreateResponseModel(
                    statusCode="400",
                    data=None,
                    message="시뮬레이션 이름이 이미 존재합니다.",
                )

                return response

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