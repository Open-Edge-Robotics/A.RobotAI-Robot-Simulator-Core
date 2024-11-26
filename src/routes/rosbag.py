from fastapi import APIRouter

from src.database import minio_conn

router = APIRouter(prefix="/rosbag", tags=["Rosbag"])


@router.get("/")
def get_minio_bag_files():
    client = minio_conn.client
    bucket_name = minio_conn.bucket_name

    objs = client.list_objects(bucket_name, recursive=True)

    return {"rosbagFiles": [obj.object_name for obj in objs]}
