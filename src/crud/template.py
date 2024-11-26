from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.template import Template
from src.schemas.template import TemplateListResponse, TemplateCreateRequest, TemplateCreateResponse, \
    TemplateDeleteResponse


class TemplateService:
    async def get_all_templates(self, db: AsyncSession):
        selected_template = await db.execute(select(Template))
        templates = selected_template.scalars().all()

        return [
            TemplateListResponse(
                template_id=template.template_id,
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

        return TemplateCreateResponse.model_validate(new_template).model_dump()

    # async def delete_template(self, template_id: int, db: AsyncSession):
    #     # TODO: 로직 수정 필요 (응답은 X)
    #     find_template = await self.read(template_id, db)
    #
    #     await db.delete(find_template)
    #     await db.commit()
    #
    #     return TemplateDeleteResponse(
    #         template_id=template_id
    #     )
