from fastapi import HTTPException
from sqlalchemy import select, exists
from sqlalchemy.exc import DatabaseError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.models.simulation import Simulation
from src.schemas.simulation import SimulationCreateModel, SimulationListModel, SimulationCreateResponse


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
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="시뮬레이션 이름이 이미 존재합니다.")

            new_simulation = Simulation(
                name=simulation_create_data.simulation_name,
                description=simulation_create_data.simulation_description
            )

            self.session.add(new_simulation)
            await self.session.commit()
            await self.session.refresh(new_simulation)

        except DatabaseError as e:
            await self.session.rollback()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='데이터 저장 중 오류가 발생했습니다.: ' + str(e))

        return SimulationCreateResponse(
            simulation_id=new_simulation.id,
            simulation_name=new_simulation.name,
            simulation_description=new_simulation.description
        ).model_dump()


    async def get_all_simulations(self):
        try:
            statement = (
                select(Simulation).
                order_by(Simulation.id.desc())
            )
            results = await self.session.scalars(statement)

            simulation_list = [
                SimulationListModel(
                    simulation_id=simulation.id,
                    simulation_name=simulation.name,
                    simulation_description=simulation.description,
                    simulation_created_at=str(simulation.created_at),
                    simulation_status="RUNNING" # TODO: status 데이터 가져오기
                )
                for simulation in results.all()
            ]

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='시뮬레이션 목록 조회 실패: ' + str(e))

        return simulation_list