from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DatabaseError
from starlette import status

from src.models.instance import Instance, InstanceSet
from src.models.simulation import Simulation
from src.models.template import Template
from src.schemas.instance import InstanceCreateModel, InstanceCreateResponse


class InstanceService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, instance_create_data: InstanceCreateModel):
        # TODO: extract 할 수 있지 않을까? (시뮬id 검사, 템플릿id 검사)
        # 시뮬레이션 id 검사
        statement = select(Simulation).where(Simulation.id == instance_create_data.simulation_id)
        simulation = await self.session.scalar(statement)

        if simulation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='존재하지 않는 시뮬레이션id 입니다.')

        # 템플릿 id 검사
        statement = select(Template).where(Template.template_id == instance_create_data.template_id)
        template = await self.session.scalar(statement)

        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='존재하지 않는 템플릿id 입니다.')

        try:
            new_instance = Instance(
                name=instance_create_data.instance_name,
                description=instance_create_data.instance_description,
                template_id=instance_create_data.template_id,
                template=template,
            )
            self.session.add(new_instance)
            await self.session.commit()
            await self.session.refresh(new_instance)

            new_instances_sets = InstanceSet(
                    instance=new_instance,
                    instance_id=new_instance.id,
                    instance_count=instance_create_data.instance_count,
                    simulation_id=instance_create_data.simulation_id,
                    simulation= simulation
            )
            self.session.add(new_instances_sets)
            await self.session.commit()

            await self.session.refresh(new_instance)

        except DatabaseError as e:
            await self.session.rollback()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='데이터 저장 중 오류가 발생했습니다.: ' + str(e))

        return InstanceCreateResponse(
            instance_id= new_instance.id,
            instance_name= new_instance.name,
            instance_description= new_instance.description
        ).model_dump()

    async def get_all_instances(self):
        pass