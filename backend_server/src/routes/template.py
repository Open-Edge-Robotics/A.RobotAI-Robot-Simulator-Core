from fastapi import APIRouter, File, Form, UploadFile
from fastapi.params import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from storage.minio_client import MinioStorageClient
from crud.template import TemplateService, get_template_service
from database.db_conn import get_db
from database.minio_conn import get_storage_client
from schemas.template import TemplateCreateRequest, TemplateCreateResponseModel, \
    TemplateListResponseModel, TemplateDeleteResponseModel
from utils.my_enum import API

router = APIRouter(prefix="/template", tags=["Template"])

# 템플릿 목록 조회
@router.get("", response_model=TemplateListResponseModel, status_code=status.HTTP_200_OK)
async def get_templates(
    service: TemplateService = Depends(get_template_service)  
):
    template_responses = await service.get_all_templates()
    return TemplateListResponseModel(
        status_code=status.HTTP_200_OK,
        data=template_responses,
        message=API.GET_TEMPLATES.value
    )


# 템플릿 생성
@router.post(
    "", 
    response_model=TemplateCreateResponseModel, 
    status_code=status.HTTP_201_CREATED,
    summary="새로운 ROSBAG 템플릿 생성",
    description="""
    ROSBAG 템플릿을 생성합니다.
    - name: 템플릿 이름 (고유값, 템플릿 식별)
    - type: 템플릿 타입 (예: robot-arm, robot-leg)
    - description: 템플릿 설명
    - topics: 구독할 ROS 토픽 목록 (쉼표로 구분)
    - metadata_file, db_file: rosbag2 재생에 필요한 파일 첨부
    """
)
async def create_template(
    name: str = Form(...),
    type: str = Form(...),
    description: str = Form(...),
    topics: str = Form(...), 
    metadata_file: UploadFile = File(...),
    db_file: UploadFile = File(...),
    service: TemplateService = Depends(get_template_service)  
):
    template_data = TemplateCreateRequest(name=name, type=type, description=description, topics=topics)
    new_template = await service.create_template_with_files(template_data, metadata_file, db_file)
    return TemplateCreateResponseModel(
        status_code=status.HTTP_201_CREATED,
        data=new_template,
        message=API.CREATE_TEMPLATE.value
    )


# 템플릿 삭제
@router.delete("/{template_id}", response_model=TemplateDeleteResponseModel, status_code=status.HTTP_200_OK)
async def delete_template(
    template_id: int, 
    service: TemplateService = Depends(get_template_service)   
):
    data = await service.delete_template(template_id)
    return TemplateDeleteResponseModel(
        status_code=status.HTTP_200_OK,
        data=data,
        message=API.DELETE_TEMPLATE.value
    )
