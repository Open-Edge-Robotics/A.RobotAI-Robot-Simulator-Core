import os
from minio import Minio
from storage.minio_client import MinioStorageClient
from settings import settings

# -----------------------------
# 1️⃣ Minio 클라이언트 초기화
# -----------------------------
minio_client = Minio(
    settings.MINIO_URL,
    access_key=settings.MINIO_ACCESS_KEY,
    secret_key=settings.MINIO_SECRET_KEY,
    secure=False,
)

# -----------------------------
# 2️⃣ 싱글톤 MinioStorageClient 생성
# -----------------------------
MinioStorageClient.get_instance(minio_client, settings.MINIO_BUCKET_NAME)

# -----------------------------
# 3️⃣ FastAPI DI용 getter
# -----------------------------
def get_storage_client() -> MinioStorageClient:
    """
    FastAPI에서 Depends로 주입 가능
    """
    return MinioStorageClient.get_instance()
