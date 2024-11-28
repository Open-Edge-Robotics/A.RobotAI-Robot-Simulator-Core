from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

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
        find_template = await self.find_template_by_id(template_id, db)

        await db.delete(find_template)
        await db.commit()
        return TemplateDeleteResponse(
            template_id=template_id #TODO: 필드 수정? 엑셀에는 template_id만 있어서 이렇게 적어둠. 반환 필드 추가 시 스키마까지 수정 필.
        ).model_dump()

    async def find_template_by_id(self, template_id, api: str, db):
        try:
            query = select(Template).where(Template.template_id == template_id)
            result = await db.execute(query)
            template = result.scalar_one_or_none()

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f'{api} 실패 : 데이터베이스 조회 중 오류가 발생했습니다. : {str(e)}')

        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'{api} 실패: 존재하지 않는 템플릿id 입니다.')
        return template
