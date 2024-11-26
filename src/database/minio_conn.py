from minio import Minio

from src.settings import settings

minio_url = settings.MINIO_URL

client = Minio(
    minio_url,
    access_key=settings.MINIO_ACCESS_KEY,
    secret_key=settings.MINIO_SECRET_KEY,
    secure=False,
)

bucket_name = settings.MINIO_BUCKET_NAME
