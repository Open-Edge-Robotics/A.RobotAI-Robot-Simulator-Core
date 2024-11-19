from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.crud.template import TemplateService
from src.database.connection import get_db
from src.schemas.template import TemplateResponse, TemplateDeleteResponse, TemplateCreate

router = APIRouter(prefix="/template", tags=["Template"])
template_service = TemplateService()


# 템플릿 목록 조회
@router.get("", response_model=List[TemplateResponse], status_code=status.HTTP_200_OK)
async def read_template(db: AsyncSession = Depends(get_db)):
    return await template_service.read_all(db)

# 템플릿 생성
@router.post("", status_code=status.HTTP_201_CREATED)
async def create_template(template: TemplateCreate, db: AsyncSession = Depends(get_db)):
    return await template_service.create(template, db)

# 템플릿 삭제
@router.delete("/{template_id}", response_model=TemplateDeleteResponse, status_code=status.HTTP_200_OK)
async def delete_template(template_id: int, db: AsyncSession = Depends(get_db)):
    return await template_service.delete(template_id, db)