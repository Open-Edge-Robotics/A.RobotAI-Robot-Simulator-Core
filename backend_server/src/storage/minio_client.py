import os
from .client import StorageClient
from minio import Minio

class MinioStorageClient(StorageClient):
    _instance = None  # 싱글톤 인스턴스
    
    def __init__(self, minio_client, bucket_name):
        if MinioStorageClient._instance is not None:
            raise Exception("This class is a singleton! Use get_instance()")
        
        self.client = minio_client
        self.bucket = bucket_name

        if not self.client.bucket_exists(bucket_name):
            self.client.make_bucket(bucket_name)

        MinioStorageClient._instance = self
        
    @classmethod
    def get_instance(cls, minio_client: Minio = None, bucket_name: str = None):
        if cls._instance is None:
            if minio_client is None or bucket_name is None:
                raise ValueError("minio_client and bucket_name must be provided for first initialization")
            cls(minio_client, bucket_name)
        return cls._instance

    def upload_file(self, local_path: str, remote_path: str):
        self.client.fput_object(self.bucket, remote_path, local_path)

    def create_directory(self, dir_path: str):
        # MinIO는 실제 디렉토리가 없으므로 placeholder 파일 생성 방식으로 처리 가능
        placeholder_path = os.path.join("/tmp", ".placeholder")
        with open(placeholder_path, "w") as f:
            f.write("placeholder")
        self.upload_file(placeholder_path, os.path.join(dir_path, ".placeholder"))
        os.remove(placeholder_path)
