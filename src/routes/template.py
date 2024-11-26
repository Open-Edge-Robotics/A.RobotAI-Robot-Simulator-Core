from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.crud.template import TemplateService
from src.database.db_conn import get_db
from src.schemas.format import GlobalResponseModel
from src.schemas.template import TemplateCreateRequest, TemplateDeleteResponse

router = APIRouter(prefix="/template", tags=["Template"])
template_service = TemplateService()


# 템플릿 목록 조회
@router.get("", response_model=GlobalResponseModel)
async def get_templates(db: AsyncSession = Depends(get_db)):
    template_responses = await template_service.get_all_templates(db)
    return GlobalResponseModel(
        status_code=status.HTTP_200_OK,
        data=template_responses,
        message="템플릿 목록 조회"
    )


# 템플릿 생성
@router.post("", status_code=status.HTTP_201_CREATED)
async def create_template(template: TemplateCreateRequest, db: AsyncSession = Depends(get_db)):
    return await template_service.create_template(template, db)


# 템플릿 삭제
@router.delete("/{template_id}", response_model=TemplateDeleteResponse, status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: int, db: AsyncSession = Depends(get_db)):
    return await template_service.delete_template(template_id, db)
