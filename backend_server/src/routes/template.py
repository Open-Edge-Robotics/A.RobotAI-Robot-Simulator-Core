from fastapi import APIRouter, File, Form, UploadFile
from fastapi.params import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from storage.minio_client import MinioStorageClient
from crud.template import TemplateService
from database.db_conn import get_db
from database.minio_conn import client, bucket_name
from schemas.template import TemplateCreateRequest, TemplateCreateResponseModel, \
    TemplateListResponseModel, TemplateDeleteResponseModel
from utils.my_enum import API

router = APIRouter(prefix="/template", tags=["Template"])

# MinIO 클라이언트 주입
storage_client = MinioStorageClient(client, bucket_name)

# 템플릿 목록 조회
@router.get("", response_model=TemplateListResponseModel, status_code=status.HTTP_200_OK)
async def get_templates(db: AsyncSession = Depends(get_db)):
    template_responses = await TemplateService(db, storage_client).get_all_templates()
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

    - type: 템플릿 타입 (예: robot-arm, robot-leg)
    - description: 템플릿 설명
    - topics: 구독할 ROS 토픽 목록 (쉼표로 구분)
    - metadata_file, db_file: rosbag2 재생에 필요한 파일 첨부
    """
)
async def create_template(
    type: str = Form(...),
    description: str = Form(...),
    topics: str = Form(...), 
    metadata_file: UploadFile = File(...),
    db_file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    template_data = TemplateCreateRequest(type=type, description=description, topics=topics)
    new_template = await TemplateService(db, storage_client).create_template_with_files(template_data, metadata_file, db_file)
    return TemplateCreateResponseModel(
        status_code=status.HTTP_201_CREATED,
        data=new_template,
        message=API.CREATE_TEMPLATE.value
    )


# 템플릿 삭제
@router.delete("/{template_id}", response_model=TemplateDeleteResponseModel, status_code=status.HTTP_200_OK)
async def delete_template(template_id: int, db: AsyncSession = Depends(get_db)):
    data = await TemplateService(db, storage_client).delete_template(template_id)
    return TemplateDeleteResponseModel(
        status_code=status.HTTP_200_OK,
        data=data,
        message=API.DELETE_TEMPLATE.value
    )
