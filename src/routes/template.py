from fastapi import APIRouter
from fastapi.params import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.crud.template import TemplateService
from src.database.db_conn import get_db
from src.schemas.template import TemplateCreateRequest, TemplateCreateResponseModel, \
    TemplateListResponseModel, TemplateDeleteResponseModel
from src.utils.my_enum import API

router = APIRouter(prefix="/template", tags=["Template"])


# 템플릿 목록 조회
@router.get("", response_model=TemplateListResponseModel, status_code=status.HTTP_200_OK)
async def get_templates(db: AsyncSession = Depends(get_db)):
    template_responses = await TemplateService(db).get_all_templates()
    return TemplateListResponseModel(
        status_code=status.HTTP_200_OK,
        data=template_responses,
        message=API.GET_TEMPLATES.value
    )


# 템플릿 생성
@router.post("", response_model=TemplateCreateResponseModel, status_code=status.HTTP_201_CREATED)
async def create_template(template: TemplateCreateRequest, db: AsyncSession = Depends(get_db)):
    new_template = await TemplateService(db).create_template(template)
    return TemplateCreateResponseModel(
        status_code=status.HTTP_201_CREATED,
        data=new_template,
        message=API.CREATE_TEMPLATE.value
    )


# 템플릿 삭제
@router.delete("/{template_id}", response_model=TemplateDeleteResponseModel, status_code=status.HTTP_200_OK)
async def delete_template(template_id: int, db: AsyncSession = Depends(get_db)):
    data = await TemplateService(db).delete_template(template_id)
    return TemplateDeleteResponseModel(
        status_code=status.HTTP_200_OK,
        data=data,
        message=API.DELETE_TEMPLATE.value
    )
