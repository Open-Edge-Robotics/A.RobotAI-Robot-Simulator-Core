import os

from minio import Minio

minio_url = os.environ['MINIO_URL']

client = Minio(
    minio_url,
    access_key=os.environ['MINIO_ACCESS_KEY'],
    secret_key=os.environ['MINIO_SECRET_KEY'],
    secure=False,
)

bucket_name = os.environ['MINIO_BUCKET_NAME']
