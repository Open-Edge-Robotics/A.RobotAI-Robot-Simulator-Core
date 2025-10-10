from typing import Optional
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.params import Depends
from starlette import status

from di.template import TemplateService, get_template_service
from schemas.template import TemplateCreateRequest, TemplateCreateResponseModel, \
    TemplateListResponseModel, TemplateDeleteResponseModel, TemplateUpdateRequest, TemplateUpdateResponseModel
from utils.my_enum import API

router = APIRouter(prefix="/template", tags=["Template"])

# 템플릿 목록 조회
@router.get(
    "",
    response_model=TemplateListResponseModel,
    status_code=status.HTTP_200_OK,
    summary="템플릿 목록 조회",
    description="""
    시스템에 등록된 모든 템플릿 목록을 조회합니다.

    반환 정보:
        - 템플릿 이름(name)
        - 템플릿 타입(type)
        - 설명(description)
        - 첨부된 파일 정보(metadata.yaml, .db3 파일, 다운로드 가능한 링크)
    UI나 API 클라이언트에서 템플릿 선택 시 활용 가능합니다.
    """
)
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
    
# 템플릿 수정
@router.patch(
    "/{template_id}", 
    response_model=TemplateUpdateResponseModel,
    summary="기존 ROSBAG 템플릿 수정",
    description="""
    기존 ROSBAG 템플릿을 수정합니다.
        - name: 템플릿 이름 (고유값, 템플릿 식별)
        - type: 템플릿 타입 (예: robot-arm, robot-leg)
        - description: 템플릿 설명
        - topics: 구독할 ROS 토픽 목록 (쉼표로 구분)
        - metadata_file, db_file: rosbag2 재생에 필요한 파일 첨부
    """
)
async def update_template(
    template_id: int,
    name: Optional[str] = Form(None),
    type: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    topics: Optional[str] = Form(None), 
    metadata_file: Optional[UploadFile] = File(None),
    db_file: Optional[UploadFile] = File(None),
    service: TemplateService = Depends(get_template_service)  
):
    # 최소 한 필드 체크
    if not any([name, type, description, topics, metadata_file, db_file]):
        raise HTTPException(status_code=400, detail="최소 하나의 필드 또는 파일을 입력해야 합니다.")
    
    template_data = TemplateUpdateRequest(name=name, type=type, description=description, topics=topics)
    updated_template = await service.update_template(template_id, template_data, metadata_file, db_file)
    return TemplateCreateResponseModel(
        status_code=status.HTTP_200_OK,
        data=updated_template,
        message=API.UPDATE_TEMPLATE.value
    )


# 템플릿 삭제
@router.delete(
    "/{template_id}",
    response_model=TemplateDeleteResponseModel,
    status_code=status.HTTP_200_OK,
    summary="템플릿 삭제",
    description="""
        특정 템플릿(template_id)을 삭제합니다.  

        주의 사항:
        - 소프트 딜리트(Soft Delete) 방식으로 처리됩니다.
        - 반환값: 삭제 처리 결과(TemplateDeleteResponseModel)
    """
)
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
