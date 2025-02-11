from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from backend_server.src.crud.instance import InstanceService
from backend_server.src.database.db_conn import get_db
from backend_server.src.schemas.instance import *
from backend_server.src.utils.my_enum import API

router = APIRouter(prefix="/instance", tags=["Instance"])


@router.post("", response_model=InstanceCreateResponseModel, status_code=status.HTTP_201_CREATED)
async def create_instance(
        instance_create_data: InstanceCreateRequest, session: AsyncSession = Depends(get_db)
):
    """새로운 인스턴스 생성"""
    new_instance = await InstanceService(session).create_instance(instance_create_data)

    return InstanceCreateResponseModel(
        status_code=status.HTTP_201_CREATED,
        data=new_instance,
        message=API.CREATE_INSTANCE.value
    )


@router.get("", response_model=InstanceListResponseModel, status_code=status.HTTP_200_OK)
async def get_instances(
        simulation_id: Optional[int] = Query(None, alias="simulationId"),
        session: AsyncSession = Depends(get_db)
):
    """
    인스턴스 전체 목록 조회

    시뮬레이션id별 목록 조회도 가능
    """
    instance_list = await InstanceService(session).get_all_instances(simulation_id)

    return InstanceListResponseModel(
        status_code=status.HTTP_200_OK,
        data=instance_list,
        message=API.GET_INSTANCES.value
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
        message=API.GET_INSTANCE.value
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
        message=API.DELETE_INSTANCE.value
    )


@router.post("/status", response_model=InstanceStatusResponseModel, status_code=status.HTTP_200_OK)
async def check_instance(request: InstanceStatusRequest, session: AsyncSession = Depends(get_db)):
    """인스턴스 실행 상태 조회"""
    data = await InstanceService(session).check_instance_status(request.instance_ids)
    return InstanceStatusResponseModel(
        status_code=status.HTTP_200_OK,
        data=data,
        message=API.CHECK_INSTANCE.value
    )


@router.post("/action", response_model=InstanceControlResponseModel, status_code=status.HTTP_200_OK)
async def run_instance(
        request: InstanceControlRequest, session: AsyncSession = Depends(get_db)
):
    """인스턴스 실행/중지"""

    if request.action == "start":
        result = await InstanceService(session).start_instances(request.instance_ids)
        message = API.RUN_INSTANCE.value
    elif request.action == "stop":
        result = await InstanceService(session).stop_instances(request.instance_ids)
        message = API.STOP_INSTANCE.value
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f'{API.CONTROL_INSTANCE.value}: action 요청 값을 확인해주세요')

    return InstanceControlResponseModel(
        status_code=status.HTTP_200_OK,
        data=result,
        message=message
    )
