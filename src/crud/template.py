from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.template import Template
from src.schemas.template import TemplateListResponse, TemplateCreateRequest


class TemplateService:
    async def get_all_templates(self, db: AsyncSession):
        selected_template = await db.execute(select(Template))
        templates = selected_template.scalars().all()

        return [
            TemplateListResponse(
                template_id=str(template.template_id),
                template_type=template.type,
                template_description=template.description,
            ) for template in templates
        ]

    async def create_template(self, template: TemplateCreateRequest, db: AsyncSession):
        new_template = Template(
            type=template.type,
            description=template.description,
            bag_file_path=template.bag_file_path,
            topics=template.topics,
        )
        db.add(new_template)
        await db.commit()
        await db.refresh(new_template)
        return new_template

    # async def delete(self, template_id: int, db: AsyncSession):
    #     find_template = await self.read(template_id, db)
    #
    #     await db.delete(find_template)
    #     await db.commit()
    #     return find_template
