from typing import Annotated
from fastapi import Depends

from repositories.template_repository import TemplateRepository
from crud.template import TemplateService
from database.minio_conn import get_storage_client
from storage.minio_client import MinioStorageClient
from database.db_conn import AsyncSession, async_sessionmaker, get_async_sessionmaker

def get_template_repository(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_async_sessionmaker)]
) -> TemplateRepository:
    """AsyncSession Factory 기반 안전한 Repository 생성"""
    return TemplateRepository(session_factory)


def get_template_service(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_async_sessionmaker)],
    storage_client: Annotated[MinioStorageClient, Depends(get_storage_client)]
):
    return TemplateService(session_factory, storage_client)