from sqlalchemy import select, exists
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

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
                where(Simulation.name == simulation_create_data.simulation_name)
            )
            is_existed = await self.session.scalar(statement)

            if is_existed:
                response = SimulationCreateResponseModel(
                    status_code=status.HTTP_409_CONFLICT,
                    data=None,
                    message="시뮬레이션 이름이 이미 존재합니다.",
                )

                return response

            new_simulation = Simulation(
                name=simulation_create_data.simulation_name,
                description=simulation_create_data.simulation_description
            )

            self.session.add(new_simulation)
            await self.session.commit()
            await self.session.refresh(new_simulation)

            response = SimulationCreateResponseModel(
                status_code=status.HTTP_201_CREATED,
                data=None,
                message="시뮬레이션 생성 성공"
            )

        except Exception as e:
            await self.session.rollback()

            response = SimulationCreateResponseModel(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
                    simulation_id=str(simulation.id),
                    simulation_name=simulation.name,
                    simulation_description=simulation.description,
                    simulation_created_at=str(simulation.created_at),
                    simulation_status="RUNNING" # TODO: status 데이터 가져오기
                )
                for simulation in results.all()
            ]

            response = SimulationListResponseModel(
                status_code="200",
                data=simulation_list,
                message="시뮬레이션 목록 조회 성공"
            )

        except Exception as e:

            response = SimulationListResponseModel(
                status_code="500",
                data=None,
                message="시뮬레이션 목록 조회 실패: " + str(e)
            )

        return response