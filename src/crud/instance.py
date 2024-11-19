from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from src.models.instance import Instance
from src.models.instance import TemplateSet
from src.schemas.instance import InstanceCreateModel

class InstanceService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_instance(self, instance_create_data: InstanceCreateModel):
        try:
            new_instance = Instance(
                name=instance_create_data.name,
                description=instance_create_data.description
            )

            self.session.add(new_instance)
            await self.session.commit()
            await self.session.refresh(new_instance)

            template_sets = [
                TemplateSet(
                    template_count=template_count,
                    instance=new_instance
                )
                for template_id, template_count in instance_create_data.templateSet.items()
            ]

            self.session.add_all(template_sets)
            await self.session.commit()

            await self.session.refresh(new_instance)

        except IntegrityError as e:
            await self.session.rollback()
            raise e

        return new_instance