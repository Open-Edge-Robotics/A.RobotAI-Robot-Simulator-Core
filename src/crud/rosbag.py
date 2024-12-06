import asyncio
import os
import subprocess
from typing import List

from minio import S3Error

from src.database import minio_conn
from src.models.instance import Instance


class RosService:
    def __init__(self, session):
        self.session = session

    async def run_instances(self, instances: List[Instance]):
        for instance in instances:
            file_path = await self.get_bag_file_path(instance)
            await asyncio.to_thread(self.run_rosbag, file_path)

    def run_rosbag(self, file_path: str):
        try:
            command = ['ros2', 'bag', 'play', str(file_path)]
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as e:
            raise Exception(f"Error while playing rosbag: {e}")

    async def get_bag_file_path(self, instance: Instance):
        template = instance.template
        file_path = os.path.join("/rosbag-data", template.bag_file_path)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        return await self.download_bag_file(file_path, template)

    async def download_bag_file(self, file_path, template):
        try:
            minio_client = minio_conn.client
            minio_client.fget_object(
                bucket_name=minio_conn.bucket_name,
                object_name=template.bag_file_path,
                file_path=file_path
            )
            return file_path
        except S3Error as e:
            print(f"Error downloading bag file: {e}")
        return None
