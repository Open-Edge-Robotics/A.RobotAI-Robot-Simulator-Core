from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.crud.instance import InstanceService
from src.database.db_conn import get_db
from src.schemas.instance import InstanceCreateRequest, InstanceCreateResponseModel, InstanceListResponseModel, \
    InstanceControlRequest, InstanceControlResponseModel, InstanceDeleteResponseModel, InstanceDetailResponseModel

router = APIRouter(prefix="/instance", tags=["Instance"])


# instance_service = InstanceService() TODO 변경 필요

@router.post("", response_model=InstanceCreateResponseModel, status_code=status.HTTP_201_CREATED)
async def create_instance(
        instance_create_data: InstanceCreateRequest, session: AsyncSession = Depends(get_db)
):
    """새로운 인스턴스 생성"""
    new_instance = await InstanceService(session).create_instance(instance_create_data)
    # await InstanceService(session).create_pod(1, instance_create_data)

    return InstanceCreateResponseModel(
        status_code=status.HTTP_201_CREATED,
        data=new_instance,
        message="인스턴스 생성 성공"
    )


@router.get("", response_model=InstanceListResponseModel, status_code=status.HTTP_200_OK)
async def get_instances(
        simulation_id: Optional[int] = None, session: AsyncSession = Depends(get_db)
):
    """
    인스턴스 전체 목록 조회

    시뮬레이션id별 목록 조회도 가능
    """
    instance_list = await InstanceService(session).get_all_instances(simulation_id)

    return InstanceListResponseModel(
        status_code=status.HTTP_200_OK,
        data=instance_list,
        message="인스턴스 목록 조회 성공"
    )


@router.get("/{instance_id}", response_model=InstanceDetailResponseModel, status_code=status.HTTP_200_OK)
async def get_instance(
        instance_id: int, session: AsyncSession = Depends(get_db)
):
    """인스턴스 상세 조회"""
    instance_detail = await InstanceService(session).get_instance(instance_id)

    return InstanceDetailResponseModel(
        status_code=status.HTTP_200_OK,
        data=instance_detail,
        message="인스턴스 상세 조회 성공"
    )


@router.post("/action", response_model=InstanceControlResponseModel, status_code=status.HTTP_200_OK)
async def control_instance(
        instance_control_data: InstanceControlRequest, session: AsyncSession = Depends(get_db)
):
    """인스턴스 실행/중지"""
    data, action = await InstanceService(session).control_instance(instance_control_data)

    return InstanceControlResponseModel(
        status_code=status.HTTP_200_OK,
        data=data,
        message=f"인스턴스 {action} 성공"
    )


@router.delete("/{instance_id}", response_model=InstanceDeleteResponseModel, status_code=status.HTTP_200_OK)
async def delete_instance(
        instance_id: int, session: AsyncSession = Depends(get_db)
):
    """인스턴스 삭제"""
    data = await InstanceService(session).delete_instance(instance_id)

    return InstanceDeleteResponseModel(
        status_code=status.HTTP_200_OK,
        data=data,
        message="인스턴스 삭제 성공"
    )
