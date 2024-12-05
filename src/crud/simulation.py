from fastapi import HTTPException
from sqlalchemy import select, exists
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette import status

from src.crud.pod import PodService
from src.models.instance import Instance
from src.models.simulation import Simulation
from src.schemas.simulation import SimulationCreateRequest, SimulationListResponse, SimulationCreateResponse, \
    SimulationControlRequest, SimulationDeleteResponse, SimulationControlResponse
from src.utils.my_enum import SimulationStatus, PodStatus, API

pod_service = PodService()


class SimulationService:
    def __init__(self, session: AsyncSession):
        self.session = session


    async def create_simulation(self, simulation_create_data: SimulationCreateRequest):
        # 시뮬레이션 이름 중복 검사
        statement = select(
            exists().
            where(Simulation.name == simulation_create_data.simulation_name)
        )
        is_existed = await self.session.scalar(statement)

        if is_existed:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail=f"{API.CREATE_SIMULATION.value}: 시뮬레이션 이름이 이미 존재합니다.")

        new_simulation = Simulation(
            name=simulation_create_data.simulation_name,
            description=simulation_create_data.simulation_description
        )

        self.session.add(new_simulation)
        await self.session.commit()
        await self.session.refresh(new_simulation)

        simulation_namespace = await pod_service.create_namespace(new_simulation.id)
        new_simulation.namespace = simulation_namespace
        await self.session.commit()

        return SimulationCreateResponse(
            simulation_id=new_simulation.id,
            simulation_name=new_simulation.name,
            simulation_description=new_simulation.description,
            simulation_namespace=new_simulation.namespace
        ).model_dump()


    async def get_all_simulations(self):
        statement = (
            select(Simulation).
            options(selectinload(Simulation.instance)).
            order_by(Simulation.id.desc())
        )
        results = await self.session.execute(statement)
        simulations = results.scalars().all()
        simulation_list =[]

        for simulation in simulations:
            simulation_status = await self.get_simulation_status(simulation)

            response = SimulationListResponse(
                simulation_id=simulation.id,
                simulation_name=simulation.name,
                simulation_description=simulation.description,
                simulation_created_at=str(simulation.created_at),
                simulation_status=simulation_status
            )
            simulation_list.append(response)

        return simulation_list


    async def control_simulation(self, simulation_control_data: SimulationControlRequest):
        # 추후 연동 시 로직 추가
        simulation_id = simulation_control_data.simulation_id
        action = simulation_control_data.action

        return SimulationControlResponse(
            simulation_id = simulation_id
        ).model_dump(), action


    async def delete_simulation(self, simulation_id: int):
        api = API.DELETE_SIMULATION.value
        simulation = await self.find_simulation_by_id(simulation_id, api)

        # 시뮬레이션이 존재해야 아래 코드 실행됨
        statement = select(
            exists().
            where(Instance.simulation_id == simulation_id)
        )
        is_existed = await self.session.scalar(statement)

        if is_existed is False:
            await self.session.delete(simulation)
            await self.session.commit()
        else:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f'{api}: 삭제하려는 시뮬레이션에 속한 인스턴스가 있어 시뮬레이션 삭제가 불가합니다.')

        return SimulationDeleteResponse(
            simulation_id=simulation_id
        ).model_dump()


    async def find_simulation_by_id(self, simulation_id: int, api: str):
        query = select(Simulation).where(Simulation.id == simulation_id)
        result = await self.session.execute(query)
        simulation = result.scalar_one_or_none()

        if simulation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=f'{api}: 존재하지 않는 시뮬레이션id 입니다.')
        return simulation


    async def get_simulation_status(self, simulation):
        instances = simulation.instance

        if not instances:
            return SimulationStatus.EMPTY.value

        for instance in instances:
            pod_status = await pod_service.get_pod_status(instance.pod_name, instance.pod_namespace)
            if pod_status != PodStatus.RUNNING.value:
                return SimulationStatus.INACTIVE.value

        return SimulationStatus.ACTIVE.value
