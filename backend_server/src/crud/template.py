from datetime import datetime
import os
import logging
import shutil
from typing import Annotated, Optional, Tuple
from fastapi import Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from exception.template_exceptions import TemplateNotFoundError
from repositories.template_repository import TemplateRepository
from database.db_conn import async_sessionmaker, get_async_sessionmaker
from database.minio_conn import get_storage_client
from storage.minio_client import MinioStorageClient
from storage.client import StorageClient
from utils.rosbag_file_validator import RosbagFileValidator
from models.template import Template
from schemas.template import TemplateFileInfo, TemplateListResponse, TemplateCreateRequest, TemplateCreateResponse, \
    TemplateDeleteResponse, TemplateUpdateRequest, TemplateUpdateResponse
from utils.my_enum import API


logger = logging.getLogger(__name__)

class TemplateService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], storage_client: StorageClient):
        self.session_factory = session_factory
        self.storage_client = storage_client
        self.template_repository = TemplateRepository(session_factory)
    
    def get_template_files_info(self, template: "Template") -> Tuple[TemplateFileInfo, TemplateFileInfo]:
        """
        템플릿 별 metadata.yaml과 db3 파일 정보를 조회하고 presigned URL 생성
        """
        file_names = self.storage_client.list_files(template.bag_file_path)

        metadata_file_name = next((f for f in file_names if f.endswith(("metadata.yaml", "metadata.yml"))), None)
        db_file_name = next((f for f in file_names if f.endswith(".db3")), None)

        if not metadata_file_name or not db_file_name:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"템플릿 {template.template_id}에 필요한 파일 누락"
            )

        metadata_file_info = TemplateFileInfo(
            file_name=metadata_file_name,
            download_url=self.storage_client.get_presigned_url(
                f"{template.bag_file_path}/{metadata_file_name}"
            )
        )

        db_file_info = TemplateFileInfo(
            file_name=db_file_name,
            download_url=self.storage_client.get_presigned_url(
                f"{template.bag_file_path}/{db_file_name}"
            )
        )

        return metadata_file_info, db_file_info

    async def get_all_templates(self):
        templates = await self.template_repository.find_all()
        
        template_list = []
        for template in templates:
            metadata_file_info, db_file_info = self.get_template_files_info(template)
            
            template_list.append(
                TemplateListResponse(
                    template_id=template.template_id,
                    template_name=template.name,
                    template_type=template.type,
                    template_description=template.description,
                    topics=template.topics,
                    created_at=str(template.created_at),
                    metadata_file=metadata_file_info,
                    db_file=db_file_info
                ).model_dump()
            )

        return template_list
    
    async def create_template_with_files(
        self,
        template_data: TemplateCreateRequest,
        metadata_file: UploadFile,
        db_file: UploadFile
    ) -> TemplateCreateResponse:
        # 1. 중복 템플릿 이름 확인
        existing_template = await self.template_repository.find_by_name(template_data.name)
        if existing_template:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'템플릿 이름 "{template_data.name}" 은(는) 이미 존재합니다.'
            )

        # 2. 로컬 임시 디렉토리 생성
        dir_name = f"{template_data.type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        local_dir = os.path.join("/tmp", dir_name)
        os.makedirs(local_dir, exist_ok=True)

        try:
            # 3. 임시 파일 저장
            metadata_path = os.path.join(local_dir, metadata_file.filename)
            db_path = os.path.join(local_dir, db_file.filename)
            for f, path in [(metadata_file, metadata_path), (db_file, db_path)]:
                with open(path, "wb") as buffer:
                    shutil.copyfileobj(f.file, buffer)

            # 4. 파일 검증
            RosbagFileValidator.validate_files([metadata_path, db_path])

            # 5. MinIO 업로드 (디렉토리 단위)
            self.storage_client.create_directory(dir_name)
            self.storage_client.upload_file(metadata_path, f"{dir_name}/{metadata_file.filename}")
            self.storage_client.upload_file(db_path, f"{dir_name}/{db_file.filename}")

        except Exception as e:
            # 파일 업로드 단계에서 실패 → DB 작업 시작도 안 했으니 여기서 바로 종료
            shutil.rmtree(local_dir, ignore_errors=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"파일 업로드 실패: {e}"
            )

        # 6. DB 저장 시도
        try:
            bag_file_path_for_db = dir_name  # 디렉토리명만 저장
            template_data.bag_file_path = bag_file_path_for_db
            new_template = await self.template_repository.create_template(template_data)

        except Exception as e:
            # DB 저장 실패 → 업로드한 파일 보상 삭제
            try:
                self.storage_client.delete_file(f"{dir_name}/{metadata_file.filename}")
                self.storage_client.delete_file(f"{dir_name}/{db_file.filename}")
                self.storage_client.delete_directory(dir_name)
            except Exception as cleanup_err:
                # 보상 실패는 로그만 남김
                logger.error(f"템플릿 DB 저장 실패 후 파일 삭제 실패: {cleanup_err}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"템플릿 생성 실패: {e}"
            )
        finally:
            # 항상 로컬 파일은 제거
            shutil.rmtree(local_dir, ignore_errors=True)

        # 7. 응답 생성
        metadata_file_info = TemplateFileInfo(
            file_name=metadata_file.filename,
            download_url=self.storage_client.get_presigned_url(f"{dir_name}/{metadata_file.filename}")
        ).model_dump()
        db_file_info = TemplateFileInfo(
            file_name=db_file.filename,
            download_url=self.storage_client.get_presigned_url(f"{dir_name}/{db_file.filename}")
        ).model_dump()
        
        template_response = TemplateCreateResponse(
            template_id=new_template.template_id,
            template_name=new_template.name,
            template_type=new_template.type,
            template_description=new_template.description,
            topics=new_template.topics,
            created_at=str(new_template.created_at),
            metadata_file=metadata_file_info,
            db_file=db_file_info
        ).model_dump()

        return template_response

    async def update_template(
        self,
        template_id: int,
        template_data: TemplateUpdateRequest,
        metadata_file: Optional[UploadFile],
        db_file: Optional[UploadFile]
    ) -> TemplateUpdateResponse:
        temp_dir_name = f"update_{template_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        local_dir = os.path.join("/tmp", temp_dir_name)
        os.makedirs(local_dir, exist_ok=True)

        metadata_path = os.path.join(local_dir, metadata_file.filename) if metadata_file else None
        db_path = os.path.join(local_dir, db_file.filename) if db_file else None

        try:
            # 파일 저장 및 검증
            upload_files = []
            for file, path in [(metadata_file, metadata_path), (db_file, db_path)]:
                if file:
                    with open(path, "wb") as buffer:
                        shutil.copyfileobj(file.file, buffer)
                    upload_files.append(path)
                
            if upload_files:
                RosbagFileValidator.validate_files(upload_files)

            # DB 조회
            async with self.session_factory() as session:
                existing_template = await self.template_repository.find_by_id(template_id, session)
                if not existing_template:
                    raise HTTPException(status_code=400, detail=f"템플릿을 찾을 수 없습니다. ID: {template_id}")

                if template_data.name and template_data.name == existing_template.name:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f'템플릿 이름 "{template_data.name}" 은(는) 이미 존재합니다.'
                    )

            # 기존 파일 삭제 및 업로드
            if upload_files:
                existing_files = self.storage_client.list_files(existing_template.bag_file_path)
                for file_name in existing_files:
                    self.storage_client.delete_file(f"{existing_template.bag_file_path}/{file_name}")


            if metadata_file:
                self.storage_client.upload_file(metadata_path, f"{existing_template.bag_file_path}/{metadata_file.filename}")
            if db_file:
                self.storage_client.upload_file(db_path, f"{existing_template.bag_file_path}/{db_file.filename}")

            # DB 업데이트
            async with self.session_factory() as session:
                updated_template = await self.template_repository.update_template(
                    template_id, template_data, session
                )
                await session.commit()
                await session.refresh(updated_template)

            # 응답 생성
            metadata_file_info, db_file_info = self.get_template_files_info(updated_template)

            return TemplateUpdateResponse(
                template_id=updated_template.template_id,
                template_name=updated_template.name,
                template_type=updated_template.type,
                template_description=updated_template.description,
                topics=updated_template.topics,
                created_at=str(updated_template.created_at),
                metadata_file=metadata_file_info or updated_template.metadata_file,
                db_file=db_file_info or updated_template.db_file
            ).model_dump()

        finally:
            # 임시 파일 정리
            if os.path.exists(local_dir):
                shutil.rmtree(local_dir)


    async def delete_template(self, template_id: int):
        # ----------------------------
        # 1. DB에서 템플릿 조회
        # ----------------------------
        template = await self.find_template_by_id(template_id)    
        bag_path = template.bag_file_path

        # ----------------------------
        # 2. DB 삭제
        # ----------------------------
        async with self.session_factory() as session:
            template.mark_as_deleted()  # deleted_at 필드 업데이트
            session.add(template)       # 변경 반영
            await session.commit()
            logger.info(f"DB 템플릿 삭제 완료: {template_id}")

        # ----------------------------
        # 3. 응답 반환
        # ----------------------------
        return TemplateDeleteResponse(template_id=template_id).model_dump()

    async def find_template_by_id(self, template_id: int):
        template = await self.template_repository.find_by_id(template_id)

        if template is None:
            raise HTTPException(status_code=400, detail=f"템플릿을 찾을 수 없습니다. ID: {template_id}")
        return template

# FastAPI DI 제공
def get_template_service(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_async_sessionmaker)],
    storage_client: Annotated[MinioStorageClient, Depends(get_storage_client)]
):
    return TemplateService(session_factory, storage_client)