from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession


from src.crud.instance import InstanceService
from src.database.connection import get_db
from src.schemas.instance import InstanceCreateModel, InstanceGetModel

instance_router = APIRouter(prefix="/api/instance", tags=["instance"])

@instance_router.post("/")
async def create_instance(
        instance_create_data: InstanceCreateModel, session: AsyncSession = Depends(get_db)
):
    """새로운 인스턴스 생성"""
    new_instance = await InstanceService(session).create_instance(instance_create_data)

    return new_instance


@instance_router.get("/", response_model="")
async def get_instances(
    instance_data : InstanceGetModel, session: AsyncSession = Depends(get_db)
):
    """인스턴스 목록 조회"""
    return instance_data

@instance_router.get("/{instance_id}", response_model="")
async def get_instance(
        instance_data : InstanceGetModel, session: AsyncSession = Depends(get_db)
):
    """인스턴스 상세 조회"""
    return instance_data

@instance_router.delete("/{instance_id}", response_model="")
async def delete_instance(
        instance_data : InstanceGetModel, session: AsyncSession = Depends(get_db)
):
    """인스턴스 삭제"""
    return instance_data