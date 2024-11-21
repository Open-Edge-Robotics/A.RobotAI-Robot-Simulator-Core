from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.crud.instance import InstanceService
from src.database.connection import get_db
from src.schemas.format import GlobalResponseModel
from src.schemas.instance import InstanceCreateModel, InstanceCreateResponseModel

router = APIRouter(prefix="/instance", tags=["Instance"])

@router.post("/", response_model=GlobalResponseModel, status_code=status.HTTP_201_CREATED)
async def create_instance(
        instance_create_data: InstanceCreateModel, session: AsyncSession = Depends(get_db)
):
    """새로운 인스턴스 생성"""
    data = await InstanceService(session).create(instance_create_data)

    return GlobalResponseModel(
        status_code=status.HTTP_201_CREATED,
        data=data,
        message="인스턴스 생성 성공"
    )


@router.get("/", response_model="")
async def get_instances(
    session: AsyncSession = Depends(get_db)
):
    """인스턴스 목록 조회"""
    return await InstanceService(session).get_all_instances()

@router.get("/{instance_id}", response_model="")
async def get_instance(
        session: AsyncSession = Depends(get_db)
):
    """인스턴스 상세 조회"""
    return None

@router.delete("/{instance_id}", response_model="")
async def delete_instance(
        session: AsyncSession = Depends(get_db)
):
    """인스턴스 삭제"""
    return None