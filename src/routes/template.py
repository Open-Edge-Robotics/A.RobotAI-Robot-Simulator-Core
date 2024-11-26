from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.crud.template import TemplateService
from src.database.connection import get_db
from src.schemas.template import TemplateCreateRequest, TemplateCreateResponseModel, \
    TemplateListResponseModel, TemplateDeleteResponseModel

router = APIRouter(prefix="/template", tags=["Template"])
template_service = TemplateService()

# 템플릿 목록 조회
@router.get("", response_model=TemplateListResponseModel, status_code=status.HTTP_200_OK)
async def get_templates(db: AsyncSession = Depends(get_db)):
    template_responses = await template_service.get_all_templates(db)
    return TemplateListResponseModel(
        status_code=status.HTTP_200_OK,
        data=template_responses,
        message="템플릿 목록 조회"
    )

# 템플릿 생성
@router.post("", response_model=TemplateCreateResponseModel, status_code=status.HTTP_201_CREATED)
async def create_template(template: TemplateCreateRequest, db: AsyncSession = Depends(get_db)):
    new_template =  await template_service.create_template(template, db)
    return TemplateCreateResponseModel(
        status_code=status.HTTP_201_CREATED,
        data=new_template,
        message="템플릿 생성 성공"
    )

# 템플릿 삭제
@router.delete("/{template_id}", response_model=TemplateDeleteResponseModel, status_code=status.HTTP_200_OK)
async def delete_template(template_id: int, db: AsyncSession = Depends(get_db)):
    data= await template_service.delete_template(template_id, db)
    return TemplateDeleteResponseModel(
        status_code=status.HTTP_200_OK,
        data=data,
        message="템플릿 삭제 성공"
    )