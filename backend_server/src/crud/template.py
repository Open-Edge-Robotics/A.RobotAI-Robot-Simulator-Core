from datetime import datetime
import os
import shutil
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from storage.client import StorageClient
from utils.rosbag_file_validator import RosbagFileValidator
from models.template import Template
from schemas.template import TemplateListResponse, TemplateCreateRequest, TemplateCreateResponse, \
    TemplateDeleteResponse
from utils.my_enum import API


class TemplateService:
    def __init__(self, db: AsyncSession, storage_client: StorageClient):
        self.db = db
        self.storage_client = storage_client

    async def get_all_templates(self):
        statement = select(Template).order_by(Template.template_id.desc())
        selected_template = await self.db.execute(statement)
        templates = selected_template.scalars().all()

        return [
            TemplateListResponse(
                template_id=template.template_id,
                template_type=template.type,
                template_description=template.description,
                topics=template.topics,
                created_at=str(template.created_at)
            ) for template in templates
        ]

    async def create_template(self, template: TemplateCreateRequest):
        new_template = Template(
            type=template.type,
            description=template.description,
            bag_file_path=template.bag_file_path,
            topics=template.topics,
        )
        self.db.add(new_template)
        await self.db.commit()
        await self.db.refresh(new_template)

        return TemplateCreateResponse.model_validate(new_template).model_dump()
    
    async def create_template_with_files(self, template_data: TemplateCreateRequest, metadata_file: UploadFile, db_file: UploadFile) -> TemplateCreateResponse:
        # 1. 로컬 임시 디렉토리 생성
        dir_name = f"{template_data.type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        local_dir = os.path.join("/tmp", dir_name)
        os.makedirs(local_dir, exist_ok=True)

        # 2. 임시 파일 저장
        metadata_path = os.path.join(local_dir, metadata_file.filename)
        db_path = os.path.join(local_dir, db_file.filename)
        for f, path in [(metadata_file, metadata_path), (db_file, db_path)]:
            with open(path, "wb") as buffer:
                shutil.copyfileobj(f.file, buffer)

        # 3. 파일 검증
        RosbagFileValidator.validate_files([metadata_path, db_path])

        # 4. MinIO 업로드 (디렉토리 단위)
        self.storage_client.create_directory(dir_name)
        self.storage_client.upload_file(metadata_path, f"{dir_name}/{metadata_file.filename}")
        self.storage_client.upload_file(db_path, f"{dir_name}/{db_file.filename}")

        # 5. DB 저장
        # Pod에서 접근할 때는 /rosbag-data/bagfiles/<directory> 구조로 사용
        bag_file_path_for_db = dir_name  # 디렉토리명만 저장
        new_template = Template(
            type=template_data.type,
            description=template_data.description,
            bag_file_path=bag_file_path_for_db,
            topics=template_data.topics,
        )
        self.db.add(new_template)
        await self.db.commit()
        await self.db.refresh(new_template)

        # 6. 로컬 임시 파일 삭제
        shutil.rmtree(local_dir)
        
        template_response = TemplateCreateResponse.model_validate(new_template).model_dump()
        return template_response
    

    async def delete_template(self, template_id: int):
        find_template = await self.find_template_by_id(template_id, API.DELETE_TEMPLATE.value)

        await self.db.delete(find_template)
        await self.db.commit()
        return TemplateDeleteResponse(
            template_id=find_template.template_id
        ).model_dump()

    async def find_template_by_id(self, template_id: int, api: str):
        query = select(Template).where(Template.template_id == template_id)
        result = await self.db.execute(query)
        template = result.scalar_one_or_none()

        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'{api}: 존재하지 않는 템플릿id 입니다.')
        return template
