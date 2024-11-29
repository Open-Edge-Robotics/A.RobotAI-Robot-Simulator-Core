from fastapi import HTTPException
from sqlalchemy import select, exists
from sqlalchemy.exc import DatabaseError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.models.instance import Instance
from src.models.simulation import Simulation
from src.schemas.simulation import SimulationCreateRequest, SimulationListResponse, SimulationCreateResponse, \
    SimulationControlRequest, SimulationDeleteResponse, SimulationControlResponse


class SimulationService:
    def __init__(self, session: AsyncSession):
        self.session = session


    async def create_simulation(self, simulation_create_data: SimulationCreateRequest):
        try:
            # 시뮬레이션 이름 중복 검사
            statement = select(
                exists().
                where(Simulation.name == simulation_create_data.simulation_name)
            )
            is_existed = await self.session.scalar(statement)

            if is_existed:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail="시뮬레이션 이름이 이미 존재합니다.")

            new_simulation = Simulation(
                name=simulation_create_data.simulation_name,
                description=simulation_create_data.simulation_description
            )

            self.session.add(new_simulation)
            await self.session.commit()
            await self.session.refresh(new_simulation)

        except DatabaseError as e:
            await self.session.rollback()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail='데이터 저장 중 오류가 발생했습니다.: ' + str(e))

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
                SimulationListResponse(
                    simulation_id=simulation.id,
                    simulation_name=simulation.name,
                    simulation_description=simulation.description,
                    simulation_created_at=str(simulation.created_at),
                    simulation_status="RUNNING" # TODO: status 데이터 가져오기
                )
                for simulation in results.all()
            ]

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail='시뮬레이션 목록 조회 실패: ' + str(e))

        return simulation_list

    async def control_simulation(self, simulation_control_data: SimulationControlRequest):
        # 추후 연동 시 로직 추가
        simulation_id = simulation_control_data.simulation_id
        action = simulation_control_data.action

        return SimulationControlResponse(
            simulation_id = simulation_id
        ).model_dump(), action

    async def delete_simulation(self, simulation_id: int):
        simulation = await self.find_simulation_by_id(simulation_id, "시뮬레이션 삭제")

        # 시뮬레이션이 존재해야 아래 코드 실행됨
        try:
            statement = select(
                exists().
                where(Instance.simulation_id == simulation_id)
            )
            is_existed = await self.session.scalar(statement)

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail=f'시뮬레이션 삭제 실패 : 데이터베이스 오류가 발생했습니다. : {str(e)}')

        if is_existed is False:
            await self.session.delete(simulation)
            await self.session.commit()
        else:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail='시뮬레이션 삭제 실패: 삭제하려는 시뮬레이션에 속한 인스턴스가 있어 시뮬레이션 삭제가 불가합니다.')

        return SimulationDeleteResponse(
            simulation_id=simulation_id
        ).model_dump()

    async def find_simulation_by_id(self, simulation_id: int, api: str):
        try:
            query = select(Simulation).where(Simulation.id == simulation_id)
            result = await self.session.execute(query)
            simulation = result.scalar_one_or_none()

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail=f'{api} 실패 : 데이터베이스 조회 중 오류가 발생했습니다. : {str(e)}')

        if simulation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=f'{api} 실패: 존재하지 않는 시뮬레이션id 입니다.')
        return simulation
