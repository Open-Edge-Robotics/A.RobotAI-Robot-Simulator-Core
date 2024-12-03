from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.models.template import Template
from src.schemas.template import TemplateListResponse, TemplateCreateRequest, TemplateCreateResponse, \
    TemplateDeleteResponse
from src.utils.my_enum import API


class TemplateService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_all_templates(self):
        selected_template = await self.db.execute(select(Template))
        templates = selected_template.scalars().all()

        return [
            TemplateListResponse(
                template_id=str(template.template_id),
                template_type=template.type,
                template_description=template.description,
            ) for template in templates
        ]

    async def create_template(self, template: TemplateCreateRequest):
        new_template = Template(
            type=template.type,
            description=template.description,
            bag_file_path=template.bag_file_path,
            topics=template.topics,
        )
        self.db.add(new_template)
        await self.db.commit()
        await self.db.refresh(new_template)

        return TemplateCreateResponse.model_validate(new_template).model_dump()

    async def delete_template(self, template_id: int):
        find_template = await self.find_template_by_id(template_id, API.DELETE_TEMPLATE.value)

        await self.db.delete(find_template)
        await self.db.commit()
        return TemplateDeleteResponse(
            template_id=find_template.template_id
        ).model_dump()

    async def find_template_by_id(self, template_id: int, api: str):
        query = select(Template).where(Template.template_id == template_id)
        result = await self.db.execute(query)
        template = result.scalar_one_or_none()

        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'{api}: 존재하지 않는 템플릿id 입니다.')
        return template
