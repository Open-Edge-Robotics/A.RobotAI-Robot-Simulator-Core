from fastapi import APIRouter
from minio import Minio

router = APIRouter(prefix="/rosbag", tags=["Rosbag"])

@router.get("/file")
async def get_minio_bag_files():
    minio_url = "192.168.160.135:30333"
    minio_client = Minio(
        minio_url,
        access_key="minioadmin",
        secret_key="qwe1212qwe1212",
        secure=False,
    )
    bucket_name = "rosbag-data"
    objs = minio_client.list_objects(bucket_name, recursive=True)

    return {"rosbagFiles": [obj.object_name for obj in objs]}