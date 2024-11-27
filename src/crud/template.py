from fastapi import HTTPException
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

    async def delete_template(self, template_id: int, db: AsyncSession):
        find_template = await self.find_template(template_id, db)

        await db.delete(find_template)
        await db.commit()
        return TemplateDeleteResponse(
            template_id=template_id #TODO: 필드 수정? 엑셀에는 template_id만 있어서 이렇게 적어둠. 반환 필드 추가 시 스키마까지 수정 필.
        ).model_dump()

    async def find_template(self, template_id, db):
        query = select(Template).filter(Template.template_id == template_id)
        template = await db.execute(query)
        find_template = template.scalar_one_or_none()

        if find_template is None:
            raise HTTPException(status_code=404, detail="Template not found")
        return find_template
